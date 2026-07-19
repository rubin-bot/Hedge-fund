import pandas as pd

from config.settings import load_portfolio_config, load_risk_config, load_simulation_config
from portfolio.optimization import mean_variance_optimize
from portfolio.risk_models import estimate_beta, estimate_covariance_matrix


def select_long_short_candidates(scores: pd.Series, num_longs: int, num_shorts: int) -> tuple[pd.Index, pd.Index]:
    ranked = scores.sort_values(ascending=False)
    longs = ranked.head(num_longs).index
    shorts = ranked.tail(num_shorts).index
    return longs, shorts


def equal_weight_positions(longs: pd.Index, shorts: pd.Index, gross_exposure: float) -> pd.Series:
    long_weight = gross_exposure / 2 / len(longs)
    short_weight = -gross_exposure / 2 / len(shorts)
    weights = pd.Series(long_weight, index=longs)
    weights = pd.concat([weights, pd.Series(short_weight, index=shorts)])
    return weights


def _tilt_weights(conviction: pd.Series, target_gross: float) -> pd.Series:
    """conviction: higher = more conviction, must be usable comparatively
    within one bucket (already the right sign — see conviction_tilt_positions).
    Returns positive weights summing to target_gross.
    """
    shifted = conviction - conviction.min()
    # Floor every name's tilt above zero so the weakest-conviction name in the
    # bucket still gets a small allocation — pure proportional-to-shifted-score
    # weighting would give it ~0 whenever scores cluster away from the bucket
    # minimum, which is "ignore the tail" rather than "tilt towards the top".
    floor = shifted.mean() * 0.1 + 1e-9
    tilted = shifted + floor
    return tilted / tilted.sum() * target_gross


def _cap_and_redistribute_positive(weights: pd.Series, cap: float) -> pd.Series:
    """All weights must be >=0. Iteratively clips any weight over `cap` down to
    `cap` and redistributes the freed-up total proportionally among the
    not-yet-capped names, repeating until nothing is over cap (or every name
    is capped, in which case the target sum can't be reached and the total
    stays below it) — the standard "water-filling" algorithm for
    capacity-constrained proportional allocation.

    A single clip-then-renormalize pass isn't enough: redistributing evenly
    can push a previously-fine name over the cap too. It's critical that once
    a name is capped it's *permanently* excluded from the redistribution pool
    (tracked via `capped` below) rather than re-checked as "weights > cap"
    each pass — a name sitting exactly at `cap` would otherwise look
    "not over" and re-enter the pool, receive more weight, breach the cap
    again, and oscillate instead of converging. With capped names locked out
    for good, each pass permanently caps at least one more name, so this
    terminates in at most len(weights) iterations.
    """
    weights = weights.astype(float).copy()
    capped = pd.Series(False, index=weights.index)
    for _ in range(len(weights)):
        newly_over = (weights > cap) & ~capped
        if not newly_over.any():
            break
        excess = (weights[newly_over] - cap).sum()
        weights[newly_over] = cap
        capped |= newly_over
        pool = weights[~capped]
        pool_sum = pool.sum()
        if pool_sum <= 0:
            break  # every name is capped — nothing left to redistribute into
        weights[~capped] = pool + excess * (pool / pool_sum)
    return weights


def _apply_sector_cap(weights: pd.Series, sector_map: pd.Series, cap: float) -> pd.Series:
    """Scales down (preserving sign and relative size) any sector whose gross
    exposure sum(|w_i|) exceeds `cap`. Sectors are disjoint, so — unlike the
    position cap above — scaling one sector never pushes another over its own
    cap, so a single pass suffices; no water-filling needed here. This can
    leave total gross exposure below the target if caps bind hard — an
    intentional simplification for this "simpler baseline" mode; MVO mode
    satisfies gross exposure and sector caps simultaneously via the LP.
    """
    weights = weights.copy()
    sectors = sector_map.reindex(weights.index)
    for sector in sectors.dropna().unique():
        mask = (sectors == sector).values
        gross = weights[mask].abs().sum()
        if gross > cap:
            weights[mask] = weights[mask] * (cap / gross)
    return weights


