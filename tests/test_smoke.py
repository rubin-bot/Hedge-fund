import importlib

import pandas as pd

MODULES = [
    "config.settings",
    "data.db",
    "data.universe",
    "data.backfill",
    "data.daily_sync",
    "data.ingestion.base",
    "data.ingestion.yfinance_client",
    "data.ingestion.fmp_client",
    "data.ingestion.fundamentals_normalizer",
    "data.ingestion.sec_edgar_client",
    "data.ingestion.quiverquant_client",
    "data.ingestion.fred_client",
    "data.ingestion.finra_short_interest",
    "data.ingestion.analyst_estimates",
    "data.ingestion.congressional.base",
    "data.ingestion.congressional.house_clerk",
    "data.ingestion.congressional.senate_efd",
    "factors.data_loader",
    "factors.scoring",
    "factors.crowding",
    "factors.momentum",
    "factors.value",
    "factors.quality",
    "factors.growth",
    "factors.estimate_revisions",
    "factors.insider_activity",
    "factors.institutional_flow",
    "factors.congressional",
    "factors.macro_regime",
    "factors.engine",
    "ai_analysis.gemini_client",
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

    assert settings.google_api_key is not None


def test_composite_score(sample_prices: pd.DataFrame):
    from factors.scoring import composite_score

    factor_scores = {"momentum": sample_prices.iloc[-1] / sample_prices.iloc[0] - 1}
    result, _diagnostics = composite_score(factor_scores, weights={"momentum": 1.0})
    assert set(result.index) == set(sample_prices.columns)
