import pandas as pd

from factors.data_loader import SECTOR_ETF_MAP
from factors.scoring import zscore

ACCELERATION_WINDOW_DAYS = 21  # ~1 trading month
RELATIVE_STRENGTH_WINDOW_DAYS = 126  # ~6 trading months
HIGH_LOOKBACK_DAYS = 252  # ~1 trading year


def _trailing_return(prices: pd.Series, window: int, offset: int = 0) -> float | None:
    """Return over `window` trading days, ending `offset` days before the last
    observation. offset=0 means the most recent window; offset=window means the
    window immediately before that one (used for the acceleration calc).
    """
    series = prices.dropna()
    if offset:
        series = series.iloc[: -offset] if offset < len(series) else pd.Series(dtype=float)
    if len(series) <= window:
        return None
    return series.iloc[-1] / series.iloc[-1 - window] - 1


def compute_momentum(prices_wide: pd.DataFrame, sector_etf_prices: pd.DataFrame, sector_map: pd.Series) -> pd.Series:
    """Raw (pre-sector-neutralization) momentum score, blending three signals:

    - Price acceleration: this month's return minus last month's return. Positive
      means the stock's trend is speeding up, not just continuing — momentum
      research (e.g. Novy-Marx) finds this "intermediate" signal adds information
      beyond a single trailing-return window.
    - 52-week high proximity: last close / trailing 252-day high. Stocks near
      their 52-week high tend to keep outperforming (the well-documented
      "52-week high" anchoring effect), so this is used level (not as a return).
    - Relative strength vs. sector ETF: 6-month stock return minus the 6-month
      return of its own GICS-sector SPDR ETF, isolating stock-specific momentum
      from a sector-wide rally the stock is just riding along with.

    Each sub-signal is z-scored (cross-sectionally, not yet sector-neutral —
    that happens once when the engine blends this into the composite) before
    being averaged, since acceleration/proximity/relative-strength live on
    unrelated scales and a raw average would let whichever has the widest
    numeric range dominate.
    """
    tickers = [t for t in prices_wide.columns if t in sector_map.index]

    acceleration = {}
    proximity = {}
    relative_strength = {}

    for ticker in tickers:
        series = prices_wide[ticker].dropna()
        if series.empty:
            continue

        recent = _trailing_return(series, ACCELERATION_WINDOW_DAYS)
        prior = _trailing_return(series, ACCELERATION_WINDOW_DAYS, offset=ACCELERATION_WINDOW_DAYS)
        if recent is not None and prior is not None:
            acceleration[ticker] = recent - prior

        if len(series) >= HIGH_LOOKBACK_DAYS:
            trailing_high = series.iloc[-HIGH_LOOKBACK_DAYS:].max()
            if trailing_high:
                proximity[ticker] = series.iloc[-1] / trailing_high

        sector = sector_map.get(ticker)
        etf = SECTOR_ETF_MAP.get(sector)
        if etf and etf in sector_etf_prices.columns:
            stock_return = _trailing_return(series, RELATIVE_STRENGTH_WINDOW_DAYS)
            etf_return = _trailing_return(sector_etf_prices[etf], RELATIVE_STRENGTH_WINDOW_DAYS)
            if stock_return is not None and etf_return is not None:
                relative_strength[ticker] = stock_return - etf_return

    components = pd.DataFrame(
        {
            "acceleration": pd.Series(acceleration),
            "proximity": pd.Series(proximity),
            "relative_strength": pd.Series(relative_strength),
        }
    )
    if components.empty:
        return pd.Series(dtype=float)

    normalized = components.apply(zscore)
    return normalized.mean(axis=1, skipna=True)
