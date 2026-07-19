import numpy as np
import pandas as pd
import pytest

from risk.circuit_breaker import apply_circuit_breaker, check_circuit_breaker
from risk.correlation_monitor import monitor_correlations
from risk.decomposition import decompose_portfolio_risk
from risk.gate import evaluate_portfolio
from risk.stress_testing import HISTORICAL_SCENARIOS, replay_scenario


# --- circuit_breaker.check_circuit_breaker ---------------------------------------


def test_check_circuit_breaker_trips_on_daily_loss():
    returns = pd.Series([0.001, 0.002, -0.03])  # last day -3% breaches -2.5%
    result = check_circuit_breaker(returns, daily_loss_threshold=0.025, drawdown_threshold=0.5)
    assert result["tripped"] is True
    assert result["daily_loss_tripped"] is True
    assert result["drawdown_tripped"] is False


def test_check_circuit_breaker_no_trip_within_thresholds():
    returns = pd.Series([0.001, -0.005, 0.002])
    result = check_circuit_breaker(returns, daily_loss_threshold=0.025, drawdown_threshold=0.5)
    assert result["tripped"] is False


def test_check_circuit_breaker_trips_on_drawdown():
    # value path: 1.0 -> 1.10 (peak) -> 1.045 -> 0.99275; drawdown from the 1.10
    # peak is -9.75%, breaching an 8% threshold, while daily loss (-5%) alone
    # would not breach a much looser 50% daily-loss threshold -- isolates the
    # drawdown condition specifically.
    returns = pd.Series([0.10, -0.05, -0.05])
    result = check_circuit_breaker(returns, daily_loss_threshold=0.5, drawdown_threshold=0.08)
    assert result["drawdown_tripped"] is True
    assert result["daily_loss_tripped"] is False
    assert abs(result["latest_drawdown"] - (-0.0975)) < 1e-6


# --- circuit_breaker.apply_circuit_breaker ---------------------------------------


def test_apply_circuit_breaker_blocks_new_position():
    proposed = pd.Series({"A": 0.05})
    previous = pd.Series({"A": 0.0})
    result = apply_circuit_breaker(proposed, previous)
    assert result["A"] == 0.0


def test_apply_circuit_breaker_allows_reducing_a_long():
    proposed = pd.Series({"A": 0.05})
    previous = pd.Series({"A": 0.10})
    result = apply_circuit_breaker(proposed, previous)
    assert result["A"] == 0.05


def test_apply_circuit_breaker_allows_reducing_a_short():
    proposed = pd.Series({"A": -0.05})
    previous = pd.Series({"A": -0.10})
    result = apply_circuit_breaker(proposed, previous)
    assert result["A"] == -0.05


def test_apply_circuit_breaker_blocks_adding_to_existing_position():
    proposed = pd.Series({"A": 0.15})
    previous = pd.Series({"A": 0.10})
    result = apply_circuit_breaker(proposed, previous)
    assert result["A"] == 0.10


def test_apply_circuit_breaker_blocks_sign_flip():
    proposed = pd.Series({"A": -0.05})
    previous = pd.Series({"A": 0.10})
    result = apply_circuit_breaker(proposed, previous)
    assert result["A"] == 0.10


def test_apply_circuit_breaker_blocks_everything_with_no_prior_weights():
    proposed = pd.Series({"A": 0.05, "B": -0.05})
    result = apply_circuit_breaker(proposed, None)
    assert (result == 0.0).all()


# --- decomposition.decompose_portfolio_risk --------------------------------------


def test_decompose_portfolio_risk_pure_specific_flagged_as_deviating():
    weights = pd.Series({"A": 0.5, "B": -0.5})
    beta_to_sector = pd.Series({"A": 0.0, "B": 0.0})  # zero factor exposure
    specific_variance = pd.Series({"A": 0.0004, "B": 0.0004})
    sector_map = pd.Series({"A": "Tech", "B": "Fin"})
    sector_factor_covariance = pd.DataFrame(
        {"Tech": [0.0001, 0.00002], "Fin": [0.00002, 0.0001]}, index=["Tech", "Fin"]
    )
    result = decompose_portfolio_risk(weights, beta_to_sector, specific_variance, sector_map, sector_factor_covariance)
    assert abs(result["specific_pct"] - 1.0) < 1e-9
    assert abs(result["factor_pct"] - 0.0) < 1e-9
    assert result["flagged"] is True  # 100% specific deviates from the 80% target by more than the 10% tolerance


