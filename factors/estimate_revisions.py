import pandas as pd

from factors.scoring import zscore

REVISION_PERIOD = "0q"  # current quarter — the estimate analysts revise most actively
RECOMMENDATION_PERIOD = "0m"


def _revision_pct(series_by_date: pd.Series) -> float | None:
    """series_by_date: as_of_date -> value, ascending. Needs >=2 distinct
    snapshots to mean anything — with only one data pull on file (e.g. right
    after the daily sync job's first run), there is no revision to measure
    yet, so this returns None rather than a fabricated zero.
    """
    unique_dates = series_by_date.index.unique()
    if len(unique_dates) < 2:
        return None
    latest_value = series_by_date.iloc[-1]
    previous_value = series_by_date.iloc[-2]
    if previous_value is None or previous_value == 0:
        return None
    return (latest_value - previous_value) / abs(previous_value)


def compute_estimate_revisions(analyst_estimates: pd.DataFrame) -> pd.Series:
    """Raw (pre-sector-neutralization) estimate-revisions score: the direction
    analyst consensus is moving, not its absolute level (that's the growth
    factor's job). Needs at least two dated snapshots per ticker to compute —
    until the daily sync job has accumulated history, this returns an empty
    series and the composite scorer treats it as an optional factor with zero
    coverage for that period, per the graceful-degradation contract.
    """
    if analyst_estimates.empty:
        return pd.Series(dtype=float)

    eps_revision, revenue_revision, rating_shift = {}, {}, {}

    grouped = analyst_estimates.sort_values("as_of_date").groupby(["ticker", "metric", "period"])
    for (ticker, metric, period), group in grouped:
        if metric == "eps_estimate_avg" and period == REVISION_PERIOD:
            pct = _revision_pct(group.set_index("as_of_date")["value"])
            if pct is not None:
                eps_revision[ticker] = pct
        elif metric == "revenue_estimate_avg" and period == REVISION_PERIOD:
            pct = _revision_pct(group.set_index("as_of_date")["value"])
            if pct is not None:
                revenue_revision[ticker] = pct

    # Net rating shift: (bullish - bearish) analyst count, this month vs a
    # month ago, using the recommendations table's own trailing-month columns.
    rec_metrics = {"rec_strongBuy", "rec_buy", "rec_sell", "rec_strongSell"}
    rec_df = analyst_estimates[analyst_estimates["metric"].isin(rec_metrics)]
    for ticker, ticker_group in rec_df.groupby("ticker"):
        latest_date = ticker_group["as_of_date"].max()
        latest = ticker_group[ticker_group["as_of_date"] == latest_date]
        current_net = _net_bullish(latest, RECOMMENDATION_PERIOD)
        prior_net = _net_bullish(latest, "-1m")
        if current_net is not None and prior_net is not None:
            rating_shift[ticker] = current_net - prior_net

    components = pd.DataFrame(
        {
            "eps_revision": pd.Series(eps_revision),
            "revenue_revision": pd.Series(revenue_revision),
            "rating_shift": pd.Series(rating_shift),
        }
    )
    if components.empty:
        return pd.Series(dtype=float)

    normalized = components.apply(zscore)
    return normalized.mean(axis=1, skipna=True)


def _net_bullish(rows: pd.DataFrame, period: str) -> float | None:
    period_rows = rows[rows["period"] == period]
    if period_rows.empty:
        return None
    values = period_rows.set_index("metric")["value"]
    bullish = values.get("rec_strongBuy", 0) + values.get("rec_buy", 0)
    bearish = values.get("rec_sell", 0) + values.get("rec_strongSell", 0)
    return bullish - bearish
