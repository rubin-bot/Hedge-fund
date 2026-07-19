import pandas as pd

# Named historical stress scenarios mapped to per-factor or per-sector shock magnitudes.
HISTORICAL_SCENARIOS: dict[str, dict[str, float]] = {
    "2008_financial_crisis": {},
    "2020_covid_crash": {},
    "2022_rate_shock": {},
}


def apply_shock(weights: pd.Series, shocks: pd.Series) -> float:
    common = weights.index.intersection(shocks.index)
    return float((weights[common] * shocks[common]).sum())


def run_scenario(weights: pd.Series, scenario_name: str, shocks_by_ticker: pd.Series) -> float:
    if scenario_name not in HISTORICAL_SCENARIOS:
        raise ValueError(f"Unknown scenario: {scenario_name}")
    return apply_shock(weights, shocks_by_ticker)
