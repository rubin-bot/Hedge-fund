import pandas as pd

DEFAULT_CROWDING_THRESHOLD = 0.4


def detect_factor_crowding(
    factor_scores: dict[str, pd.Series],
    weights: dict[str, float],
    threshold: float = DEFAULT_CROWDING_THRESHOLD,
) -> dict:
    """Flags when one factor is doing most of the work in the composite score
    — either because it's weighted too heavily, or because it's highly
    correlated with other factors that are each individually reinforcing the
    same bet (e.g. momentum and estimate revisions both just tracking recent
    price action).

    Uses the standard Euler/marginal-contribution-to-variance decomposition
    from portfolio risk analysis: for composite = sum(w_i * z_i), the variance
    of the composite splits *exactly* (no residual, no approximation) into
    per-factor contributions via contribution_i = w_i * Cov(z_i, composite).
    This is why it's on complete-case data only (rows with every factor
    present) — with per-row weight renormalization for missing data (as
    composite_score does), "composite = sum(w_i * z_i)" with one fixed set of
    weights isn't exactly true row-by-row, and the decomposition would just be
    wrong, not approximately-fine-wrong.
    """
    columns = [name for name in factor_scores if name in weights and factor_scores[name] is not None]
    matrix = pd.DataFrame({name: factor_scores[name] for name in columns}).dropna()

    if matrix.empty or len(matrix) < 3 or matrix.shape[1] < 2:
        return {
            "crowded": False,
            "dominant_factor": None,
            "contributions": {},
            "reason": "insufficient complete-case data across >=2 factors to decompose variance",
        }

    weight_vec = pd.Series({name: weights[name] for name in matrix.columns})
    covariance_matrix = matrix.cov()
    portfolio_variance = float(weight_vec @ covariance_matrix @ weight_vec)

    if portfolio_variance <= 0:
        return {
            "crowded": False,
            "dominant_factor": None,
            "contributions": {},
            "reason": "composite has zero variance on the complete-case sample",
        }

    marginal_contribution = covariance_matrix.values @ weight_vec.values
    contributions = weight_vec.values * marginal_contribution
    pct_contributions = dict(zip(matrix.columns, contributions / portfolio_variance))

    dominant_factor = max(pct_contributions, key=pct_contributions.get)
    crowded = pct_contributions[dominant_factor] > threshold

    return {
        "crowded": crowded,
        "dominant_factor": dominant_factor if crowded else None,
        "contributions": pct_contributions,
        "threshold": threshold,
        "num_tickers_used": len(matrix),
    }