def test_decompose_portfolio_risk_at_exact_target_not_flagged():
    # Constructed so factor_variance=0.2, specific_variance=0.8, total=1.0 exactly.
    weights = pd.Series({"A": 1.0})
    beta_to_sector = pd.Series({"A": 1.0})
    specific_variance = pd.Series({"A": 0.8})
    sector_map = pd.Series({"A": "Tech"})
    sector_factor_covariance = pd.DataFrame({"Tech": [0.2]}, index=["Tech"])
    result = decompose_portfolio_risk(weights, beta_to_sector, specific_variance, sector_map, sector_factor_covariance)
    assert abs(result["specific_pct"] - 0.80) < 1e-9
    assert abs(result["factor_pct"] - 0.20) < 1e-9
    assert result["flagged"] is False


# --- correlation_monitor.monitor_correlations -------------------------------------


def test_monitor_correlations_flags_highly_correlated_pair():
    dates = pd.date_range("2026-01-01", periods=10, freq="B")
    a = pd.Series(np.linspace(-0.01, 0.01, 10), index=dates)
    returns = pd.DataFrame({"A": a, "B": 2 * a})  # B is an exact linear transform of A -> corr == 1.0
    weights = pd.Series({"A": 0.1, "B": 0.1})
    flagged = monitor_correlations(weights, returns, threshold=0.85)
    assert len(flagged) == 1
    assert flagged[0]["pair"] == ("A", "B")
    assert abs(flagged[0]["correlation"] - 1.0) < 1e-9


def test_monitor_correlations_does_not_flag_orthogonal_pair():
    dates = pd.date_range("2026-01-01", periods=8, freq="B")
    c = pd.Series([1, 1, -1, -1, 1, 1, -1, -1], index=dates, dtype=float)
    d = pd.Series([1, -1, 1, -1, 1, -1, 1, -1], index=dates, dtype=float)  # exactly orthogonal to c -> corr == 0.0
    returns = pd.DataFrame({"C": c, "D": d})
    weights = pd.Series({"C": 0.1, "D": 0.1})
    assert monitor_correlations(weights, returns, threshold=0.85) == []


def test_monitor_correlations_ignores_zero_weight_positions():
    dates = pd.date_range("2026-01-01", periods=10, freq="B")
    a = pd.Series(np.linspace(-0.01, 0.01, 10), index=dates)
    returns = pd.DataFrame({"A": a, "B": 2 * a})
    weights = pd.Series({"A": 0.1, "B": 0.0})  # B not currently held
    assert monitor_correlations(weights, returns, threshold=0.85) == []


# --- stress_testing.replay_scenario (network-free via injected fetch_fn) ---------


def _fake_fetch_factory(price_data: dict[str, tuple[float, float]]):
    """price_data: {ticker: (window_start_price, window_end_price)}."""

    def fetch_fn(tickers, start, end):
        cols = {t: [price_data[t][0], price_data[t][1]] for t in tickers if t in price_data}
        return pd.DataFrame(cols, index=pd.to_datetime(["2020-01-01", "2020-06-01"]))

    return fetch_fn


def test_replay_scenario_computes_weighted_window_return(tmp_path, monkeypatch):
    import risk.stress_testing as st

    monkeypatch.setattr(st, "STRESS_SCENARIO_CACHE_DIR", tmp_path / "stress_scenarios")
    weights = pd.Series({"A": 0.5, "B": -0.5})
    sector_map = pd.Series({"A": "Information Technology", "B": "Financials"})
    fetch_fn = _fake_fetch_factory({"A": (100.0, 120.0), "B": (50.0, 40.0)})  # A +20%, B -20%

    result = replay_scenario(weights, sector_map, "2008_financial_crisis", fetch_fn=fetch_fn)

    # portfolio_return = 0.5*0.20 + (-0.5)*(-0.20) = 0.20
    assert abs(result["portfolio_return"] - 0.20) < 1e-9
    assert abs(result["coverage"] - 1.0) < 1e-9
    assert result["tickers_missing_data"] == []
    assert result["window"] == HISTORICAL_SCENARIOS["2008_financial_crisis"]


def test_replay_scenario_falls_back_to_sector_etf_for_missing_ticker(tmp_path, monkeypatch):
    import risk.stress_testing as st

    monkeypatch.setattr(st, "STRESS_SCENARIO_CACHE_DIR", tmp_path / "stress_scenarios")
    weights = pd.Series({"NEWCO": 1.0})
    sector_map = pd.Series({"NEWCO": "Information Technology"})
    # NEWCO has no price data in this window (e.g. it IPO'd after the crash);
    # only its sector ETF (XLK) does -- the fallback path.
    fetch_fn = _fake_fetch_factory({"XLK": (100.0, 110.0)})

    result = replay_scenario(weights, sector_map, "2020_covid_crash", fetch_fn=fetch_fn)

    assert abs(result["portfolio_return"] - 0.10) < 1e-9
    assert abs(result["coverage"] - 1.0) < 1e-9  # covered via the sector-ETF proxy
    assert result["tickers_missing_data"] == []


