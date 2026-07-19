import pandas as pd


def check_position_limits(weights: pd.Series, max_position_weight: float) -> pd.Series:
    return weights[weights.abs() > max_position_weight]


def check_sector_limits(weights: pd.Series, sector_map: pd.Series, max_sector_weight: float) -> pd.Series:
    sector_exposure = weights.groupby(sector_map).sum().abs()
    return sector_exposure[sector_exposure > max_sector_weight]


def historical_var(returns: pd.Series, confidence: float = 0.95) -> float:
    # Historical VaR: the loss at the (1 - confidence) percentile of realized
    # returns, e.g. 95% VaR is the loss not expected to be exceeded 95% of days.
    return -returns.quantile(1 - confidence)


def check_beta_neutrality(weights: pd.Series, beta: pd.Series, tolerance: float) -> float | None:
    common = weights.index.intersection(beta.index)
    portfolio_beta = float((weights[common] * beta[common]).sum())
    return portfolio_beta if abs(portfolio_beta) > tolerance else None


def check_turnover(weights: pd.Series, prior_weights: pd.Series, budget: float) -> float | None:
    all_tickers = weights.index.union(prior_weights.index)
    turnover = float(
        (weights.reindex(all_tickers, fill_value=0.0) - prior_weights.reindex(all_tickers, fill_value=0.0))
        .abs()
        .sum()
    )
    return turnover if turnover > budget else None
