import pandas as pd


def zscore(series: pd.Series) -> pd.Series:
    return (series - series.mean()) / series.std(ddof=0)


def composite_score(factor_scores: dict[str, pd.Series], weights: dict[str, float]) -> pd.Series:
    normalized = {name: zscore(scores) for name, scores in factor_scores.items()}
    combined = sum(normalized[name] * weight for name, weight in weights.items())
    return combined.rank(pct=True)
