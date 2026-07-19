from datetime import date

import pandas as pd

from config.settings import settings
from data.ingestion.yfinance_client import YFinanceClient
from factors.data_loader import SECTOR_ETF_MAP

# Real S&P 500 peak-to-trough dates for each named drawdown (verified via web
# search against cited market history, not estimated): 2007-10-09 (1565.15)
# to 2009-03-06 (666), -56.8%; 2020-02-19 (3386.15) to 2020-03-23 (2237.40),
# -33.9%; 2022-01-03 (4796.56) to 2022-10-12 (3577.03), -25.4%. Used as the
# replay window for every ticker, not just the index itself.
HISTORICAL_SCENARIOS: dict[str, dict[str, str]] = {
    "2008_financial_crisis": {"start": "2007-10-09", "end": "2009-03-06"},
    "2020_covid_crash": {"start": "2020-02-19", "end": "2020-03-23"},
    "2022_rate_hike_selloff": {"start": "2022-01-03", "end": "2022-10-12"},
}

STRESS_SCENARIO_CACHE_DIR = settings.raw_data_dir / "stress_scenarios"


def _default_fetch(tickers: list[str], start: date, end: date) -> pd.DataFrame:
    return YFinanceClient().get_prices(tickers, start, end)


def fetch_historical_scenario_prices(
    tickers: list[str], scenario_name: str, fetch_fn=_default_fetch
) -> dict[str, pd.Series]:
    """Real historical Close prices for `tickers` over a named scenario's
    date window, cached to disk per-ticker (data/raw/stress_scenarios/{scenario}/
    {ticker}.csv) since a historical crash window's prices never change once
    fetched — same "fetch once, cache forever" convention as
    ai_analysis.filing_sections.get_filing_text. fetch_fn is injectable
    (defaults to a real yfinance pull) so callers/tests can avoid the network.

    Returns {ticker: close_price_series}; a ticker with no trading history in
    the window (e.g. it IPO'd after the crash) is simply absent from the
    result — callers should fall back to a proxy (see replay_scenario).
    Per-ticker Series rather than one aligned wide DataFrame deliberately:
    only each ticker's own first/last price is needed, so there's no reason
    to force every ticker onto a shared trading-day index.
    """
    if scenario_name not in HISTORICAL_SCENARIOS:
        raise ValueError(f"Unknown scenario: {scenario_name!r} — expected one of {list(HISTORICAL_SCENARIOS)}")
    window = HISTORICAL_SCENARIOS[scenario_name]
    cache_dir = STRESS_SCENARIO_CACHE_DIR / scenario_name
    cache_dir.mkdir(parents=True, exist_ok=True)

    result: dict[str, pd.Series] = {}
    missing = []
    for ticker in tickers:
        cache_file = cache_dir / f"{ticker}.csv"
        if cache_file.exists():
            result[ticker] = pd.read_csv(cache_file, index_col=0)["close"]
        else:
            missing.append(ticker)

    if missing:
        start = date.fromisoformat(window["start"])
        end = date.fromisoformat(window["end"])
        fetched = fetch_fn(missing, start, end)
        for ticker in missing:
            if ticker not in fetched.columns:
                continue
            series = fetched[ticker].dropna()
            if series.empty:
                continue
            series.rename("close").to_frame().to_csv(cache_dir / f"{ticker}.csv")
            result[ticker] = series

    return result


def replay_scenario(
    weights: pd.Series, sector_map: pd.Series, scenario_name: str, fetch_fn=_default_fetch
) -> dict:
    """Computes what the CURRENT portfolio weights would have returned had
    this historical drawdown window replayed today — a real historical
    replay, not a hand-typed shock table. Tickers without trading history
    that far back fall back to their GICS sector ETF's window return
    (factors.data_loader.SECTOR_ETF_MAP; sector ETFs all date to 1998).
    Reports coverage explicitly rather than silently assuming full coverage.
    """
    held = weights[weights != 0].index.tolist()
    if not held:
        return {
            "scenario": scenario_name,
            "window": HISTORICAL_SCENARIOS[scenario_name],
            "portfolio_return": 0.0,
            "coverage": 0.0,
            "tickers_missing_data": [],
        }

    prices = fetch_historical_scenario_prices(held, scenario_name, fetch_fn=fetch_fn)
    window_returns: dict[str, float] = {
        ticker: float(series.iloc[-1] / series.iloc[0] - 1) for ticker, series in prices.items() if len(series) >= 2
    }

    still_missing = [t for t in held if t not in window_returns]
    if still_missing:
        missing_sectors = sector_map.reindex(still_missing).dropna().unique().tolist()
        sector_etfs = [SECTOR_ETF_MAP[s] for s in missing_sectors if s in SECTOR_ETF_MAP]
        if sector_etfs:
            sector_prices = fetch_historical_scenario_prices(sector_etfs, scenario_name, fetch_fn=fetch_fn)
            sector_window_returns = {
                etf: float(series.iloc[-1] / series.iloc[0] - 1)
                for etf, series in sector_prices.items()
                if len(series) >= 2
            }
            for ticker in still_missing:
                sector = sector_map.get(ticker)
                etf = SECTOR_ETF_MAP.get(sector)
                if etf in sector_window_returns:
                    window_returns[ticker] = sector_window_returns[etf]

    covered_tickers = [t for t in held if t in window_returns]
    tickers_missing_data = [t for t in held if t not in window_returns]

    portfolio_return = float(sum(weights[t] * window_returns[t] for t in covered_tickers))
    total_gross = float(weights[held].abs().sum())
    coverage = float(weights[covered_tickers].abs().sum() / total_gross) if total_gross > 0 else 0.0

    return {
        "scenario": scenario_name,
        "window": HISTORICAL_SCENARIOS[scenario_name],
        "portfolio_return": portfolio_return,
        "coverage": coverage,
        "tickers_missing_data": tickers_missing_data,
    }


def run_all_scenarios(weights: pd.Series, sector_map: pd.Series, fetch_fn=_default_fetch) -> dict[str, dict]:
    return {
        scenario_name: replay_scenario(weights, sector_map, scenario_name, fetch_fn=fetch_fn)
        for scenario_name in HISTORICAL_SCENARIOS
    }


# --- hypothetical (non-historical) shock scenarios -------------------------------
# Kept alongside the real historical replay above for ad hoc "what if sector X
# drops N%" scenarios that aren't tied to an actual historical window.


def apply_shock(weights: pd.Series, shocks: pd.Series) -> float:
    common = weights.index.intersection(shocks.index)
    return float((weights[common] * shocks[common]).sum())


def run_scenario(weights: pd.Series, scenario_name: str, shocks_by_ticker: pd.Series) -> float:
    # scenario_name is just a caller-chosen label for a hypothetical, not
    # validated against HISTORICAL_SCENARIOS (that dict holds real replay
    # windows for replay_scenario()/run_all_scenarios() above, a different
    # concept from an arbitrary hypothetical shock vector).
    return apply_shock(weights, shocks_by_ticker)
