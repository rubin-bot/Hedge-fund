import pandas as pd


def estimate_sector_factor_loadings(
    returns: pd.DataFrame,
    sector_returns: pd.DataFrame,
    sector_map: pd.Series,
    min_observations: int = 60,
) -> tuple[pd.Series, pd.Series]:
    """Per-ticker beta to its OWN GICS sector's ETF return, and the
    regression residual (specific/idiosyncratic) variance —
    Var(r_i - beta_i * r_sector(i)). This is the single-factor-per-stock
    version of portfolio.risk_models.estimate_beta's Cov/Var regression,
    extended to also return the residual variance decompose_portfolio_risk
    needs (estimate_beta only returns the beta itself).

    Tickers with no mapped sector, or fewer than min_observations overlapping
    days with their sector's ETF, are dropped from both returned Series.
    """
    betas: dict[str, float] = {}
    specific_variances: dict[str, float] = {}
    for ticker in returns.columns:
        sector = sector_map.get(ticker)
        if sector is None or sector not in sector_returns.columns:
            continue
        common = returns[ticker].dropna().index.intersection(sector_returns[sector].dropna().index)
        if len(common) < min_observations:
            continue
        stock_returns = returns.loc[common, ticker]
        factor_returns = sector_returns.loc[common, sector]
        beta = stock_returns.cov(factor_returns) / factor_returns.var()
        residual = stock_returns - beta * factor_returns
        betas[ticker] = beta
        specific_variances[ticker] = residual.var()
    return pd.Series(betas, dtype=float), pd.Series(specific_variances, dtype=float)


def decompose_portfolio_risk(
    weights: pd.Series,
    beta_to_sector: pd.Series,
    specific_variance: pd.Series,
    sector_map: pd.Series,
    sector_factor_covariance: pd.DataFrame,
    specific_target_pct: float = 0.80,
    tolerance: float = 0.10,
) -> dict:
    """Splits total portfolio variance into factor risk (driven by sector
    exposure) and stock-specific risk, using the standard single-factor risk
    model structure Sigma = B F B' + D: B is each stock's loading on its own
    sector factor (zero on every other sector), F is the sector factor
    covariance matrix, and D is the diagonal matrix of specific variances.
    This split is exact given the model's standard assumption that specific
    returns are uncorrelated across stocks and with the sector factors.

    Since portfolio.optimization's MVO mode already enforces sector- and
    beta-neutrality as hard constraints, a well-built market-neutral book
    should have most of its variance come from stock-specific bets, not
    residual sector exposure — this is effectively a risk-weighted sanity
    check that those upstream constraints are doing their job, not just
    balancing weight sums. `flagged` is True when the specific-risk share
    falls outside [specific_target_pct - tolerance, specific_target_pct + tolerance].
    """
    common = weights.index.intersection(beta_to_sector.index).intersection(specific_variance.index)
    w = weights[common]
    beta = beta_to_sector[common]
    spec_var = specific_variance[common]
    sectors = sector_map.reindex(common)

    # B'w aggregated by sector: each sector's net factor exposure across the book.
    sector_exposure = (w * beta).groupby(sectors).sum()
    sector_exposure = sector_exposure.reindex(sector_factor_covariance.index, fill_value=0.0)

    factor_variance = float(sector_exposure.values @ sector_factor_covariance.values @ sector_exposure.values)
    specific_risk_variance = float((w**2 * spec_var).sum())
    total_variance = factor_variance + specific_risk_variance

    if total_variance <= 0:
        return {
            "factor_variance": 0.0,
            "specific_variance": 0.0,
            "total_variance": 0.0,
            "factor_pct": None,
            "specific_pct": None,
            "flagged": False,
            "reason": "zero total variance on the covered tickers — nothing to decompose",
        }

    specific_pct = specific_risk_variance / total_variance
    factor_pct = factor_variance / total_variance
    flagged = abs(specific_pct - specific_target_pct) > tolerance

    return {
        "factor_variance": factor_variance,
        "specific_variance": specific_risk_variance,
        "total_variance": total_variance,
        "factor_pct": factor_pct,
        "specific_pct": specific_pct,
        "flagged": flagged,
        "target_specific_pct": specific_target_pct,
        "tolerance": tolerance,
        "reason": (
            f"specific risk is {specific_pct:.1%} of total variance, outside the "
            f"[{specific_target_pct - tolerance:.0%}, {specific_target_pct + tolerance:.0%}] target band"
            if flagged
            else "within target band"
        ),
    }
