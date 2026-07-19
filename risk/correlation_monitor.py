import pandas as pd

DEFAULT_CORRELATION_THRESHOLD = 0.85


def monitor_correlations(
    weights: pd.Series, returns: pd.DataFrame, threshold: float = DEFAULT_CORRELATION_THRESHOLD
) -> list[dict]:
    """Flags any pair of currently-held (nonzero weight) positions whose
    return correlation exceeds `threshold` in absolute value — two highly
    correlated positions are effectively one concentrated bet even if their
    individual position sizes look fine in isolation. Returns pairs sorted
    most-correlated first.
    """
    held = weights[weights != 0].index
    held = [t for t in held if t in returns.columns]
    if len(held) < 2:
        return []

    correlation_matrix = returns[held].corr()
    flagged = []
    for i, ticker_a in enumerate(held):
        for ticker_b in held[i + 1 :]:
            corr = correlation_matrix.loc[ticker_a, ticker_b]
            if pd.notna(corr) and abs(corr) > threshold:
                flagged.append({"pair": (ticker_a, ticker_b), "correlation": float(corr)})

    return sorted(flagged, key=lambda item: -abs(item["correlation"]))
