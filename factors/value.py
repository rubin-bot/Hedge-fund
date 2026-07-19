import pandas as pd

from factors.scoring import zscore


def _latest_row(history: pd.DataFrame) -> pd.Series | None:
    if history is None or history.empty:
        return None
    return history.iloc[-1]


def compute_value(fundamentals_history: dict[str, pd.DataFrame]) -> pd.Series:
    """Raw (pre-sector-neutralization) value score from P/E, P/B, and EV/EBITDA.

    All three are "cheaper is better" ratios, so each is inverted (1/ratio)
    before z-scoring — otherwise a high z-score would mean *expensive*, which
    would silently flip the sign of the whole value factor. Ratios computed
    from a non-positive denominator (loss-making companies, negative equity)
    are treated as missing rather than as a nonsensical (and sign-flipping)
    negative multiple.
    """
    inverse_pe, inverse_pb, inverse_ev_ebitda = {}, {}, {}

    for ticker, history in fundamentals_history.items():
        row = _latest_row(history)
        if row is None:
            continue

        market_cap = row.get("market_cap")
        net_income = row.get("net_income")
        equity = row.get("stockholders_equity")
        debt = row.get("total_debt")
        cash = row.get("cash_and_equivalents")
        ebitda = row.get("ebitda")

        if market_cap and net_income and net_income > 0:
            inverse_pe[ticker] = net_income / market_cap  # = 1 / (P/E)

        if market_cap and equity and equity > 0:
            inverse_pb[ticker] = equity / market_cap  # = 1 / (P/B)

        if market_cap is not None and ebitda and ebitda > 0:
            enterprise_value = market_cap + (debt or 0) - (cash or 0)
            if enterprise_value > 0:
                inverse_ev_ebitda[ticker] = ebitda / enterprise_value  # = 1 / (EV/EBITDA)

    components = pd.DataFrame(
        {
            "inverse_pe": pd.Series(inverse_pe),
            "inverse_pb": pd.Series(inverse_pb),
            "inverse_ev_ebitda": pd.Series(inverse_ev_ebitda),
        }
    )
    if components.empty:
        return pd.Series(dtype=float)

    normalized = components.apply(zscore)
    return normalized.mean(axis=1, skipna=True)
