import pandas as pd

from factors.scoring import zscore

# SEC Form 4 transaction codes. Only P (open-market purchase) and S (open-market
# sale) reflect a genuinely discretionary bet by the insider. A/F/M/G/C are
# grants, tax-withholding dispositions, option exercises, and gifts — mechanical
# or scheduled events that happen regardless of the insider's view on the
# stock, so they're excluded entirely rather than counted as a weak signal.
# (One known simplification: a "sell-to-cover" S filed right after an M exercise
# is still counted as a discretionary sale here, since matching same-insider
# same-day M->S sequences reliably would need per-filer transaction ordering
# this dataset doesn't cleanly support. That means routine post-exercise
# liquidations get some weight they arguably shouldn't — SALE_WEIGHT below
# exists partly to blunt that.)
BUY_CODE = "P"
SALE_CODE = "S"
LOOKBACK_DAYS = 90
SALE_WEIGHT = 0.3  # sales count for less than buys of the same dollar size
CLUSTER_BONUS_PER_EXTRA_BUYER = 0.5  # each additional distinct insider buying adds 50% weight


def compute_insider_activity(form4: pd.DataFrame, market_cap: pd.Series) -> pd.Series:
    """Raw (pre-sector-neutralization) insider-activity score: normalized
    dollar value of open-market insider buying (boosted when multiple distinct
    insiders buy the same name — a "cluster buy", historically a stronger
    signal than a single insider's purchase) minus a discounted value of
    open-market selling. Normalized by market cap so a $2M purchase means
    something different at a $50B company than a $2B one.
    """
    if form4.empty or market_cap.empty:
        return pd.Series(dtype=float)

    df = form4.copy()
    df["transaction_date"] = pd.to_datetime(df["transaction_date"], errors="coerce")
    cutoff = pd.Timestamp.today() - pd.Timedelta(days=LOOKBACK_DAYS)
    df = df[(df["transaction_date"] >= cutoff) & df["transaction_code"].isin([BUY_CODE, SALE_CODE])]
    if df.empty:
        return pd.Series(dtype=float)

    df["dollar_value"] = df["shares"] * df["price_per_share"]

    raw_scores = {}
    for ticker, group in df.groupby("ticker"):
        cap = market_cap.get(ticker)
        if not cap:
            continue

        buys = group[group["transaction_code"] == BUY_CODE]
        sales = group[group["transaction_code"] == SALE_CODE]

        buy_value = buys["dollar_value"].sum(skipna=True)
        sale_value = sales["dollar_value"].sum(skipna=True)
        distinct_buyers = buys["filer_name"].nunique()
        cluster_bonus = 1 + CLUSTER_BONUS_PER_EXTRA_BUYER * max(0, distinct_buyers - 1)

        raw_scores[ticker] = (buy_value * cluster_bonus - sale_value * SALE_WEIGHT) / cap

    raw = pd.Series(raw_scores)
    if raw.empty:
        return raw
    return zscore(raw)
