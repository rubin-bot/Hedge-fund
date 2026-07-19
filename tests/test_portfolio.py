import numpy as np
import pandas as pd
import pytest

from portfolio.construction import (
    _apply_sector_cap,
    _cap_and_redistribute_positive,
    _tilt_weights,
    apply_beta_neutralization,
    apply_turnover_budget,
    construct_portfolio,
    conviction_tilt_positions,
)
from portfolio.optimization import mean_variance_optimize
from portfolio.risk_models import estimate_beta, estimate_covariance_matrix
from risk.risk_management import check_beta_neutrality, check_turnover


# --- risk_models ---------------------------------------------------------------


def test_covariance_shrinkage_scales_off_diagonal_leaves_diagonal_unchanged():
    returns = pd.DataFrame(
        {"A": [0.01, -0.02, 0.03, 0.0, -0.01], "B": [0.02, -0.01, 0.01, 0.01, -0.02]}
    )
    sample = returns.cov()
    shrunk = estimate_covariance_matrix(returns, shrinkage=0.3)

    assert abs(shrunk.loc["A", "B"] - 0.7 * sample.loc["A", "B"]) < 1e-9
    assert abs(shrunk.loc["A", "A"] - sample.loc["A", "A"]) < 1e-9
    assert abs(shrunk.loc["B", "B"] - sample.loc["B", "B"]) < 1e-9


def test_estimate_beta_recovers_exact_beta_with_noiseless_returns():
    market = pd.Series(np.linspace(-0.02, 0.02, 100))
    returns = pd.DataFrame({"A": 1.5 * market, "B": 0.5 * market})
    beta = estimate_beta(returns, market, min_observations=10)
    assert abs(beta["A"] - 1.5) < 1e-9
    assert abs(beta["B"] - 0.5) < 1e-9


def test_estimate_beta_drops_tickers_below_min_observations():
    market = pd.Series(np.linspace(-0.02, 0.02, 100))
    thin = pd.Series([0.01] * 5 + [np.nan] * 95)  # only 5 real observations
    returns = pd.DataFrame({"A": 1.0 * market, "THIN": thin})
    beta = estimate_beta(returns, market, min_observations=60)
    assert "A" in beta.index
    assert "THIN" not in beta.index


# --- mean_variance_optimize ------------------------------------------------------


def _synthetic_universe():
    tickers = ["A1", "A2", "A3", "B1", "B2", "B3"]
    sector_map = pd.Series({"A1": "S1", "A2": "S1", "A3": "S1", "B1": "S2", "B2": "S2", "B3": "S2"})
    expected_returns = pd.Series({"A1": 0.02, "A2": 0.01, "A3": -0.01, "B1": -0.02, "B2": 0.015, "B3": -0.015})
    beta = pd.Series({"A1": 1.0, "A2": 1.2, "A3": 0.8, "B1": 0.9, "B2": 1.1, "B3": 1.0})
    covariance = pd.DataFrame(np.eye(6) * 0.0001, index=tickers, columns=tickers)
    return tickers, sector_map, expected_returns, beta, covariance


def test_mean_variance_optimize_respects_all_constraints():
    # cvxpy is an iterative solver, not closed-form arithmetic — constraint
    # checks below use a looser tolerance (~1e-4) than the 1e-9 used for exact
    # arithmetic elsewhere in this file, matching this repo's convention of
    # tightest-tolerance-that-reflects-what's-actually-being-computed.
    _, sector_map, expected_returns, beta, covariance = _synthetic_universe()

    weights = mean_variance_optimize(
        expected_returns=expected_returns,
        covariance=covariance,
        sector_map=sector_map,
        beta=beta,
        max_position_weight=0.3,
        gross_exposure=1.0,
        net_exposure=0.0,
        target_volatility=0.10,
        sector_neutrality_band=0.05,
        beta_neutrality_band=0.05,
    )

    assert abs(weights.sum()) < 1e-4  # net exposure
    assert weights.abs().sum() <= 1.0 + 1e-4  # gross exposure
    assert (weights.abs() <= 0.3 + 1e-4).all()  # position caps

    for sector in sector_map.unique():
        assert abs(weights[sector_map == sector].sum()) <= 0.05 + 1e-4  # sector neutrality

    assert abs((weights * beta).sum()) <= 0.05 + 1e-4  # beta neutrality

    annualized_vol = float((weights.values @ (covariance.values * 252) @ weights.values) ** 0.5)
    assert annualized_vol <= 0.10 + 1e-3  # target volatility


