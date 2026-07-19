import re

import pandas as pd

from factors.scoring import zscore

AMOUNT_RE = re.compile(r"\$[\d,]+")
MIN_COVERED_TICKERS = 5  # below this, treat the whole factor as unavailable for the period
CLUSTER_BONUS_PER_EXTRA_MEMBER = 0.5


def _amount_midpoint(amount_range: str | None) -> float | None:
    """House/Senate disclosures report a dollar *range*, not an exact figure
    (e.g. "$1,001 - $15,000"), since that's all the law requires members to
    disclose. This takes the midpoint as a point estimate; for open-ended
    "Over $X" ranges it falls back to the single lower-bound figure.
    """
    if not amount_range:
        return None
    values = [float(m.replace("$", "").replace(",", "")) for m in AMOUNT_RE.findall(amount_range)]
    if not values:
        return None
    return sum(values) / len(values)


def _is_purchase(transaction_type: str | None) -> bool:
    return bool(transaction_type) and transaction_type.strip().upper().startswith(("P", "PURCHASE"))


def _is_sale(transaction_type: str | None) -> bool:
    return bool(transaction_type) and transaction_type.strip().upper().startswith(("S", "SALE"))


def compute_congressional_factor(congressional_trades: pd.DataFrame) -> pd.Series | None:
    """Raw (pre-sector-neutralization) congressional-trading score, or None if
    the data for this period is too sparse to be meaningful (this is the
    "optional factor" contract the composite scorer checks — see scoring.py).

    Scored like insider cluster-buy activity: net (purchase - discounted sale)
    dollar-range midpoint, boosted when multiple distinct members buy the same
    name. Not normalized by market cap, unlike the insider-activity factor —
    congressional trade sizes are already bounded by the disclosure buckets
    themselves rather than scaling with company size the way real transaction
    values do, so a cap-normalized version would mostly just re-derive 1/market
    cap rather than add signal.
    """
    if congressional_trades.empty:
        return None

    df = congressional_trades.dropna(subset=["ticker"]).copy()
    if df["ticker"].nunique() < MIN_COVERED_TICKERS:
        return None

    df["amount_mid"] = df["amount_range"].apply(_amount_midpoint)
    df = df.dropna(subset=["amount_mid"])
    if df.empty:
        return None

    raw_scores = {}
    for ticker, group in df.groupby("ticker"):
        buys = group[group["transaction_type"].apply(_is_purchase)]
        sales = group[group["transaction_type"].apply(_is_sale)]

        buy_value = buys["amount_mid"].sum()
        sale_value = sales["amount_mid"].sum()
        distinct_buyers = buys["member_name"].nunique()
        cluster_bonus = 1 + CLUSTER_BONUS_PER_EXTRA_MEMBER * max(0, distinct_buyers - 1)

        raw_scores[ticker] = buy_value * cluster_bonus - sale_value * 0.3

    raw = pd.Series(raw_scores)
    if raw.empty:
        return None
    return zscore(raw)
