import sqlite3
from typing import Literal

from pydantic import BaseModel

from ai_analysis.cache import get_cached, store_cache
from ai_analysis.filing_sections import EXPECTED_10K_ITEMS, EXPECTED_10Q_ITEMS, extract_item_sections, get_filing_text
from ai_analysis.gemini_client import GeminiClient

ANALYZER_NAME = "filing_structure"
PROMPT_VERSION = "v1"

SYSTEM_PROMPT = """You are an equity research analyst reviewing the structure of an SEC 10-K/10-Q
filing for anomalies. You are given a deterministic structural summary (which standard items are
present, missing, or unusually short/long) plus short excerpts. Flag anything a careful analyst
would find notable: missing items that aren't explained by the filing type, sections that look like
boilerplate or are suspiciously terse relative to their normal length, or duplicated/out-of-order
content. Do not flag the known-benign case of a 10-Q reusing item numbers 1-4 across Part I and
Part II — that's standard SEC form structure, not an anomaly. Be concise and specific."""


class FilingStructureVerdict(BaseModel):
    flagged: bool
    severity: Literal["none", "low", "medium", "high"]
    anomalies: list[str]
    rationale: str


class FilingStructureAnalyzer:
    def __init__(self, client: GeminiClient | None = None):
        self.client = client or GeminiClient()

    def analyze(self, conn: sqlite3.Connection, ticker: str, accession_number: str) -> FilingStructureVerdict:
        cached = get_cached(conn, ANALYZER_NAME, ticker, accession_number)
        if cached is not None:
            return FilingStructureVerdict.model_validate(cached)

        row = conn.execute(
            "SELECT form_type FROM sec_filings WHERE accession_number = ?", (accession_number,)
        ).fetchone()
        if row is None:
            raise ValueError(f"No sec_filings row for accession_number={accession_number!r}")
        form_type = row[0]

        text = get_filing_text(conn, ticker, accession_number)
        sections = extract_item_sections(text, form_type)
        expected = EXPECTED_10K_ITEMS if form_type == "10-K" else EXPECTED_10Q_ITEMS
        present = set(sections.keys())
        missing = [item for item in dict.fromkeys(expected) if item not in present]

        summary_lines = [f"Filing: {ticker} {form_type} ({accession_number})"]
        summary_lines.append(f"Expected items: {expected}")
        summary_lines.append(f"Missing items: {missing or 'none'}")
        summary_lines.append("Section lengths (chars):")
        for item, section_text in sections.items():
            summary_lines.append(f"  Item {item}: {len(section_text)} chars")
        summary_lines.append("\nExcerpts (first 500 chars of each section):")
        for item, section_text in sections.items():
            summary_lines.append(f"--- Item {item} ---\n{section_text[:500]}")

        verdict = self.client.complete_structured(
            system=SYSTEM_PROMPT,
            user="\n".join(summary_lines),
            response_schema=FilingStructureVerdict,
        )
        store_cache(
            conn, ANALYZER_NAME, ticker, accession_number, verdict.model_dump(), self.client.model, PROMPT_VERSION
        )
        return verdict