def test_replay_scenario_reports_missing_data_when_no_fallback_available(tmp_path, monkeypatch):
    import risk.stress_testing as st

    monkeypatch.setattr(st, "STRESS_SCENARIO_CACHE_DIR", tmp_path / "stress_scenarios")
    weights = pd.Series({"GHOST": 1.0})
    sector_map = pd.Series({"GHOST": "Nonexistent Sector"})  # not in SECTOR_ETF_MAP -> no proxy possible
    fetch_fn = _fake_fetch_factory({})

    result = replay_scenario(weights, sector_map, "2022_rate_hike_selloff", fetch_fn=fetch_fn)

    assert result["tickers_missing_data"] == ["GHOST"]
    assert abs(result["coverage"] - 0.0) < 1e-9
    assert abs(result["portfolio_return"] - 0.0) < 1e-9


def test_replay_scenario_rejects_unknown_scenario_name(tmp_path, monkeypatch):
    import risk.stress_testing as st

    monkeypatch.setattr(st, "STRESS_SCENARIO_CACHE_DIR", tmp_path / "stress_scenarios")
    weights = pd.Series({"A": 1.0})
    sector_map = pd.Series({"A": "Financials"})
    with pytest.raises(ValueError):
        replay_scenario(weights, sector_map, "not_a_real_scenario", fetch_fn=_fake_fetch_factory({}))


# --- gate.evaluate_portfolio (the veto entry point) -------------------------------


def _synthetic_prices(tickers, n_days=90, seed=0, crash_tickers=()):
    # A shock applied uniformly to every ticker nets to ~0 for a market-neutral
    # book (longs and shorts move together and cancel) -- crash_tickers lets a
    # test hit only the LONG legs specifically, producing a genuine portfolio
    # loss rather than an accidentally-hedged no-op.
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2026-01-01", periods=n_days, freq="B")
    data = {}
    for t in tickers:
        daily_returns = rng.normal(0, 0.005, n_days)
        if t in crash_tickers:
            daily_returns[-1] = -0.30
        data[t] = 100 * np.cumprod(1 + daily_returns)
    return pd.DataFrame(data, index=dates)


def _synthetic_gate_inputs(crash: bool):
    tickers = ["A", "B", "C", "D"]
    sector_map = pd.Series({"A": "Information Technology", "B": "Information Technology", "C": "Financials", "D": "Financials"})
    weights = pd.Series({"A": 0.1, "B": -0.1, "C": 0.1, "D": -0.1})  # long A, C; short B, D
    beta = pd.Series({"A": 1.0, "B": 1.0, "C": 0.9, "D": 0.9})
    crash_tickers = ["A", "C"] if crash else ()  # crash only the long legs
    prices = _synthetic_prices(tickers, crash_tickers=crash_tickers)
    sector_etf_prices = _synthetic_prices(["XLK", "XLF"], seed=1)
    return weights, sector_map, beta, prices, sector_etf_prices


def test_evaluate_portfolio_vetoes_and_applies_circuit_breaker_when_tripped():
    weights, sector_map, beta, prices, sector_etf_prices = _synthetic_gate_inputs(crash=True)
    previous_weights = pd.Series({"A": 0.05, "B": -0.05, "C": 0.05, "D": -0.05})

    verdict = evaluate_portfolio(
        proposed_weights=weights,
        previous_weights=previous_weights,
        prices=prices,
        sector_etf_prices=sector_etf_prices,
        sector_map=sector_map,
        beta=beta,
        max_position_weight=0.5,
        max_sector_weight=1.0,
        beta_neutrality_tolerance=1.0,
        daily_loss_threshold=0.025,
        drawdown_threshold=0.5,  # isolate the daily-loss trip specifically
        include_stress_test=False,
    )

    assert verdict.approved is False
    assert len(verdict.veto_reasons) == 1
    # every position moves toward (or stays at) previous_weights, never past it
    for ticker in weights.index:
        assert abs(verdict.weights[ticker]) <= abs(previous_weights[ticker]) + 1e-9


def test_evaluate_portfolio_approves_and_passes_through_when_nothing_trips():
    weights, sector_map, beta, prices, sector_etf_prices = _synthetic_gate_inputs(crash=False)

    verdict = evaluate_portfolio(
        proposed_weights=weights,
        previous_weights=None,
        prices=prices,
        sector_etf_prices=sector_etf_prices,
        sector_map=sector_map,
        beta=beta,
        max_position_weight=0.5,
        max_sector_weight=1.0,
        beta_neutrality_tolerance=1.0,
        daily_loss_threshold=0.5,
        drawdown_threshold=0.5,
        include_stress_test=False,
    )

    assert verdict.approved is True
    assert verdict.veto_reasons == []
    pd.testing.assert_series_equal(verdict.weights, weights)
    assert "decomposition" in verdict.checks
    assert "correlation_monitor" in verdict.checks