def test_mean_variance_optimize_respects_turnover_budget():
    tickers, sector_map, _, _, covariance = _synthetic_universe()
    expected_returns = pd.Series({"A1": 0.02, "A2": -0.02, "A3": 0.01, "B1": -0.01, "B2": 0.015, "B3": -0.015})
    beta = pd.Series({t: 1.0 for t in tickers})  # uniform beta -> beta-neutral == net-neutral, no interaction to test here
    current_weights = pd.Series({"A1": -0.1, "A2": 0.1, "A3": 0.0, "B1": 0.0, "B2": 0.0, "B3": 0.0})

    weights = mean_variance_optimize(
        expected_returns=expected_returns,
        covariance=covariance,
        sector_map=sector_map,
        beta=beta,
        max_position_weight=0.3,
        gross_exposure=1.0,
        net_exposure=0.0,
        target_volatility=0.10,
        sector_neutrality_band=0.05,
        beta_neutrality_band=0.05,
        current_weights=current_weights,
        turnover_budget=0.2,
    )
    turnover = float((weights - current_weights.reindex(weights.index, fill_value=0.0)).abs().sum())
    assert turnover <= 0.2 + 1e-4


def test_mean_variance_optimize_raises_on_misaligned_index():
    _, sector_map, expected_returns, beta, covariance = _synthetic_universe()
    with pytest.raises(ValueError):
        mean_variance_optimize(
            expected_returns=expected_returns.iloc[:-1],  # dropped a ticker -> misaligned
            covariance=covariance,
            sector_map=sector_map,
            beta=beta,
            max_position_weight=0.3,
            gross_exposure=1.0,
            net_exposure=0.0,
            target_volatility=0.10,
        )


# --- conviction_tilt_positions ---------------------------------------------------


def test_conviction_tilt_orders_weights_by_conviction():
    scores = pd.Series({"A": 3.0, "B": 1.0, "C": 0.5, "X": -0.5, "Y": -1.0, "Z": -3.0})
    longs, shorts = pd.Index(["A", "B", "C"]), pd.Index(["X", "Y", "Z"])
    sector_map = pd.Series({t: "S1" for t in scores.index})

    weights = conviction_tilt_positions(
        scores, longs, shorts, gross_exposure=1.0, max_position_weight=1.0,
        sector_map=sector_map, max_sector_weight=1.0,
    )

    assert weights["A"] > weights["B"] > weights["C"] > 0
    assert weights["Z"] < weights["Y"] < weights["X"] < 0
    # caps don't bind here (max_position_weight/max_sector_weight == 1.0, gross == 1.0)
    # so each side's raw tilt weights should sum to exactly half the gross budget
    assert abs(weights.loc[longs].sum() - 0.5) < 1e-9
    assert abs(weights.loc[shorts].abs().sum() - 0.5) < 1e-9


def test_tilt_weights_gives_weakest_name_a_nonzero_floor():
    conviction = pd.Series({"A": 100.0, "B": 100.0, "C": 0.0})  # C ties for min, huge spread otherwise
    weights = _tilt_weights(conviction, target_gross=1.0)
    assert weights["C"] > 0  # not starved to ~0 despite being far from A/B
    assert abs(weights.sum() - 1.0) < 1e-9


def test_cap_and_redistribute_positive_converges_when_single_pass_would_still_violate():
    # Total (2.0) exactly equals cap * n_names (0.5 * 4) — the only feasible
    # fully-capped allocation is every name sitting exactly at the cap. A
    # naive single clip-then-renormalize pass overshoots and re-breaches the
    # cap on names that weren't capped in the first pass (see the docstring
    # in construction.py) — this exercises that multi-pass case specifically.
    weights = pd.Series({"A": 0.9, "B": 0.45, "C": 0.45, "D": 0.2})
    capped = _cap_and_redistribute_positive(weights, cap=0.5)
    assert (capped <= 0.5 + 1e-9).all()
    assert abs(capped.sum() - weights.sum()) < 1e-9  # total preserved, nothing lost


def test_cap_and_redistribute_positive_leaves_uncapped_case_untouched():
    weights = pd.Series({"A": 0.1, "B": 0.2, "C": 0.3})
    result = _cap_and_redistribute_positive(weights, cap=0.5)
    pd.testing.assert_series_equal(result, weights)


def test_apply_sector_cap_scales_down_only_overweight_sector():
    weights = pd.Series({"A": 0.15, "B": 0.15, "C": 0.05})
    sector_map = pd.Series({"A": "S1", "B": "S1", "C": "S2"})
    result = _apply_sector_cap(weights, sector_map, cap=0.20)
    # S1 gross = 0.30 > 0.20 -> scaled by 0.20/0.30; S2 gross = 0.05 <= 0.20 -> untouched
    assert abs(result["A"] - 0.15 * (0.20 / 0.30)) < 1e-9
    assert abs(result["B"] - 0.15 * (0.20 / 0.30)) < 1e-9
    assert abs(result["C"] - 0.05) < 1e-9


# --- apply_turnover_budget / apply_beta_neutralization ---------------------------


