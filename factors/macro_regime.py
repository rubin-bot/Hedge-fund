"""Macro regime indicator: PPI, the DXY (dollar) proxy, and the Baltic Dry
Index proxy, reduced to a trend/regime read. This is deliberately NOT a
stock-level factor — it isn't sector-neutral z-scored or blended into the
composite score, since every stock in the universe experiences the same
macro backdrop (sector-neutralizing a market-wide series would just zero it
out). It's exposed separately for the portfolio/risk layer to condition on
(e.g. cutting gross exposure in a "tightening" regime), not for stock ranking.
"""

import pandas as pd

TREND_WINDOW_DAYS = 90
FLAT_THRESHOLD = 0.01  # +/-1% over the window counts as "flat", not a trend


def _trend_pct(series: pd.Series, window_days: int) -> float | None:
    if series.empty:
        return None
    series = series.dropna()
    series.index = pd.to_datetime(series.index)
    series = series.sort_index()
    if len(series) < 2:
        return None

    cutoff = series.index[-1] - pd.Timedelta(days=window_days)
    window = series[series.index >= cutoff]
    if len(window) < 2 or window.iloc[0] == 0:
        return None
    return window.iloc[-1] / window.iloc[0] - 1


def _classify(trend: float | None) -> str:
    if trend is None:
        return "unknown"
    if trend > FLAT_THRESHOLD:
        return "rising"
    if trend < -FLAT_THRESHOLD:
        return "falling"
    return "flat"


def compute_macro_regime(
    ppi: pd.Series, dxy_proxy: pd.Series, bdi_proxy: pd.Series, window_days: int = TREND_WINDOW_DAYS
) -> dict:
    ppi_trend = _trend_pct(ppi, window_days)
    dxy_trend = _trend_pct(dxy_proxy, window_days)
    bdi_trend = _trend_pct(bdi_proxy, window_days)

    return {
        "ppi_trend_pct": ppi_trend,
        "ppi_regime": _classify(ppi_trend),
        "dxy_trend_pct": dxy_trend,
        "dxy_regime": _classify(dxy_trend),
        "bdi_trend_pct": bdi_trend,
        "bdi_regime": _classify(bdi_trend),
        "window_days": window_days,
    }
