import numpy as np
import pandas as pd

TRADING_DAYS_PER_YEAR = 252


def estimate_covariance_matrix(returns: pd.DataFrame, shrinkage: float = 0.2) -> pd.DataFrame:
    """Sample covariance of daily returns, shrunk toward its diagonal.

    Sample covariance from a limited daily-return history is noisy, and once
    the number of tickers approaches the number of observations it's
    guaranteed singular (rank <= T-1 for T observations) — cvxpy's
    quad_form() needs a positive-semidefinite matrix, so a singular sample
    covariance isn't just imprecise, it's unusable. Shrinking toward the
    diagonal (same variances, zero off-diagonal correlation) trades a little
    bias for a much better-conditioned matrix. This uses a fixed shrinkage
    intensity rather than Ledoit-Wolf's data-driven optimal intensity, to
    avoid adding scikit-learn as a dependency for one estimator — revisit if
    a fixed 0.2 proves too crude as the universe grows toward the full S&P 500.
    """
    sample = returns.cov()
    diagonal_target = pd.DataFrame(np.diag(np.diag(sample.values)), index=sample.index, columns=sample.columns)
    return (1 - shrinkage) * sample + shrinkage * diagonal_target


def estimate_beta(returns: pd.DataFrame, benchmark_returns: pd.Series, min_observations: int = 60) -> pd.Series:
    """Market-model beta per ticker: beta_i = Cov(r_i, r_benchmark) / Var(r_benchmark).

    Uses only the trading days where both the stock and the benchmark have a
    return (inner join), since a ticker's price history may start later than
    the benchmark's. min_observations guards against a beta estimated off a
    handful of noisy overlapping days for a thinly-backfilled ticker — such
    tickers are dropped from the result rather than given an unreliable beta.
    """
    benchmark_variance = benchmark_returns.var()
    betas = {}
    for ticker in returns.columns:
        common = returns[ticker].dropna().index.intersection(benchmark_returns.dropna().index)
        if len(common) < min_observations:
            continue
        covariance = returns.loc[common, ticker].cov(benchmark_returns.loc[common])
        betas[ticker] = covariance / benchmark_variance
    return pd.Series(betas, dtype=float)
