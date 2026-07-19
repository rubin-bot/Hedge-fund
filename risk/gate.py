from dataclasses import dataclass, field

import pandas as pd

from config.settings import load_risk_config
from factors.data_loader import SECTOR_ETF_MAP
from portfolio.risk_models import estimate_covariance_matrix
from risk.circuit_breaker import (
    DAILY_LOSS_THRESHOLD,
    DRAWDOWN_THRESHOLD,
    apply_circuit_breaker,
    check_circuit_breaker,
    simulate_portfolio_returns,
)
from risk.correlation_monitor import DEFAULT_CORRELATION_THRESHOLD, monitor_correlations
from risk.decomposition import decompose_portfolio_risk, estimate_sector_factor_loadings
from risk.risk_management import check_beta_neutrality, check_position_limits, check_sector_limits, check_turnover
from risk.stress_testing import _default_fetch as _default_stress_fetch
from risk.stress_testing import run_all_scenarios


@dataclass
class RiskGateVerdict:
    approved: bool  # False iff the circuit breaker tripped -- the one hard-veto condition
    weights: pd.Series  # what to actually use downstream: proposed unchanged if approved, else apply_circuit_breaker()'s output
    veto_reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)  # decomposition/correlation/position/sector/beta/turnover flags -- never gate
    checks: dict = field(default_factory=dict)  # every sub-check's raw result, for the dashboard/audit trail


