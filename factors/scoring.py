import pandas as pd


def zscore(series: pd.Series) -> pd.Series:
    return (series - series.mean()) / series.std(ddof=0)


def composite_score(factor_scores: dict[str, pd.Series], weights: dict[str, float]) -> pd.Series:
    # Z-score each factor first so differently-scaled factors (e.g. P/E vs.
    # momentum %) contribute comparably to the weighted blend, then convert
    # the blend to a percentile rank so downstream long/short cutoffs are
    # scale-free.
    normalized = {name: zscore(scores) for name, scores in factor_scores.items()}
    combined = sum(normalized[name] * weight for name, weight in weights.items())
    return combined.rank(pct=True)
