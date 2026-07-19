import importlib

import pandas as pd

MODULES = [
    "config.settings",
    "data.ingestion.base",
    "data.ingestion.fmp_client",
    "data.ingestion.polygon_client",
    "data.ingestion.quiverquant_client",
    "data.ingestion.fred_client",
    "factors.definitions",
    "factors.scoring",
    "ai_analysis.claude_client",
    "ai_analysis.filing_analysis",
    "ai_analysis.transcript_analysis",
    "portfolio.construction",
    "portfolio.optimization",
    "risk.risk_management",
    "risk.stress_testing",
    "simulation.paper_trading",
]


def test_all_modules_import():
    for module_name in MODULES:
        importlib.import_module(module_name)


def test_settings_loads():
    from config.settings import settings

    assert settings.data_provider in ("fmp", "polygon")


def test_composite_score(sample_prices: pd.DataFrame):
    from factors.scoring import composite_score

    factor_scores = {"momentum": sample_prices.iloc[-1] / sample_prices.iloc[0] - 1}
    result = composite_score(factor_scores, weights={"momentum": 1.0})
    assert set(result.index) == set(sample_prices.columns)
