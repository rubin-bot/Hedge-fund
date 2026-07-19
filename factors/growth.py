import pandas as pd

from factors.scoring import zscore

PROJECTED_GROWTH_PERIOD = "0y"  # current fiscal year, per yfinance's estimate period convention


def _yoy_growth(history: pd.DataFrame, field: str) -> float | None:
    if history is None or len(history) < 2:
        return None
    curr, prior = history.iloc[-1].get(field), history.iloc[-2].get(field)
    if curr is None or prior is None or prior <= 0:
        return None
    return curr / prior - 1


def compute_growth(fundamentals_history: dict[str, pd.DataFrame], analyst_estimates: pd.DataFrame) -> pd.Series:
    """Raw (pre-sector-neutralization) growth score blending trailing
    fundamental growth (revenue and net income, YoY from the last two fiscal
    periods on file) with forward-looking growth (analyst consensus revenue
    and EPS growth estimates for the current fiscal year). Combining realized
    and projected growth avoids over-weighting either a company that grew
    fast last year but is guided to slow down, or one priced purely on a
    forecast that hasn't materialized yet.
    """
    revenue_growth, earnings_growth = {}, {}
    for ticker, history in fundamentals_history.items():
        rg = _yoy_growth(history, "revenue")
        if rg is not None:
            revenue_growth[ticker] = rg
        eg = _yoy_growth(history, "net_income")
        if eg is not None:
            earnings_growth[ticker] = eg

    projected_revenue_growth, projected_eps_growth = {}, {}
    if not analyst_estimates.empty:
        latest = analyst_estimates.sort_values("as_of_date").groupby(["ticker", "metric", "period"]).last()
        for ticker in analyst_estimates["ticker"].unique():
            key = (ticker, "revenue_estimate_growth", PROJECTED_GROWTH_PERIOD)
            if key in latest.index:
                projected_revenue_growth[ticker] = latest.loc[key, "value"]
            key = (ticker, "eps_estimate_growth", PROJECTED_GROWTH_PERIOD)
            if key in latest.index:
                projected_eps_growth[ticker] = latest.loc[key, "value"]

    components = pd.DataFrame(
        {
            "revenue_growth": pd.Series(revenue_growth),
            "earnings_growth": pd.Series(earnings_growth),
            "projected_revenue_growth": pd.Series(projected_revenue_growth),
            "projected_eps_growth": pd.Series(projected_eps_growth),
        }
    )
    if components.empty:
        return pd.Series(dtype=float)

    normalized = components.apply(zscore)
    return normalized.mean(axis=1, skipna=True)