def test_apply_turnover_budget_scales_trade_when_over_budget():
    target = pd.Series({"A": 0.3, "B": -0.3})
    current = pd.Series({"A": 0.0, "B": 0.0})
    result = apply_turnover_budget(target, current, turnover_budget=0.3)
    # raw turnover would be 0.6; budget 0.3 -> scale = 0.5 -> half the trade executes
    assert abs(result["A"] - 0.15) < 1e-9
    assert abs(result["B"] - (-0.15)) < 1e-9


def test_apply_turnover_budget_is_a_no_op_when_within_budget():
    target = pd.Series({"A": 0.1, "B": -0.1})
    current = pd.Series({"A": 0.0, "B": 0.0})
    result = apply_turnover_budget(target, current, turnover_budget=1.0)
    pd.testing.assert_series_equal(result, target)


def test_apply_beta_neutralization_hits_the_tolerance_edge_exactly():
    weights = pd.Series({"A": 0.5, "B": 0.5})
    beta = pd.Series({"A": 1.0, "B": 1.0})
    result = apply_beta_neutralization(weights, beta, tolerance=0.2)
    portfolio_beta = (result * beta).sum()
    assert abs(portfolio_beta - 0.2) < 1e-9  # started at 1.0, pulled exactly to the band edge


def test_apply_beta_neutralization_is_a_no_op_within_tolerance():
    weights = pd.Series({"A": 0.1, "B": -0.1})
    beta = pd.Series({"A": 1.0, "B": 1.0})
    result = apply_beta_neutralization(weights, beta, tolerance=0.5)  # portfolio beta = 0.0, already inside band
    pd.testing.assert_series_equal(result, weights)


# --- construct_portfolio (mode toggle) --------------------------------------------


def _synthetic_prices(tickers, benchmark, n_days=120, seed=0):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2026-01-01", periods=n_days, freq="B")
    data = {}
    for t in tickers + [benchmark]:
        returns = rng.normal(0, 0.01, n_days)
        data[t] = 100 * np.cumprod(1 + returns)
    return pd.DataFrame(data, index=dates)


def test_construct_portfolio_modes_both_respect_position_cap():
    tickers = [f"T{i}" for i in range(12)]
    sector_map = pd.Series({t: ("S1" if i % 2 == 0 else "S2") for i, t in enumerate(tickers)})
    scores = pd.Series({t: float(6 - i) for i, t in enumerate(tickers)})  # descending scores
    prices = _synthetic_prices(tickers, "SPY")

    common_kwargs = dict(
        scores=scores, sector_map=sector_map, prices=prices, benchmark_ticker="SPY",
        num_longs=4, num_shorts=4, gross_exposure=1.0, net_exposure=0.0,
        max_position_weight=0.15, max_sector_weight=0.5, target_volatility=0.5,
        turnover_budget=None, sector_neutrality_band=0.5, beta_neutrality_band=0.5,
    )
    mvo_weights = construct_portfolio(mode="mvo", **common_kwargs)
    tilt_weights = construct_portfolio(mode="conviction_tilt", **common_kwargs)

    assert (mvo_weights.abs() <= 0.15 + 1e-4).all()
    assert (tilt_weights.abs() <= 0.15 + 1e-9).all()
    assert not mvo_weights.equals(tilt_weights)  # the two modes genuinely differ


def test_construct_portfolio_raises_on_unknown_mode():
    tickers = [f"T{i}" for i in range(4)]
    sector_map = pd.Series({t: "S1" for t in tickers})
    scores = pd.Series({t: float(i) for i, t in enumerate(tickers)})
    prices = _synthetic_prices(tickers, "SPY")
    with pytest.raises(ValueError):
        construct_portfolio(
            scores=scores, sector_map=sector_map, prices=prices, benchmark_ticker="SPY",
            mode="not_a_real_mode", num_longs=2, num_shorts=2,
        )


# --- risk_management additions ---------------------------------------------------


def test_check_beta_neutrality_flags_breach_and_clears_within_tolerance():
    weights = pd.Series({"A": 0.5, "B": 0.5})
    beta = pd.Series({"A": 1.0, "B": 1.0})
    breach = check_beta_neutrality(weights, beta, tolerance=0.5)
    assert breach is not None and abs(breach - 1.0) < 1e-9
    assert check_beta_neutrality(weights, beta, tolerance=1.5) is None


def test_check_turnover_flags_breach_and_clears_within_budget():
    weights = pd.Series({"A": 0.3, "B": -0.3})
    prior = pd.Series({"A": 0.0, "B": 0.0})
    breach = check_turnover(weights, prior, budget=0.3)
    assert breach is not None and abs(breach - 0.6) < 1e-9
    assert check_turnover(weights, prior, budget=1.0) is None
