import cvxpy as cp
import numpy as np
import pandas as pd


def mean_variance_optimize(
    expected_returns: pd.Series,
    covariance: pd.DataFrame,
    max_position_weight: float,
    gross_exposure: float,
    net_exposure: float,
    risk_aversion: float = 1.0,
) -> pd.Series:
    n = len(expected_returns)
    w = cp.Variable(n)
    mu = expected_returns.values
    sigma = covariance.values

    # Classic Markowitz objective: maximize expected return net of a variance
    # penalty (quad_form = w'*sigma*w, the portfolio variance).
    objective = cp.Maximize(mu @ w - risk_aversion * cp.quad_form(w, sigma))
    constraints = [
        cp.sum(cp.abs(w)) <= gross_exposure,  # gross = sum of |long| + |short| weights
        cp.sum(w) == net_exposure,  # net = longs minus shorts, e.g. 0 for market-neutral
        w <= max_position_weight,
        w >= -max_position_weight,
    ]
    problem = cp.Problem(objective, constraints)
    problem.solve()

    return pd.Series(np.asarray(w.value).flatten(), index=expected_returns.index)
