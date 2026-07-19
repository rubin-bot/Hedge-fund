import pytest

from ai_analysis.cache import get_cached, store_cache
from ai_analysis.filing_sections import FilingSectionNotFoundError, extract_item_sections
from ai_analysis.insider_transaction_analyzer import (
    InsiderClusterVerdict,
    InsiderTransactionAnalyzer,
    aggregate_features,
    classify_transaction,
)
from ai_analysis.rate_limiter import DailyQuotaExceededError, RateLimiter
from ai_analysis.risk_factor_analyzer import RiskFactorAnalyzer, RiskFactorChangeVerdict
from ai_analysis.transcript_sentiment_analyzer import TranscriptSentimentAnalyzer, TranscriptSentimentVerdict
from data.db import connection, init_db


class _FakeClock:
    """Shared mutable clock so a fake sleep_fn can advance the same time_fn
    the limiter reads from, without a real delay or a new dependency."""

    def __init__(self):
        self.now = 0.0
        self.sleep_calls: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleep_calls.append(seconds)
        self.now += seconds


class _NoCallGeminiClient:
    """Stands in for GeminiClient in cache-hit tests — raises if the
    analyzer ever actually tries to call Gemini, proving the cache was used."""

    model = "no-call-model"

    def complete_structured(self, **kwargs):
        raise AssertionError("Gemini should not be called when a cache entry already exists")

    def complete(self, **kwargs):
        raise AssertionError("Gemini should not be called when a cache entry already exists")


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    init_db(path)
    return path


# --- RateLimiter -------------------------------------------------------------


def test_rate_limiter_sleeps_once_rpm_exceeded():
    clock = _FakeClock()
    limiter = RateLimiter(requests_per_minute=2, requests_per_day=100, time_fn=clock.time, sleep_fn=clock.sleep)

    limiter.acquire()
    limiter.acquire()
    assert clock.sleep_calls == []

    limiter.acquire()  # 3rd call within the same minute should block
    assert clock.sleep_calls == [60.0]


def test_rate_limiter_raises_once_rpd_exceeded():
    clock = _FakeClock()
    limiter = RateLimiter(requests_per_minute=1000, requests_per_day=2, time_fn=clock.time, sleep_fn=clock.sleep)

    limiter.acquire()
    clock.now += 61  # step past the per-minute window so only the daily cap is in play
    limiter.acquire()
    clock.now += 61
    with pytest.raises(DailyQuotaExceededError):
        limiter.acquire()


def test_rate_limiter_allows_calls_again_after_minute_window_passes():
    clock = _FakeClock()
    limiter = RateLimiter(requests_per_minute=1, requests_per_day=100, time_fn=clock.time, sleep_fn=clock.sleep)

    limiter.acquire()
    clock.now += 61
    limiter.acquire()  # should not sleep — outside the 60s window already
    assert clock.sleep_calls == []


# --- cache ---------------------------------------------------------------------


def test_cache_round_trip(db_path):
    with connection(db_path) as conn:
        assert get_cached(conn, "filing_structure", "AAPL", "acc-1") is None
        store_cache(conn, "filing_structure", "AAPL", "acc-1", {"flagged": True}, "gemini-flash-latest", "v1")

    with connection(db_path) as conn:
        cached = get_cached(conn, "filing_structure", "AAPL", "acc-1")
    assert cached == {"flagged": True}


def test_cache_upsert_overwrites(db_path):
    with connection(db_path) as conn:
        store_cache(conn, "filing_structure", "AAPL", "acc-1", {"flagged": True}, "m1", "v1")
        store_cache(conn, "filing_structure", "AAPL", "acc-1", {"flagged": False}, "m1", "v2")

    with connection(db_path) as conn:
        cached = get_cached(conn, "filing_structure", "AAPL", "acc-1")
    assert cached == {"flagged": False}


# --- filing_sections.extract_item_sections -------------------------------------


def _synthetic_10k_text() -> str:
    # A Table-of-Contents block (short, clustered matches) followed by the real
    # headings with substantial body text between them — mirrors real 10-K structure.
    toc = "Item 1. Business 3\nItem 1A. Risk Factors 10\nItem 2. Properties 25\n"
    body = (
        "Item 1. Business\n" + ("Business content. " * 40) + "\n"
        "Item 1A. Risk Factors\n" + ("Risk factor content. " * 40) + "\n"
        "Item 2. Properties\n" + ("Properties content. " * 40)
    )
    return toc + body


def test_extract_item_sections_discards_toc_entries():
    sections = extract_item_sections(_synthetic_10k_text(), "10-K")
    assert set(sections.keys()) == {"1", "1A", "2"}
    assert "Business content." in sections["1"]
    assert "Risk factor content." in sections["1A"]
    assert "Properties content." in sections["2"]
    # TOC's short entries must not have won out over the real, content-bearing headings
    assert len(sections["1A"]) > 500


def test_extract_item_sections_raises_when_no_items_found():
    with pytest.raises(FilingSectionNotFoundError):
        extract_item_sections("This document has no item headings at all.", "10-K")


# --- insider_transaction_analyzer -----------------------------------------------


def test_classify_transaction_covers_standard_codes():
    assert classify_transaction("P") == "open_market_buy"
    assert classify_transaction("S") == "open_market_sale"
    assert classify_transaction("M") == "option_exercise"
    assert classify_transaction("F") == "tax_withholding"
    assert classify_transaction("A") == "grant_or_award"
    assert classify_transaction("G") == "gift"
    assert classify_transaction(None) == "other"
    assert classify_transaction("Z") == "other"


