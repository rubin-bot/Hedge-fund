"""Glue layer between the existing engine modules and the API's JSON shapes
for the MODEL's suggestions (composite scores, factor breakdowns, ranked
candidates) -- as opposed to api/ledger_service.py, which analyzes the
user's ACTUAL positions in the virtual ledger.

Nothing in factors/ is modified or reimplemented here -- this module only
calls into it, joins its output with sector labels, and casts pandas/numpy
types to JSON-safe Python types. `compute_pipeline()` deliberately does
NOT run `portfolio.construction.construct_portfolio()`'s MVO optimizer or
`risk.gate.evaluate_portfolio()` -- those build a target-weights book for
automated rebalancing, which doesn't apply to the manual candidate-review
flow this module now serves; see api/routers/candidates.py.
"""

from __future__ import annotations

import math
from datetime import date

import pandas as pd

from api.cache import TTLCache
from data.db import connection
from factors.data_loader import load_sector_map, load_universe
from factors.engine import ScoringEngine

# Scoring the full default universe (33 tickers) takes a couple of seconds
# -- cheap enough that the TTL here is mostly about not hammering the DB on
# every keystroke/rerender, not about avoiding an expensive optimizer call
# (unlike the old MVO-based pipeline this replaced).
_PIPELINE_CACHE_TTL_SECONDS = 900
_UNIVERSE_CACHE_TTL_SECONDS = 3600

_pipeline_cache = TTLCache(ttl_seconds=_PIPELINE_CACHE_TTL_SECONDS)
_universe_cache = TTLCache(ttl_seconds=_UNIVERSE_CACHE_TTL_SECONDS)


def _num(value) -> float | None:
    """Casts a pandas/numpy scalar to a plain float, or None for NaN/missing.

    Needed because pandas/numpy scalar types (np.float64, np.bool_, NaN)
    aren't directly JSON-serializable the way Pydantic expects, and a
    missing factor/score should surface as `null`, not the string "nan".
    """
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def default_universe() -> list[str]:
    """Tickers that are both S&P 500 constituents and actually have price
    history backfilled -- the sector ETFs and the SPY benchmark are
    deliberately excluded since they aren't stock-selection candidates.
    """

    def _compute() -> list[str]:
        with connection() as conn:
            have = {row[0] for row in conn.execute("SELECT DISTINCT ticker FROM prices_daily").fetchall()}
        sp500 = set(load_universe()["ticker"])
        return sorted(sp500 & have)

    return _universe_cache.get_or_compute("universe", _compute)


def _cache_key(tickers: list[str]) -> str:
    return ",".join(sorted(tickers))


def compute_pipeline(tickers: list[str] | None = None, refresh: bool = False) -> dict:
    """Runs ScoringEngine and returns the composite/rank/factor scores plus
    sector labels for every router that needs the model's view of the
    universe. Cached per distinct ticker universe; pass refresh=True to
    force a recompute.
    """
    tickers = tickers or default_universe()
    key = _cache_key(tickers)
    if refresh:
        _pipeline_cache.invalidate(key)

    def _compute() -> dict:
        scoring = ScoringEngine(tickers=tickers).run()
        sector_map = load_sector_map(tickers)
        return {
            "as_of": date.today().isoformat(),
            "tickers": tickers,
            "scoring": scoring,
            "sector_map": sector_map,
        }

    return _pipeline_cache.get_or_compute(key, _compute)


def build_factor_response(tickers: list[str] | None = None, refresh: bool = False) -> dict:
    bundle = compute_pipeline(tickers, refresh=refresh)
    scoring = bundle["scoring"]
    sector_map = bundle["sector_map"]
    factor_frame = pd.DataFrame(scoring["factor_scores"])
    composite = scoring["composite_score"]
    rank = scoring["rank"]

    holdings = []
    for ticker in bundle["tickers"]:
        factors = {}
        if ticker in factor_frame.index:
            for factor_name in factor_frame.columns:
                factors[factor_name] = _num(factor_frame.loc[ticker, factor_name])
        holdings.append(
            {
                "ticker": ticker,
                "sector": sector_map.get(ticker),
                "composite_score": _num(composite.get(ticker)),
                "rank": _num(rank.get(ticker)),
                "factors": factors,
            }
        )

    diagnostics = scoring["diagnostics"]
    crowding = scoring["crowding"]

    return {
        "as_of": bundle["as_of"],
        "dropped_factors": diagnostics["dropped_factors"],
        "coverage": {k: _num(v) or 0.0 for k, v in diagnostics["coverage"].items()},
        "holdings": holdings,
        "crowding": {
            "crowded": bool(crowding["crowded"]),
            "dominant_factor": crowding["dominant_factor"],
            "contributions": {k: _num(v) or 0.0 for k, v in crowding["contributions"].items()},
            "threshold": crowding["threshold"],
            "num_tickers_used": crowding.get("num_tickers_used"),
            "reason": crowding.get("reason"),
        },
    }
