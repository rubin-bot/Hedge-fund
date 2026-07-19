import hashlib
import sqlite3
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel

from ai_analysis.cache import get_cached, store_cache
from ai_analysis.gemini_client import GeminiClient

ANALYZER_NAME = "insider_cluster"
PROMPT_VERSION = "v1"

# Standard SEC Form 4 transaction codes (Table I & II, Item 4). The Python
# classification below is what actually separates routine liquidity events
# (an insider exercising options and immediately selling to cover taxes) from
# real signal (an insider or several insiders buying on the open market) —
# Gemini is used for the narrative verdict on top of this, not to re-derive it.
CODE_LABELS: dict[str, str] = {
    "P": "open_market_buy",
    "S": "open_market_sale",
    "A": "grant_or_award",
    "M": "option_exercise",
    "F": "tax_withholding",
    "G": "gift",
    "C": "conversion",
    "D": "disposition_to_issuer",
    "X": "option_exercise",  # exercise of in-the-money/at-the-money option
}

SYSTEM_PROMPT = """You are an equity research analyst assessing a cluster of SEC Form 4 insider
transactions for a company over a trailing window. You are given the raw transactions plus
pre-computed features (distinct open-market buyers/sellers, dollar totals by category). Open-market
purchases (code P) by multiple distinct insiders in a short window are the strongest signal of
genuine conviction. Option exercises (M/X) followed by same-day or near-day sales (S) to cover taxes
or realize compensation are routine liquidity events, not a bearish signal, even if the dollar amount
is large — do not call these "meaningful selling" unless there is ALSO substantial open-market
selling (code S) unconnected to a recent exercise. Be concise and specific about which insiders and
transactions drove your verdict."""


class InsiderClusterVerdict(BaseModel):
    verdict: Literal["meaningful_cluster_buy", "routine_activity", "meaningful_selling", "mixed_insufficient_signal"]
    distinct_buyers: int
    rationale: str


def classify_transaction(code: str | None) -> str:
    return CODE_LABELS.get((code or "").upper(), "other")


def _cluster_cache_key(transactions: list[dict]) -> str:
    parts = sorted(
        f"{t['accession_number']}|{t['transaction_date']}|{t['transaction_code']}|{t['shares']}"
        for t in transactions
    )
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:24]


def aggregate_features(transactions: list[dict]) -> dict:
    """Deterministic pre-classification the LLM verdict is grounded in —
    computed in Python so the "why" is auditable independent of the model.
    """
    buyers, sellers = set(), set()
    totals: dict[str, float] = {}
    for t in transactions:
        category = classify_transaction(t.get("transaction_code"))
        value = (t.get("shares") or 0) * (t.get("price_per_share") or 0)
        totals[category] = totals.get(category, 0.0) + value
        filer = t.get("filer_name") or "unknown"
        if category == "open_market_buy":
            buyers.add(filer)
        elif category == "open_market_sale":
            sellers.add(filer)
    return {
        "distinct_open_market_buyers": sorted(buyers),
        "distinct_open_market_sellers": sorted(sellers),
        "dollar_totals_by_category": totals,
    }


class InsiderTransactionAnalyzer:
    def __init__(self, client: GeminiClient | None = None):
        self.client = client or GeminiClient()

    def analyze(
        self, conn: sqlite3.Connection, ticker: str, as_of_date: str, window_days: int = 90
    ) -> InsiderClusterVerdict:
        start_date = (date.fromisoformat(as_of_date) - timedelta(days=window_days)).isoformat()
        rows = conn.execute(
            """
            SELECT accession_number, filer_name, is_officer, is_director, officer_title,
                   transaction_date, transaction_code, shares, price_per_share, acquired_disposed
            FROM sec_form4_transactions
            WHERE ticker = ? AND transaction_date BETWEEN ? AND ?
            """,
            (ticker, start_date, as_of_date),
        ).fetchall()
        columns = [
            "accession_number", "filer_name", "is_officer", "is_director", "officer_title",
            "transaction_date", "transaction_code", "shares", "price_per_share", "acquired_disposed",
        ]
        transactions = [dict(zip(columns, row)) for row in rows]

        if not transactions:
            return InsiderClusterVerdict(
                verdict="mixed_insufficient_signal", distinct_buyers=0,
                rationale=f"No Form 4 transactions for {ticker} in the {window_days}-day window ending {as_of_date}.",
            )

        cache_key = _cluster_cache_key(transactions)
        cached = get_cached(conn, ANALYZER_NAME, ticker, cache_key)
        if cached is not None:
            return InsiderClusterVerdict.model_validate(cached)

        features = aggregate_features(transactions)
        user_prompt = (
            f"Ticker: {ticker}\nWindow: {start_date} to {as_of_date}\n\n"
            f"Transactions ({len(transactions)}):\n{transactions}\n\n"
            f"Pre-computed features:\n{features}"
        )
        verdict = self.client.complete_structured(
            system=SYSTEM_PROMPT, user=user_prompt, response_schema=InsiderClusterVerdict
        )
        store_cache(conn, ANALYZER_NAME, ticker, cache_key, verdict.model_dump(), self.client.model, PROMPT_VERSION)
        return verdict