def evaluate_portfolio(
    proposed_weights: pd.Series,
    previous_weights: pd.Series | None,
    prices: pd.DataFrame,
    sector_etf_prices: pd.DataFrame,
    sector_map: pd.Series,
    beta: pd.Series,
    max_position_weight: float,
    max_sector_weight: float,
    beta_neutrality_tolerance: float,
    turnover_budget: float | None = None,
    daily_loss_threshold: float | None = None,
    drawdown_threshold: float | None = None,
    correlation_threshold: float | None = None,
    specific_risk_target: float | None = None,
    specific_risk_tolerance: float | None = None,
    include_stress_test: bool = True,
    stress_test_fetch_fn=_default_stress_fetch,
) -> RiskGateVerdict:
    """The risk layer's single entry point: the only subsystem here with
    real veto authority is the circuit breaker (the request's "halting"
    language) — everything else (position/sector/beta/turnover checks,
    correlation monitor, risk decomposition, stress test replay) surfaces as
    a warning/informational check but never overrides `proposed_weights`
    ("flagging" language). `prices` must cover every ticker in
    `proposed_weights` (for the circuit breaker's simulated returns and the
    correlation monitor). `sector_etf_prices` (e.g.
    factors.data_loader.load_sector_etf_prices()'s output) is a separate,
    explicit input rather than fetched internally, so this function stays a
    pure function of its inputs — easy to test, and consistent with how
    portfolio.construct_portfolio takes its price panel as an argument
    rather than reaching into the DB itself.
    """
    risk_cfg = load_risk_config()
    daily_loss_threshold = (
        daily_loss_threshold
        if daily_loss_threshold is not None
        else risk_cfg.get("circuit_breaker_daily_loss_threshold", DAILY_LOSS_THRESHOLD)
    )
    drawdown_threshold = (
        drawdown_threshold
        if drawdown_threshold is not None
        else risk_cfg.get("circuit_breaker_drawdown_threshold", DRAWDOWN_THRESHOLD)
    )
    correlation_threshold = (
        correlation_threshold
        if correlation_threshold is not None
        else risk_cfg.get("correlation_threshold", DEFAULT_CORRELATION_THRESHOLD)
    )
    specific_risk_target = (
        specific_risk_target if specific_risk_target is not None else risk_cfg.get("specific_risk_target_pct", 0.80)
    )
    specific_risk_tolerance = (
        specific_risk_tolerance
        if specific_risk_tolerance is not None
        else risk_cfg.get("specific_risk_tolerance", 0.10)
    )

    checks: dict = {}
    warnings: list[str] = []

    # --- circuit breaker: the one hard veto ---
    portfolio_returns = simulate_portfolio_returns(proposed_weights, prices)
    circuit_breaker_result = check_circuit_breaker(portfolio_returns, daily_loss_threshold, drawdown_threshold)
    checks["circuit_breaker"] = circuit_breaker_result

    if circuit_breaker_result["tripped"]:
        final_weights = apply_circuit_breaker(proposed_weights, previous_weights)
        approved = False
        veto_reasons = [circuit_breaker_result["reason"]]
    else:
        final_weights = proposed_weights
        approved = True
        veto_reasons = []

    # --- position/sector/beta/turnover: defense-in-depth reporting; portfolio.optimization's
    # LP already enforces these as hard constraints upstream, so a breach here signals an
    # upstream bug worth surfacing, not a new condition to gate on again ---
    position_breaches = check_position_limits(proposed_weights, max_position_weight)
    if not position_breaches.empty:
        warnings.append(f"position limit breached: {position_breaches.to_dict()}")
    checks["position_limits"] = position_breaches.to_dict()

    sector_breaches = check_sector_limits(proposed_weights, sector_map, max_sector_weight)
    if not sector_breaches.empty:
        warnings.append(f"sector limit breached: {sector_breaches.to_dict()}")
    checks["sector_limits"] = sector_breaches.to_dict()

    beta_breach = check_beta_neutrality(proposed_weights, beta, beta_neutrality_tolerance)
    if beta_breach is not None:
        warnings.append(f"beta neutrality breached: portfolio beta {beta_breach:.3f}")
    checks["beta_neutrality"] = beta_breach

    if previous_weights is not None and turnover_budget is not None:
        turnover_breach = check_turnover(proposed_weights, previous_weights, turnover_budget)
        if turnover_breach is not None:
            warnings.append(f"turnover budget breached: {turnover_breach:.3f}")
        checks["turnover"] = turnover_breach

    # --- correlation monitor ---
    returns = prices.pct_change().dropna(how="all")
    correlated_pairs = monitor_correlations(proposed_weights, returns, correlation_threshold)
    if correlated_pairs:
        warnings.append(f"{len(correlated_pairs)} position pair(s) above {correlation_threshold} correlation")
    checks["correlation_monitor"] = correlated_pairs

    # --- risk decomposition (factor vs. specific) ---
    sector_etf_returns = sector_etf_prices.pct_change().dropna(how="all")
    # sector_map holds sector NAMES (e.g. "Information Technology"); re-key sector ETF
    # returns from ETF ticker to sector name so estimate_sector_factor_loadings can
    # join stocks to their sector's return series via sector_map directly.
    etf_to_sector = {etf: sector for sector, etf in SECTOR_ETF_MAP.items()}
    sector_returns_by_name = sector_etf_returns.rename(columns=etf_to_sector)

    beta_to_sector, specific_variance = estimate_sector_factor_loadings(returns, sector_returns_by_name, sector_map)
    sector_factor_covariance = estimate_covariance_matrix(sector_returns_by_name)
    decomposition_result = decompose_portfolio_risk(
        proposed_weights,
        beta_to_sector,
        specific_variance,
        sector_map,
        sector_factor_covariance,
        specific_target_pct=specific_risk_target,
        tolerance=specific_risk_tolerance,
    )
    if decomposition_result.get("flagged"):
        warnings.append(f"risk decomposition flagged: {decomposition_result['reason']}")
    checks["decomposition"] = decomposition_result

    # --- stress test replay: informational only ---
    if include_stress_test:
        checks["stress_test"] = run_all_scenarios(proposed_weights, sector_map, fetch_fn=stress_test_fetch_fn)

    return RiskGateVerdict(
        approved=approved,
        weights=final_weights,
        veto_reasons=veto_reasons,
        warnings=warnings,
        checks=checks,
    )