def conviction_tilt_positions(
    scores: pd.Series,
    longs: pd.Index,
    shorts: pd.Index,
    gross_exposure: float,
    max_position_weight: float,
    sector_map: pd.Series,
    max_sector_weight: float,
) -> pd.Series:
    """Simpler baseline vs. mean_variance_optimize: weight each candidate
    proportional to its composite-score conviction (within the long bucket
    and short bucket separately), then apply the same position and sector
    caps MVO mode respects — but sequentially (position cap, then sector
    cap) via closed-form/iterative scaling rather than a solver, so unlike
    MVO it doesn't guarantee hitting gross_exposure exactly if caps bind hard.
    """
    long_conviction = scores.loc[longs]
    short_conviction = -scores.loc[shorts]  # more negative composite score = higher short conviction

    long_weights = _tilt_weights(long_conviction, gross_exposure / 2)
    short_magnitude = _tilt_weights(short_conviction, gross_exposure / 2)

    long_weights = _cap_and_redistribute_positive(long_weights, max_position_weight)
    short_magnitude = _cap_and_redistribute_positive(short_magnitude, max_position_weight)

    weights = pd.concat([long_weights, -short_magnitude])
    return _apply_sector_cap(weights, sector_map, max_sector_weight)


def apply_turnover_budget(target_weights: pd.Series, current_weights: pd.Series, turnover_budget: float) -> pd.Series:
    all_tickers = target_weights.index.union(current_weights.index)
    target = target_weights.reindex(all_tickers, fill_value=0.0)
    current = current_weights.reindex(all_tickers, fill_value=0.0)
    trade = target - current
    turnover = trade.abs().sum()
    if turnover <= turnover_budget or turnover == 0:
        return target
    # Scale the TRADE (not the weights) down uniformly, so every name moves
    # partway toward its target — preserves the direction of every trade, just
    # executes less of it, rather than arbitrarily prioritizing some names'
    # full trade over others.
    scale = turnover_budget / turnover
    return current + trade * scale


def apply_beta_neutralization(weights: pd.Series, beta: pd.Series, tolerance: float) -> pd.Series:
    aligned_beta = beta.reindex(weights.index)
    if aligned_beta.isna().any():
        raise ValueError("beta is missing for tickers in weights — can't beta-neutralize")

    portfolio_beta = float((weights * aligned_beta).sum())
    if abs(portfolio_beta) <= tolerance:
        return weights

    # Portfolio beta is exactly linear in a single tilt parameter k, where
    # every position is scaled by (1 - k*beta_i):
    #   pb(k) = sum(w_i*(1-k*beta_i)*beta_i) = pb(0) - k*sum(w_i*beta_i^2)
    # Solving pb(k) = target directly (target = the nearest edge of the
    # tolerance band, so this pushes exactly as far as needed and no further)
    # needs no numerical solver — the relationship is exact, not approximate.
    target = tolerance if portfolio_beta > 0 else -tolerance
    denom = float((weights * aligned_beta**2).sum())
    if denom == 0:
        return weights  # no beta exposure left to trim against
    k = (portfolio_beta - target) / denom
    return weights * (1 - k * aligned_beta)


