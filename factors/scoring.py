import numpy as np
import pandas as pd


def zscore(series: pd.Series) -> pd.Series:
    std = series.std(ddof=0)
    if not std or pd.isna(std):
        return pd.Series(0.0, index=series.index)
    return (series - series.mean()) / std


def sector_neutral_zscore(values: pd.Series, sector_map: pd.Series) -> pd.Series:
    """z-score within each GICS sector rather than across the whole universe.
    Utilities structurally trade at low P/E and Tech at high P/E — comparing
    P/E across sectors would just re-derive the sector, not find mispriced
    stocks within it, so every factor is standardized against sector peers
    before it's allowed into the composite blend.
    """
    aligned_sector = sector_map.reindex(values.index)
    frame = pd.DataFrame({"value": values, "sector": aligned_sector})

    def _z(group: pd.Series) -> pd.Series:
        std = group.std(ddof=0)
        if not std or pd.isna(std):
            return pd.Series(0.0, index=group.index)
        return (group - group.mean()) / std

    return frame.groupby("sector")["value"].transform(_z)


def composite_score(
    factor_scores: dict[str, pd.Series | None],
    weights: dict[str, float],
    optional_factors: set[str] = frozenset(),
    min_coverage: float = 0.2,
) -> tuple[pd.Series, dict]:
    """Blend sector-neutral factor scores into one composite, degrading
    gracefully at two levels rather than propagating NaN or crashing:

    1. Factor-level (period): an optional factor (e.g. congressional trading)
       whose non-null coverage across the universe falls below `min_coverage`
       — or that returned None/empty entirely, e.g. because a scraped source
       had nothing for this period — is dropped from the blend for this run,
       and the remaining factors' weights are renormalized to sum to 1.
       Non-optional factors are never dropped this way; a coverage problem
       there is a real data-pipeline issue that should surface, not be hidden.

    2. Ticker-level (every run): a ticker missing just some factors (e.g. no
       Form 4 activity this quarter, nothing unusual) still gets scored from
       whatever factors it does have, with that ticker's own weights
       renormalized — rather than the whole row becoming NaN because one
       input was missing.

    Returns the raw weighted-z composite (NOT rank-transformed) — this
    preserves the variance/covariance structure the crowding detector needs.
    Use rank_scores() on the result if you need a scale-free percentile for
    portfolio construction.
    """
    diagnostics = {"dropped_factors": [], "coverage": {}}

    usable_factors = {}
    for name, series in factor_scores.items():
        if name not in weights:
            continue
        if series is None or series.empty:
            if name in optional_factors:
                diagnostics["dropped_factors"].append(name)
                continue
            raise ValueError(f"Required factor '{name}' returned no data — this is not optional and can't degrade.")
        usable_factors[name] = series

    all_tickers = sorted(set().union(*(s.index for s in usable_factors.values()))) if usable_factors else []
    matrix = pd.DataFrame({name: series.reindex(all_tickers) for name, series in usable_factors.items()})

    for name in list(matrix.columns):
        coverage = matrix[name].notna().mean() if len(matrix) else 0.0
        diagnostics["coverage"][name] = coverage
        if name in optional_factors and coverage < min_coverage:
            matrix = matrix.drop(columns=name)
            diagnostics["dropped_factors"].append(name)

    if matrix.empty:
        return pd.Series(dtype=float), diagnostics

    weight_vec = pd.Series({name: weights[name] for name in matrix.columns})
    available_mask = matrix.notna()
    effective_weights = available_mask.mul(weight_vec, axis=1)
    row_weight_sum = effective_weights.sum(axis=1)

    weighted_values = (matrix.fillna(0) * effective_weights).sum(axis=1)
    composite = weighted_values / row_weight_sum
    composite[row_weight_sum == 0] = np.nan

    return composite, diagnostics


def rank_scores(composite: pd.Series) -> pd.Series:
    """Scale-free percentile rank (0-1) of a composite score — for
    long/short selection, where only relative ordering matters. Don't use
    this output for the crowding detector; it discards the variance
    structure that decomposition needs.
    """
    return composite.rank(pct=True)
