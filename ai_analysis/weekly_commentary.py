"""Weekly analytical commentary over the simulated trade log, risk report,
and performance attribution -- same call/cache pattern as the other four
analyzers in this package (filing_structure, risk_factor_change,
insider_cluster, transcript_sentiment), just not per-ticker.
"""

from __future__ import annotations

import json
import sqlite3

from pydantic import BaseModel, Field

from ai_analysis.cache import get_cached, store_cache
from ai_analysis.gemini_client import GeminiClient

ANALYZER_NAME = "weekly_commentary"
PROMPT_VERSION = "v1"

# ai_analysis_cache is keyed (analyzer, ticker, cache_key) everywhere else in
# this codebase; this analyzer operates on the whole portfolio rather than a
# single ticker, so it reuses that same three-part key with a fixed sentinel
# in the ticker slot instead of adding a bespoke cache table for one analyzer.
PORTFOLIO_SENTINEL = "PORTFOLIO"

SYSTEM_PROMPT = """You are a quantitative long-short equity analyst writing an internal weekly
commentary for the portfolio management team of a SIMULATED paper-trading
research system. No real capital is at risk -- never write as if it is, and
never imply these are live brokerage results. Ground every claim in the
figures provided; do not invent numbers. Be direct about what went right and
wrong this week, reference specific tickers/sectors/scenarios from the data,
and avoid generic filler. Write for a technical, internal audience that
already understands the strategy."""


class WeeklyCommentary(BaseModel):
    commentary: str = Field(description="3-6 paragraph analytical commentary on the week's simulated trading, risk, and performance.")
    key_takeaways: list[str] = Field(description="3-5 short, specific bullet takeaways.")


class WeeklyCommentaryAnalyzer:
    def __init__(self, client: GeminiClient | None = None):
        self.client = client or GeminiClient()

    def analyze(
        self,
        conn: sqlite3.Connection,
        period_start: str,
        period_end: str,
        context: dict,
        force_refresh: bool = False,
    ) -> WeeklyCommentary:
        cache_key = f"{period_start}_{period_end}"
        if not force_refresh:
            cached = get_cached(conn, ANALYZER_NAME, PORTFOLIO_SENTINEL, cache_key)
            if cached is not None:
                return WeeklyCommentary.model_validate(cached)

        user_prompt = _build_prompt(period_start, period_end, context)
        result = self.client.complete_structured(
            system=SYSTEM_PROMPT, user=user_prompt, response_schema=WeeklyCommentary,
            # A 3-6 paragraph commentary plus takeaways routinely runs past
            # this package's other analyzers' short-verdict default (see
            # config.yaml's gemini.max_output_tokens) -- without a larger
            # ceiling here, Gemini's JSON gets cut off mid-string and
            # complete_structured() raises GeminiAnalysisError.
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
