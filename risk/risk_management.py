import pandas as pd


def check_position_limits(weights: pd.Series, max_position_weight: float) -> pd.Series:
    return weights[weights.abs() > max_position_weight]


def check_sector_limits(weights: pd.Series, sector_map: pd.Series, max_sector_weight: float) -> pd.Series:
    sector_exposure = weights.groupby(sector_map).sum().abs()
    return sector_exposure[sector_exposure > max_sector_weight]


def historical_var(returns: pd.Series, confidence: float = 0.95) -> float:
    return -returns.quantile(1 - confidence)