def test_aggregate_features_separates_cluster_buy_from_routine_exercise():
    transactions = [
        {"transaction_code": "P", "shares": 1000, "price_per_share": 50.0, "filer_name": "Alice CEO"},
        {"transaction_code": "P", "shares": 500, "price_per_share": 50.0, "filer_name": "Bob CFO"},
        {"transaction_code": "M", "shares": 2000, "price_per_share": 10.0, "filer_name": "Carol Director"},
        {"transaction_code": "F", "shares": 800, "price_per_share": 60.0, "filer_name": "Carol Director"},
    ]
    features = aggregate_features(transactions)
    assert features["distinct_open_market_buyers"] == ["Alice CEO", "Bob CFO"]
    assert features["distinct_open_market_sellers"] == []
    assert features["dollar_totals_by_category"]["open_market_buy"] == 1000 * 50.0 + 500 * 50.0
    assert "option_exercise" in features["dollar_totals_by_category"]


def test_insider_analyzer_cache_hit_does_not_call_gemini(db_path):
    transactions = [
        {
            "accession_number": "acc-1", "ticker": "AAPL", "cik": "1", "filer_name": "Alice CEO",
            "is_officer": 1, "is_director": 0, "officer_title": "CEO", "transaction_date": "2026-06-01",
            "transaction_code": "P", "shares": 1000, "price_per_share": 50.0, "shares_owned_after": 5000,
            "acquired_disposed": "A", "fetched_at": "2026-06-02",
        }
    ]
    with connection(db_path) as conn:
        conn.executemany(
            """INSERT INTO sec_form4_transactions
               (accession_number, ticker, cik, filer_name, is_director, is_officer, officer_title,
                transaction_date, transaction_code, shares, price_per_share, shares_owned_after,
                acquired_disposed, fetched_at)
               VALUES (:accession_number, :ticker, :cik, :filer_name, :is_director, :is_officer,
                       :officer_title, :transaction_date, :transaction_code, :shares, :price_per_share,
                       :shares_owned_after, :acquired_disposed, :fetched_at)""",
            transactions,
        )
    from ai_analysis.insider_transaction_analyzer import _cluster_cache_key

    with connection(db_path) as conn:
        # Compute the cache key from what SQLite actually returns (shares is a REAL
        # column, so it round-trips as a float even though we inserted an int) rather
        # than from the Python literals above, so this matches what analyze() computes.
        row = conn.execute(
            "SELECT accession_number, transaction_date, transaction_code, shares FROM sec_form4_transactions"
        ).fetchone()
        cache_key = _cluster_cache_key(
            [dict(zip(["accession_number", "transaction_date", "transaction_code", "shares"], row))]
        )
        canned = InsiderClusterVerdict(verdict="meaningful_cluster_buy", distinct_buyers=1, rationale="cached")
        store_cache(conn, "insider_cluster", "AAPL", cache_key, canned.model_dump(), "m1", "v1")

    with connection(db_path) as conn:
        analyzer = InsiderTransactionAnalyzer(client=_NoCallGeminiClient())
        result = analyzer.analyze(conn, "AAPL", as_of_date="2026-06-15", window_days=90)

    assert result.verdict == "meaningful_cluster_buy"
    assert result.rationale == "cached"


def test_insider_analyzer_no_transactions_short_circuits_without_gemini(db_path):
    with connection(db_path) as conn:
        analyzer = InsiderTransactionAnalyzer(client=_NoCallGeminiClient())
        result = analyzer.analyze(conn, "ZZZZ", as_of_date="2026-06-15", window_days=90)
    assert result.verdict == "mixed_insufficient_signal"
    assert result.distinct_buyers == 0


# --- risk_factor_analyzer: no-prior-filing baseline -----------------------------


def test_risk_factor_analyzer_baseline_when_no_prior_filing(db_path):
    with connection(db_path) as conn:
        conn.execute(
            """INSERT INTO sec_filings
               (accession_number, ticker, cik, form_type, filing_date, period_of_report,
                primary_doc_url, local_path, fetched_at)
               VALUES ('acc-1', 'AAPL', '320193', '10-K', '2026-01-01', '2025-12-31',
                       'http://example.com/doc.html', NULL, '2026-01-02')"""
        )

    with connection(db_path) as conn:
        analyzer = RiskFactorAnalyzer(client=_NoCallGeminiClient())
        result = analyzer.analyze(conn, "AAPL", "acc-1")

    assert isinstance(result, RiskFactorChangeVerdict)
    assert result.has_material_changes is False
    assert "No prior filing" in result.summary


# --- transcript_sentiment_analyzer: cache hit ------------------------------------


def test_transcript_analyzer_cache_hit_does_not_call_gemini(db_path):
    canned = TranscriptSentimentVerdict(
        overall_sentiment="bullish", confidence="high", key_quotes=["great quarter"], rationale="cached"
    )
    with connection(db_path) as conn:
        store_cache(conn, "transcript_sentiment", "AAPL", "2026-05-01", canned.model_dump(), "m1", "v1")

    with connection(db_path) as conn:
        analyzer = TranscriptSentimentAnalyzer(client=_NoCallGeminiClient())
        result = analyzer.analyze(conn, "AAPL", "2026-05-01", transcript_text="irrelevant if cached")

    assert result.overall_sentiment == "bullish"
    assert result.rationale == "cached"
