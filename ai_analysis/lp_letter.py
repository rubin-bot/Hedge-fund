"""LP-style investor letter over the same weekly data as
ai_analysis/weekly_commentary.py, formatted for a less technical audience.
Same call/cache pattern as the other analyzers in this package.
"""

from __future__ import annotations

import json
import sqlite3

from pydantic import BaseModel, Field

from ai_analysis.cache import get_cached, store_cache
from ai_analysis.gemini_client import GeminiClient

ANALYZER_NAME = "lp_letter"
PROMPT_VERSION = "v1"
PORTFOLIO_SENTINEL = "PORTFOLIO"  # see weekly_commentary.py for why this sentinel exists

SYSTEM_PROMPT = """You are writing a short limited-partner-style letter for a SIMULATED
paper-trading research system -- this is a research/backtesting tool, not a
fund with real capital or real LPs. The letter must make that unambiguous
(state plainly that all figures are simulated/paper-trading results, not
real returns) while still reading like a genuine, professional investor
letter in tone: measured, specific about performance and risk, light on
jargon compared to an internal commentary. Ground every claim in the
figures provided; do not invent numbers. Keep it to 3-5 short paragraphs."""


class LPLetter(BaseModel):
    letter: str = Field(description="A 3-5 paragraph LP-style letter, clearly labeled as simulated/paper-trading results.")


class LPLetterAnalyzer:
    def __init__(self, client: GeminiClient | None = None):
        self.client = client or GeminiClient()

    def analyze(
        self,
        conn: sqlite3.Connection,
        period_start: str,
        period_end: str,
        context: dict,
        force_refresh: bool = False,
    ) -> LPLetter:
        cache_key = f"{period_start}_{period_end}"
        if not force_refresh:
            cached = get_cached(conn, ANALYZER_NAME, PORTFOLIO_SENTINEL, cache_key)
            if cached is not None:
                return LPLetter.model_validate(cached)

        user_prompt = _build_prompt(period_start, period_end, context)
        result = self.client.complete_structured(
            system=SYSTEM_PROMPT, user=user_prompt, response_schema=LPLetter,
            # see weekly_commentary.py's analyze() for why this needs a
            # larger ceiling than this package's other analyzers' default
            max_output_tokens=8192,
        )
        store_cache(conn, ANALYZER_NAME, PORTFOLIO_SENTINEL, cache_key, result.model_dump(), self.client.model, PROMPT_VERSION)
        return result


def _build_prompt(period_start: str, period_end: str, context: dict) -> str:
    return (
        f"Period: {period_start} to {period_end}\n\n"
        f"Simulated positions opened/closed this period:\n{json.dumps(context.get('positions_summary', {}), indent=2, default=str)}\n\n"
        f"Risk report:\n{json.dumps(context.get('risk', {}), indent=2, default=str)}\n\n"
        f"Performance attribution:\n{json.dumps(context.get('performance', {}), indent=2, default=str)}\n"
    )
