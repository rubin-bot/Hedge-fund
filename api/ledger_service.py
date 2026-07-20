"""Risk and performance analysis over the user's ACTUAL open positions
(the virtual ledger) -- as opposed to api/engine_service.py, which
analyzes the model's hypothetical MVO target book. Reuses
risk/decomposition.py, risk/correlation_monitor.py, risk/stress_testing.py,
risk/risk_management.py, and risk/circuit_breaker.py (via
simulation.virtual_ledger.circuit_breaker_status, which is already built
on top of it) exactly as they're used in risk/gate.py -- only the weights
fed into them differ (derived from real positions here, not a proposed
rebalance). Position/sector limit checks and stress testing are reused
directly; beta-neutrality and turnover checks are skipped since neither
concept applies to a manually-picked, non-rebalanced book.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

import simulation.virtual_ledger as vl
from api.engine_service import _num
from config.settings import load_portfolio_config, load_risk_config
from data.db import connection
from factors.data_loader import SECTOR_ETF_MAP, load_close_on_or_before, load_prices_wide, load_sector_etf_prices, load_sector_map
from portfolio.risk_models import estimate_beta, estimate_covariance_matrix
from risk.correlation_monitor import monitor_correlations
from risk.decomposition import decompose_portfolio_risk, estimate_sector_factor_loadings
from risk.risk_management import check_position_limits, check_sector_limits
from risk.stress_testing import run_all_scenarios

PRICE_HISTORY_START = "2025-01-01"


def _position_weights(conn) -> pd.Series:
    """Signed weight-of-total-account-value per ticker, from OPEN positions
    only. Recomputes unrealized P&L live (via load_close_on_or_before)
    rather than reusing the last EOD snapshot, so the Risk Controls view
    reflects the latest available close even between EOD runs. Multiple
    open positions on the same ticker (only possible via an opposite-side
    "flip") net together into one signed exposure.
    """
    open_rows = vl.list_positions(conn, status="open")
    if not open_rows:
        return pd.Series(dtype=float)

    tickers = sorted({row["ticker"] for row in open_rows})
    latest_prices = load_close_on_or_before(tickers, date.today().isoformat())

    unrealized_total = 0.0
    exposure: dict[str, float] = {}
    for row in open_rows:
        price = latest_prices.get(row["ticker"])
        if price is not None and not pd.isna(price):
            unrealized_total += (
                row["shares"] * (price - row["entry_price"])
                if row["side"] == "long"
                else row["shares"] * (row["entry_price"] - price)
            )
        signed_exposure = row["cost_basis"] if row["side"] == "long" else -row["cost_basis"]
        exposure[row["ticker"]] = exposure.get(row["ticker"], 0.0) + signed_exposure

    balance = vl.get_balance(conn)
    total_account_value = balance["free_cash"] + balance["cash_tied_up"] + unrealized_total
    if total_account_value <= 0:
        return pd.Series(dtype=float)
    return pd.Series(exposure) / total_account_value


def build_risk_response() -> dict:
    with connection() as conn:
        weights = _position_weights(conn)
        circuit_breaker = vl.circuit_breaker_status(conn)

    as_of = date.today().isoformat()
    if weights.empty:
        return {
            "as_of": as_of,
            "has_open_positions": False,
            "circuit_breaker": circuit_breaker,
            "decomposition": {
                "factor_pct": None, "specific_pct": None, "flagged": False,
                "target_specific_pct": None, "tolerance": None, "reason": "No open positions to analyze.",
            },
            "correlation_flags": [], "stress_tests": [],
            "position_limit_breaches": {}, "sector_limit_breaches": {}, "warnings": [],
        }

    tickers = list(weights.index)
    portfolio_cfg = load_portfolio_config()
    risk_cfg = load_risk_config()
    benchmark = portfolio_cfg.get("benchmark_ticker", "SPY")
    max_position_weight = portfolio_cfg.get("max_position_weight", 0.03)
    max_sector_weight = portfolio_cfg.get("max_sector_weight", 0.20)
    correlation_threshold = risk_cfg.get("correlation_threshold", 0.85)
    specific_risk_target = risk_cfg.get("specific_risk_target_pct", 0.80)
    specific_risk_tolerance = risk_cfg.get("specific_risk_tolerance", 0.10)

    sector_map = load_sector_map(tickers)
    prices = load_prices_wide(tickers + [benchmark], start_date=PRICE_HISTORY_START)
    sector_etf_prices = load_sector_etf_prices(start_date=PRICE_HISTORY_START)
    returns = prices[tickers].pct_change().dropna(how="all")

    warnings: list[str] = []

    position_breaches = check_position_limits(weights, max_position_weight)
    if not position_breaches.empty:
        warnings.append(f"position limit breached: {position_breaches.to_dict()}")

    sector_breaches = check_sector_limits(weights, sector_map, max_sector_weight)
    if not sector_breaches.empty:
        warnings.append(f"sector limit breached: {sector_breaches.to_dict()}")

    correlated_pairs = monitor_correlations(weights, returns, correlation_threshold)
    if correlated_pairs:
        warnings.append(f"{len(correlated_pairs)} position pair(s) above {correlation_threshold} correlation")

    sector_etf_returns = sector_etf_prices.pct_change().dropna(how="all")
    etf_to_sector = {etf: sector for sector, etf in SECTOR_ETF_MAP.items()}
    sector_returns_by_name = sector_etf_returns.rename(columns=etf_to_sector)
    beta_to_sector, specific_variance = estimate_sector_factor_loadings(returns, sector_returns_by_name, sector_map)
    sector_factor_covariance = estimate_covariance_matrix(sector_returns_by_name)
    decomposition_result = decompose_portfolio_risk(
        weights, beta_to_sector, specific_variance, sector_map, sector_factor_covariance,
        specific_target_pct=specific_risk_target, tolerance=specific_risk_tolerance,
    )
    if decomposition_result.get("flagged"):
        warnings.append(f"risk decomposition flagged: {decomposition_result['reason']}")

    stress_tests_raw = run_all_scenarios(weights, sector_map)

    return {
        "as_of": as_of,
        "has_open_positions": True,
        "circuit_breaker": circuit_breaker,
        "decomposition": {
            "factor_pct": _num(decomposition_result.get("factor_pct")),
            "specific_pct": _num(decomposition_result.get("specific_pct")),
            "flagged": bool(decomposition_result["flagged"]),
            "target_specific_pct": _num(decomposition_result.get("target_specific_pct")),
            "tolerance": _num(decomposition_result.get("tolerance")),
            "reason": decomposition_result["reason"],
        },
        "correlation_flags": [
            {"ticker_a": pair["pair"][0], "ticker_b": pair["pair"][1], "correlation": _num(pair["correlation"]) or 0.0}
            for pair in correlated_pairs
        ],
        "stress_tests": [
            {
                "scenario": result["scenario"], "window_start": result["window"]["start"],
                "window_end": result["window"]["end"], "portfolio_return": _num(result["portfolio_return"]) or 0.0,
                "coverage": _num(result["coverage"]) or 0.0, "tickers_missing_data": result["tickers_missing_data"],
            }
            for result in stress_tests_raw.values()
        ],
        "position_limit_breaches": {k: _num(v) or 0.0 for k, v in position_breaches.to_dict().items()},
        "sector_limit_breaches": {k: _num(v) or 0.0 for k, v in sector_breaches.to_dict().items()},
        "warnings": warnings,
    }


# --- Performance / P&L attribution -----------------------------------------


def _empty_performance() -> dict:
    return {"has_data": False}


def get_performance() -> dict:
    """Single-period CAPM + Brinson-style attribution, same methodology as
    last session's version (see api/engine_service.py's docstring for the
    full derivation of each term) but re-pointed at account_daily_snapshots
    and positions instead of the old paper_portfolio_snapshots table.

    total_return/beta/alpha are computed over investment P&L
    (unrealized_pnl + realized_pnl_cumulative), NOT total_account_value --
    total_account_value includes free_cash, so a deposit would otherwise
    register as a fake positive "return" (same reasoning as
    virtual_ledger.circuit_breaker_status). starting_equity/ending_equity
    in the response ARE total_account_value-based, since those are for
    display ("what's my account worth") rather than a performance metric.
    """
    with connection() as conn:
        snapshots = vl.get_account_snapshots(conn)
        all_positions = vl.list_positions(conn)

    if len(snapshots) < 2:
        return _empty_performance()

    df = pd.DataFrame(snapshots).set_index("snapshot_date")
    investment_value = (df["unrealized_pnl"] + df["realized_pnl_cumulative"]).astype(float)
    start_date, end_date = df.index[0], df.index[-1]
    starting_equity = float(df["total_account_value"].iloc[0])
    ending_equity = float(df["total_account_value"].iloc[-1])

    portfolio_returns = investment_value.pct_change().replace([float("inf"), float("-inf")], 0.0).dropna()
    # Compounding the daily investment-return series (not a simple
    # start/end ratio on investment_value, which breaks if it ever crosses
    # zero) gives the deposit-neutral total return over the window.
    total_return = float((1.0 + portfolio_returns).prod() - 1.0) if len(portfolio_returns) else None

    portfolio_cfg = load_portfolio_config()
    benchmark = portfolio_cfg.get("benchmark_ticker", "SPY")
    bench_prices = load_prices_wide([benchmark], start_date=start_date)
    beta = None
    benchmark_period_return = None
    attribution = None

    if benchmark in bench_prices.columns and len(portfolio_returns) >= 2:
        bench_close = bench_prices[benchmark].reindex(df.index).ffill()
        bench_returns = bench_close.pct_change().dropna()
        if bench_close.iloc[0]:
            benchmark_period_return = float(bench_close.iloc[-1] / bench_close.iloc[0] - 1.0)

        aligned = pd.concat(
            [portfolio_returns.rename("portfolio"), bench_returns.rename("benchmark")], axis=1
        ).dropna()
        if len(aligned) >= 2:
            beta_series = estimate_beta(aligned[["portfolio"]], aligned["benchmark"])
            if "portfolio" in beta_series.index:
                beta = float(beta_series["portfolio"])

        if beta is not None and benchmark_period_return is not None and total_return is not None:
            beta_contribution = beta * benchmark_period_return
            alpha = total_return - beta_contribution
            sector_contribution = _reconstruct_sector_contribution(df, all_positions, bench_returns)
            factor_contribution = alpha - sum(sector_contribution.values())
            attribution = {
                "beta_contribution": beta_contribution, "sector_contribution": sector_contribution,
                "factor_contribution": factor_contribution, "alpha": alpha,
            }

    monthly_index = pd.to_datetime(df.index)
    monthly_series = pd.Series(df["total_account_value"].values, index=monthly_index)
    monthly_returns_series = monthly_series.resample("ME").last().pct_change().dropna()
    monthly_returns = {ts.strftime("%Y-%m"): float(v) for ts, v in monthly_returns_series.items()}

    return {
        "has_data": True,
        "start_date": start_date, "end_date": end_date,
        "starting_equity": starting_equity, "ending_equity": ending_equity,
        "total_return": total_return,
        "equity_curve": [{"date": d, "equity": float(v)} for d, v in df["total_account_value"].items()],
        "beta": beta, "benchmark_ticker": benchmark, "benchmark_return": benchmark_period_return,
        "attribution": attribution, "monthly_returns": monthly_returns,
    }


def _reconstruct_sector_contribution(
    snapshots_df: pd.DataFrame, positions: list[dict], bench_returns: pd.Series
) -> dict[str, float]:
    """Daily Brinson-style sector-tilt contribution: for each day in the
    snapshot window, sum over every position that was actually open that
    day of (that position's dollar value as a fraction of that day's
    total_account_value) * (its sector ETF's one-day return MINUS the
    benchmark's one-day return). Reconstructed from the `positions` table
    (entry_date/exit_date determine which days a position was open) rather
    than a stored daily positions blob -- the equivalent of what last
    session's version did against paper_portfolio_snapshots.positions_json.
    """
    if not positions:
        return {}

    tickers = sorted({p["ticker"] for p in positions})
    window_start = min(p["entry_date"] for p in positions)
    position_prices = load_prices_wide(tickers, start_date=window_start)
    sector_map = load_sector_map(tickers)
    sector_etf_tickers = sorted(set(SECTOR_ETF_MAP.values()))
    sector_etf_prices = load_prices_wide(sector_etf_tickers, start_date=window_start)

    dates = list(snapshots_df.index)
    sector_contribution: dict[str, float] = {}
    for i in range(1, len(dates)):
        prev_date, cur_date = dates[i - 1], dates[i]
        if cur_date not in bench_returns.index:
            continue
        bench_return_today = float(bench_returns.loc[cur_date])
        prev_account_value = float(snapshots_df.loc[prev_date, "total_account_value"])
        if prev_account_value <= 0:
            continue

        sector_weight_today: dict[str, float] = {}
        for position in positions:
            if position["entry_date"] > prev_date:
                continue
            if position["exit_date"] and position["exit_date"] < prev_date:
                continue
            ticker = position["ticker"]
            if ticker not in position_prices.columns or prev_date not in position_prices.index:
                continue
            price = position_prices.loc[prev_date, ticker]
            if pd.isna(price):
                continue
            sector = sector_map.get(ticker)
            if not sector:
                continue
            # Signed by side: a short position's sector exposure is negative
            # -- it should be hurt by the sector rallying, not credited for it.
            dollar_value = position["shares"] * price * (1 if position["side"] == "long" else -1)
            sector_weight_today[sector] = sector_weight_today.get(sector, 0.0) + dollar_value / prev_account_value

        for sector, weight in sector_weight_today.items():
            etf = SECTOR_ETF_MAP.get(sector)
            if not etf or etf not in sector_etf_prices.columns:
                continue
            if prev_date not in sector_etf_prices.index or cur_date not in sector_etf_prices.index:
                continue
            etf_prev, etf_cur = sector_etf_prices.loc[prev_date, etf], sector_etf_prices.loc[cur_date, etf]
            if pd.isna(etf_prev) or pd.isna(etf_cur) or etf_prev == 0:
                continue
            sector_return_today = float(etf_cur / etf_prev - 1.0)
            sector_contribution[sector] = sector_contribution.get(sector, 0.0) + weight * (
                sector_return_today - bench_return_today
            )

    return sector_contribution
