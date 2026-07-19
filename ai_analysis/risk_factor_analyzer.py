import sqlite3

from pydantic import BaseModel

from ai_analysis.cache import get_cached, store_cache
from ai_analysis.filing_sections import find_prior_filing, get_risk_factors_section
from ai_analysis.gemini_client import GeminiClient

ANALYZER_NAME = "risk_factor_change"
PROMPT_VERSION = "v1"

SYSTEM_PROMPT = """You are an equity research analyst comparing the Item 1A Risk Factors section of
two consecutive SEC filings from the same company (prior filing, then current filing). Identify:
(1) risks that are genuinely new in the current filing, (2) risks that were removed, (3) existing
risks whose language has intensified (stronger warnings, quantified for the first time, moved
earlier in the section). Ignore pure wording/formatting changes that don't change the substance.
Be concise and specific, citing the relevant text."""


class RiskFactorChangeVerdict(BaseModel):
    has_material_changes: bool
    new_risks: list[str]
    removed_risks: list[str]
    intensified_risks: list[str]
    summary: str


def _baseline_verdict() -> RiskFactorChangeVerdict:
    return RiskFactorChangeVerdict(
        has_material_changes=False,
        new_risks=[],
        removed_risks=[],
        intensified_risks=[],
        summary="No prior filing of this form type on record for this ticker — nothing to compare against.",
    )


class RiskFactorAnalyzer:
    def __init__(self, client: GeminiClient | None = None):
        self.client = client or GeminiClient()

    def analyze(self, conn: sqlite3.Connection, ticker: str, accession_number: str) -> RiskFactorChangeVerdict:
        prior = find_prior_filing(conn, ticker, accession_number)
        cache_key = f"{accession_number}_vs_{prior['accession_number'] if prior else 'none'}"

        cached = get_cached(conn, ANALYZER_NAME, ticker, cache_key)
        if cached is not None:
            return RiskFactorChangeVerdict.model_validate(cached)

        if prior is None:
            # First filing of this form type on record — expected sparsity for an
            # early ticker, not an error. No Gemini call needed for a no-op comparison.
            verdict = _baseline_verdict()
            store_cache(conn, ANALYZER_NAME, ticker, cache_key, verdict.model_dump(), "n/a", PROMPT_VERSION)
            return verdict

        current_text = get_risk_factors_section(conn, ticker, accession_number)
        prior_text = get_risk_factors_section(conn, ticker, prior["accession_number"])

        user_prompt = (
            f"Ticker: {ticker}\n\n"
            f"--- PRIOR FILING Item 1A ({prior['accession_number']}, {prior['filing_date']}) ---\n{prior_text}\n\n"
            f"--- CURRENT FILING Item 1A ({accession_number}) ---\n{current_text}"
        )
        verdict = self.client.complete_structured(
            system=SYSTEM_PROMPT, user=user_prompt, response_schema=RiskFactorChangeVerdict
        )
        store_cache(conn, ANALYZER_NAME, ticker, cache_key, verdict.model_dump(), self.client.model, PROMPT_VERSION)
        return verdict
