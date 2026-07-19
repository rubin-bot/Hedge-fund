import cvxpy as cp
import numpy as np
import pandas as pd

from portfolio.risk_models import TRADING_DAYS_PER_YEAR


def mean_variance_optimize(
    expected_returns: pd.Series,
    covariance: pd.DataFrame,
    sector_map: pd.Series,
    beta: pd.Series,
    max_position_weight: float,
    gross_exposure: float,
    net_exposure: float,
    target_volatility: float,
    sector_neutrality_band: float = 0.02,
    beta_neutrality_band: float = 0.10,
    current_weights: pd.Series | None = None,
    turnover_budget: float | None = None,
    slippage_bps: float = 5.0,
    commission_bps: float = 1.0,
) -> pd.Series:
    """Maximizes expected return net of estimated transaction costs, subject
    to a target-volatility risk budget plus sector-neutrality, beta-neutrality,
    position-size, gross/net exposure, and turnover constraints.

    All Series/DataFrame args must already share the same ticker index —
    that's the caller's responsibility (see portfolio.construction.
    construct_portfolio, which builds the full ticker universe, including
    positions being unwound, before calling this). A misaligned index is a
    caller bug, so this raises rather than silently reindexing/dropping names.
    """
    tickers = expected_returns.index
    if not (covariance.index.equals(tickers) and sector_map.index.equals(tickers) and beta.index.equals(tickers)):
        raise ValueError("expected_returns, covariance, sector_map, and beta must share the same ticker index")

    n = len(tickers)
    w = cp.Variable(n)
    mu = expected_returns.values
    sigma = covariance.values

    if current_weights is not None:
        w_prev = current_weights.reindex(tickers, fill_value=0.0).values
    else:
        w_prev = np.zeros(n)

    # Transaction cost is charged on turnover (the L1 distance between old and
    # new weights) at the same slippage+commission rate simulation/paper_trading.py
    # will eventually apply — a linear cost term keeps the problem convex (cp.abs
    # is convex, and it's subtracted, so it only ever pulls the objective down).
    cost_rate = (slippage_bps + commission_bps) / 10_000
    objective = cp.Maximize(mu @ w - cost_rate * cp.sum(cp.abs(w - w_prev)))

    constraints = [
        cp.sum(cp.abs(w)) <= gross_exposure,  # gross = sum of |long| + |short| weights
        cp.sum(w) == net_exposure,  # net = longs minus shorts, e.g. 0 for market-neutral
        w <= max_position_weight,
        w >= -max_position_weight,
    ]

    # Target volatility as a hard constraint rather than a risk-aversion penalty
    # in the objective: "maximize return for a target volatility" is the
    # constrained-return-maximization dual of classic penalized-variance
    # Markowitz. covariance is of DAILY returns; annualize variance by the
    # standard x252 trading-days convention (vol scales by sqrt(252)) since
    # target_volatility (e.g. 0.15) is quoted annualized.
    annualized_covariance = sigma * TRADING_DAYS_PER_YEAR
    constraints.append(cp.quad_form(w, annualized_covariance) <= target_volatility**2)

    # Sector neutrality: each sector's NET (signed) weight stays within a small
    # band around zero — long and short exposure within a sector should roughly
    # offset, distinct from a gross concentration cap (which this module doesn't
    # enforce directly; see risk.risk_management.check_sector_limits for that).
    for sector in sector_map.unique():
        sector_mask = (sector_map == sector).values
        if sector_mask.any():
            constraints.append(cp.sum(w[sector_mask]) <= sector_neutrality_band)
            constraints.append(cp.sum(w[sector_mask]) >= -sector_neutrality_band)

    # Beta neutrality: portfolio beta (the weighted sum of position betas) stays
    # within a small band around zero, so the book isn't taking a hidden
    # directional bet on the overall market via its long/short tilt.
    constraints.append(beta.values @ w <= beta_neutrality_band)
    constraints.append(beta.values @ w >= -beta_neutrality_band)

    if turnover_budget is not None:
        constraints.append(cp.sum(cp.abs(w - w_prev)) <= turnover_budget)

    problem = cp.Problem(objective, constraints)
    problem.solve()
    if w.value is None:
        raise ValueError(f"MVO problem did not solve to a feasible solution (status: {problem.status})")

    return pd.Series(np.asarray(w.value).flatten(), index=tickers)
