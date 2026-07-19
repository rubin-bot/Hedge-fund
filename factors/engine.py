from datetime import date, timedelta

import pandas as pd

from config.settings import load_factors_config
from data.ingestion.fred_client import SERIES_BDI_PROXY, SERIES_DXY_PROXY, SERIES_PPI
from factors.congressional import compute_congressional_factor
from factors.crowding import DEFAULT_CROWDING_THRESHOLD, detect_factor_crowding
from factors.data_loader import (
    load_13f_holdings,
    load_analyst_estimates,
    load_congressional_trades,
    load_form4,
    load_fred_series,
    load_fundamentals_history,
    load_prices_wide,
    load_sector_etf_prices,
    load_sector_map,
    load_universe,
)
from factors.estimate_revisions import compute_estimate_revisions
from factors.growth import compute_growth
from factors.insider_activity import compute_insider_activity
from factors.institutional_flow import compute_institutional_flow
from factors.macro_regime import compute_macro_regime
from factors.momentum import compute_momentum
from factors.quality import compute_quality
from factors.scoring import composite_score, rank_scores, sector_neutral_zscore
from factors.value import compute_value

# Weights are configurable per ScoringEngine(weights=...) call; these are just
# the defaults, split roughly evenly across the eight factors with slightly
# less weight on the four that depend on sparser/noisier data sources.
DEFAULT_WEIGHTS = {
    "momentum": 0.15,
    "value": 0.15,
    "quality": 0.15,
    "growth": 0.15,
    "estimate_revisions": 0.10,
    "insider_activity": 0.10,
    "institutional_flow": 0.10,
    "congressional": 0.10,
}

# Split: momentum/value/quality/growth are "core" factors built from prices
# and fundamentals, which the pipeline should always have once backfilled —
# missing data there indicates a real problem and should raise. The other
# four are alternative-data factors that can legitimately have gaps through
# no fault of the pipeline: congressional data is scraped from sources that
# can go quiet (the user's explicit example), estimate_revisions and
# institutional_flow structurally need >=2 historical snapshots (successive
# analyst-estimate pulls / successive 13F quarters) that won't exist yet on a
# freshly-backfilled database, and insider_activity depends on Form 4 filings
# that may simply not have been ingested yet for a given ticker subset. All
# four are marked optional so the engine degrades gracefully rather than
# hard-failing whenever one alt-data source is thin.
OPTIONAL_FACTORS = {"congressional", "estimate_revisions", "institutional_flow", "insider_activity"}

PRICE_HISTORY_DAYS = 400  # >252 trading days, enough for the momentum factor's 52-week-high lookback


class ScoringEngine:
    def __init__(
        self,
        tickers: list[str] | None = None,
        weights: dict[str, float] | None = None,
        optional_factors: set[str] | None = None,
        min_coverage: float | None = None,
        crowding_threshold: float | None = None,
    ):
        # Explicit constructor args always win; otherwise fall back to
        # config/config.yaml's `factors:` section; otherwise the hardcoded
        # defaults above — so weights are editable via the config file
        # without code changes, but still overridable per-call for backtests
        # or experiments.
        file_config = load_factors_config()

        self.tickers = tickers or load_universe()["ticker"].tolist()
        self.weights = weights or file_config.get("weights") or dict(DEFAULT_WEIGHTS)
        self.optional_factors = (
            optional_factors
            if optional_factors is not None
            else (set(file_config["optional_factors"]) if file_config.get("optional_factors") else set(OPTIONAL_FACTORS))
        )
        min_coverage = min_coverage if min_coverage is not None else file_config.get("min_coverage", 0.2)
        crowding_threshold = (
            crowding_threshold if crowding_threshold is not None else file_config.get("crowding_threshold", DEFAULT_CROWDING_THRESHOLD)
        )
        self.min_coverage = min_coverage
        self.crowding_threshold = crowding_threshold

    def _load_market_cap(self, fundamentals_history: dict[str, pd.DataFrame]) -> pd.Series:
        caps = {}
        for ticker, history in fundamentals_history.items():
            if history.empty:
                continue
            cap = history.iloc[-1].get("market_cap")
            if cap:
                caps[ticker] = cap
        return pd.Series(caps, dtype=float)

    def run(self) -> dict:
        sector_map = load_sector_map(self.tickers)
        price_start = (date.today() - timedelta(days=PRICE_HISTORY_DAYS)).isoformat()

        prices = load_prices_wide(self.tickers, price_start)
        sector_etf_prices = load_sector_etf_prices(price_start)
        fundamentals_history = load_fundamentals_history(self.tickers)
        form4 = load_form4(self.tickers)
        thirteen_f = load_13f_holdings(self.tickers)
        congressional = load_congressional_trades(self.tickers)
        analyst_estimates = load_analyst_estimates(self.tickers)
        market_cap = self._load_market_cap(fundamentals_history)

        raw_factors = {
            "momentum": compute_momentum(prices, sector_etf_prices, sector_map),
            "value": compute_value(fundamentals_history),
            "quality": compute_quality(fundamentals_history, sector_map),
            "growth": compute_growth(fundamentals_history, analyst_estimates),
            "estimate_revisions": compute_estimate_revisions(analyst_estimates),
            "insider_activity": compute_insider_activity(form4, market_cap),
            "institutional_flow": compute_institutional_flow(thirteen_f),
            "congressional": compute_congressional_factor(congressional),
        }

        # Every factor is sector-neutral z-scored here, uniformly, right before
        # blending — individual factor modules only produce economically
        # meaningful raw scores and stay agnostic to sector composition.
        sector_neutral_factors = {
            name: (sector_neutral_zscore(series, sector_map) if series is not None and not series.empty else series)
            for name, series in raw_factors.items()
        }

        composite, diagnostics = composite_score(
            sector_neutral_factors,
            self.weights,
            optional_factors=self.optional_factors,
            min_coverage=self.min_coverage,
        )
        ranked = rank_scores(composite)

        # Crowding must be computed on exactly the factors composite_score
        # actually used — including a factor composite_score already dropped
        # for thin coverage would pollute the complete-case sample (dropna()
        # across a mostly-empty column wipes out nearly every ticker) and
        # make the decomposition meaningless.
        surviving_factors = {
            name: series for name, series in sector_neutral_factors.items() if name not in diagnostics["dropped_factors"]
        }
        crowding = detect_factor_crowding(surviving_factors, self.weights, threshold=self.crowding_threshold)

        # Macro regime is computed and returned alongside, never blended into
        # the stock-level composite — see factors/macro_regime.py for why.
        macro_regime = compute_macro_regime(
            load_fred_series(SERIES_PPI),
            load_fred_series(SERIES_DXY_PROXY),
            load_fred_series(SERIES_BDI_PROXY),
        )

        return {
            "composite_score": composite,
            "rank": ranked,
            "factor_scores": sector_neutral_factors,
            "diagnostics": diagnostics,
            "crowding": crowding,
            "macro_regime": macro_regime,
        }
