import sqlite3
from typing import Literal

from pydantic import BaseModel

from ai_analysis.cache import get_cached, store_cache
from ai_analysis.gemini_client import GeminiClient

ANALYZER_NAME = "transcript_sentiment"
PROMPT_VERSION = "v1"

SYSTEM_PROMPT = """You are an equity research analyst assessing sentiment from an earnings call
transcript. Read the transcript excerpt and determine the overall sentiment of management's tone
and guidance, your confidence in that read, and pull out a few direct quotes that best support your
verdict — favor management's prepared remarks and answers to analyst questions on guidance/demand
over boilerplate safe-harbor language. Be concise and specific."""


class TranscriptSentimentVerdict(BaseModel):
    overall_sentiment: Literal["bullish", "neutral", "bearish"]
    confidence: Literal["low", "medium", "high"]
    key_quotes: list[str]
    rationale: str


class TranscriptSentimentAnalyzer:
    def __init__(self, client: GeminiClient | None = None):
        self.client = client or GeminiClient()

    def analyze(
        self, conn: sqlite3.Connection, ticker: str, call_date: str, transcript_text: str
    ) -> TranscriptSentimentVerdict:
        cached = get_cached(conn, ANALYZER_NAME, ticker, call_date)
        if cached is not None:
            return TranscriptSentimentVerdict.model_validate(cached)

        user_prompt = f"Ticker: {ticker}\nCall date: {call_date}\n\nTranscript excerpt:\n{transcript_text}"
        verdict = self.client.complete_structured(
            system=SYSTEM_PROMPT, user=user_prompt, response_schema=TranscriptSentimentVerdict
        )
        store_cache(conn, ANALYZER_NAME, ticker, call_date, verdict.model_dump(), self.client.model, PROMPT_VERSION)
        return verdict
