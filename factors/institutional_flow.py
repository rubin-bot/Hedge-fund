import pandas as pd

from factors.scoring import zscore


def compute_institutional_flow(thirteen_f: pd.DataFrame) -> pd.Series:
    """Raw (pre-sector-neutralization) institutional-flow score: the
    quarter-over-quarter change in aggregate 13F-reported shares held across
    all filing institutions for a ticker. Needs at least two quarterly data
    sets on file — with only one quarter backfilled, this returns an empty
    series (handled the same way as any other under-covered optional factor).
    """
    if thirteen_f.empty:
        return pd.Series(dtype=float)

    df = thirteen_f.copy()
    df["report_period_parsed"] = pd.to_datetime(df["report_period"], format="%d-%b-%Y", errors="coerce")
    aggregated = (
        df.dropna(subset=["report_period_parsed"])
        .groupby(["ticker", "report_period_parsed"])["shares"]
        .sum()
        .reset_index()
    )

    raw_scores = {}
    for ticker, group in aggregated.groupby("ticker"):
        group = group.sort_values("report_period_parsed")
        if len(group) < 2:
            continue
        latest_shares, prior_shares = group["shares"].iloc[-1], group["shares"].iloc[-2]
        if not prior_shares:
            continue
        raw_scores[ticker] = latest_shares / prior_shares - 1

    raw = pd.Series(raw_scores)
    if raw.empty:
        return raw
    return zscore(raw)