def construct_portfolio(
    scores: pd.Series,
    sector_map: pd.Series,
    prices: pd.DataFrame,
    benchmark_ticker: str | None = None,
    current_weights: pd.Series | None = None,
    mode: str | None = None,
    num_longs: int | None = None,
    num_shorts: int | None = None,
    gross_exposure: float | None = None,
    net_exposure: float | None = None,
    max_position_weight: float | None = None,
    max_sector_weight: float | None = None,
    target_volatility: float | None = None,
    turnover_budget: float | None = None,
    sector_neutrality_band: float | None = None,
    beta_neutrality_band: float | None = None,
    covariance_shrinkage: float | None = None,
    slippage_bps: float | None = None,
    commission_bps: float | None = None,
) -> pd.Series:
    """The config-toggle entry point: builds a target weight vector from
    composite scores using either mean-variance optimization or the simpler
    conviction-tilt baseline, picked by `mode` (explicit arg > config.yaml's
    `portfolio.mode` > "mvo" default — same precedence pattern as
    ScoringEngine/GeminiClient elsewhere in this codebase).

    `prices` must be a wide close-price panel (data.factors.data_loader.
    load_prices_wide's output) covering every candidate ticker plus
    `benchmark_ticker`, needed for MVO's covariance/beta estimation.
    """
    portfolio_cfg = load_portfolio_config()
    risk_cfg = load_risk_config()
    simulation_cfg = load_simulation_config()

    mode = mode or portfolio_cfg.get("mode", "mvo")
    num_longs = num_longs or portfolio_cfg.get("num_longs", 40)
    num_shorts = num_shorts or portfolio_cfg.get("num_shorts", 40)
    gross_exposure = gross_exposure if gross_exposure is not None else portfolio_cfg.get("gross_exposure", 2.0)
    net_exposure = net_exposure if net_exposure is not None else portfolio_cfg.get("net_exposure", 0.0)
    max_position_weight = max_position_weight or portfolio_cfg.get("max_position_weight", 0.03)
    max_sector_weight = max_sector_weight or portfolio_cfg.get("max_sector_weight", 0.20)
    turnover_budget = turnover_budget if turnover_budget is not None else portfolio_cfg.get("turnover_budget")
    sector_neutrality_band = sector_neutrality_band or portfolio_cfg.get("sector_neutrality_band", 0.02)
    beta_neutrality_band = beta_neutrality_band or portfolio_cfg.get("beta_neutrality_band", 0.10)
    benchmark_ticker = benchmark_ticker or portfolio_cfg.get("benchmark_ticker", "SPY")
    covariance_shrinkage = covariance_shrinkage if covariance_shrinkage is not None else portfolio_cfg.get(
        "covariance_shrinkage", 0.20
    )
    # target_volatility isn't duplicated in portfolio: config — it's already
    # the semantically-correct value in risk.max_portfolio_volatility, and
    # duplicating it would let the two drift apart.
    target_volatility = target_volatility or risk_cfg.get("max_portfolio_volatility", 0.15)
    slippage_bps = slippage_bps if slippage_bps is not None else simulation_cfg.get("slippage_bps", 5.0)
    commission_bps = commission_bps if commission_bps is not None else simulation_cfg.get("commission_bps", 1.0)

    longs, shorts = select_long_short_candidates(scores, num_longs, num_shorts)

    if mode == "conviction_tilt":
        weights = conviction_tilt_positions(
            scores, longs, shorts, gross_exposure, max_position_weight, sector_map, max_sector_weight
        )
        returns = prices[[benchmark_ticker] + [t for t in weights.index if t in prices.columns]].pct_change().dropna(
            how="all"
        )
        beta = estimate_beta(returns.drop(columns=[benchmark_ticker]), returns[benchmark_ticker])
        beta = beta.reindex(weights.index).fillna(0.0)  # untradeable/thin-history names get treated as beta-0
        weights = apply_beta_neutralization(weights, beta, beta_neutrality_band)
        if current_weights is not None and turnover_budget is not None:
            weights = apply_turnover_budget(weights, current_weights, turnover_budget)
        return weights

    if mode == "mvo":
        candidates = longs.union(shorts)
        universe = candidates.union(current_weights.index) if current_weights is not None else candidates
        universe = [t for t in universe if t in prices.columns]

        returns = prices[universe + [benchmark_ticker]].pct_change().dropna(how="all")
        stock_returns = returns[universe]
        covariance = estimate_covariance_matrix(stock_returns, shrinkage=covariance_shrinkage)
        beta = estimate_beta(stock_returns, returns[benchmark_ticker]).reindex(universe).fillna(0.0)

        expected_returns = scores.reindex(universe).fillna(0.0)  # not a candidate this period -> no expected edge
        sectors = sector_map.reindex(universe)

        return mean_variance_optimize(
            expected_returns=expected_returns,
            covariance=covariance.loc[universe, universe],
            sector_map=sectors,
            beta=beta,
            max_position_weight=max_position_weight,
            gross_exposure=gross_exposure,
            net_exposure=net_exposure,
            target_volatility=target_volatility,
            sector_neutrality_band=sector_neutrality_band,
            beta_neutrality_band=beta_neutrality_band,
            current_weights=current_weights,
            turnover_budget=turnover_budget,
            slippage_bps=slippage_bps,
            commission_bps=commission_bps,
        )

    raise ValueError(f"Unknown portfolio mode {mode!r} — expected 'mvo' or 'conviction_tilt'")
