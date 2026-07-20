"""Today's ranked candidate trades (bypasses MVO/`construct_portfolio()`
entirely -- a plain top-N-by-composite-score list for human review, not an
optimized target book) and the on-demand, cached "AI Analysis" panel per
candidate.

The AI Analysis panel reuses the 3 usable existing Gemini analyzers
completely unmodified (FilingStructureAnalyzer, RiskFactorAnalyzer,
InsiderTransactionAnalyzer) plus the 2 existing idempotent backfill
functions that populate their SEC data dependencies on first use
(backfill_sec_filings, backfill_form4) -- see their docstrings/module
comments for why sec_filings/sec_form4_transactions must be populated
before these analyzers can run. TranscriptSentimentAnalyzer is not
callable here: it needs transcript_text supplied directly and no free
full-transcript source exists anywhere in this system (documented gap),
so it's always reported unavailable rather than faked.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query

import simulation.virtual_ledger as vl
from ai_analysis.filing_structure_analyzer import FilingStructureAnalyzer
from ai_analysis.insider_transaction_analyzer import InsiderTransactionAnalyzer
from ai_analysis.risk_factor_analyzer import RiskFactorAnalyzer
from api.engine_service import _num, compute_pipeline
from api.schemas import (
    Candidate,
    CandidateAnalysisResponse,
    CandidatesResponse,
    FactorDriver,
    FilingStructureSection,
    InsiderClusterSection,
    RiskFactorChangeSection,
)
from data.backfill import backfill_form4, backfill_sec_filings
from data.db import connection
from data.universe import get_ticker_to_cik
from portfolio.construction import select_long_short_candidates

router = APIRouter(prefix="/api/candidates", tags=["candidates"])

DEFAULT_NUM_CANDIDATES = 15
INSIDER_WINDOW_DAYS = 90
MAX_FACTOR_DRIVERS = 5
# Caps the on-demand Form 4 fetch to a bounded number of live SEC requests
# -- an active filer's full "recent" window can hold hundreds of Form 4s,
# each requiring its own request; unbounded, first-expand latency for a
# heavily-filed ticker could run to minutes. 20 filings is enough for a
# meaningful cluster-activity read within a ~90-day insider window.
FORM4_FETCH_LIMIT = 20


@router.get("", response_model=CandidatesResponse)
def get_candidates(
    num_longs: int = Query(default=DEFAULT_NUM_CANDIDATES, ge=1, le=100),
    num_shorts: int = Query(default=DEFAULT_NUM_CANDIDATES, ge=1, le=100),
):
    bundle = compute_pipeline()
    scoring = bundle["scoring"]
    composite = scoring["composite_score"].dropna()
    rank = scoring["rank"]
    sector_map = bundle["sector_map"]

    longs_idx, shorts_idx = select_long_short_candidates(composite, num_longs, num_shorts)

    with connection() as conn:
        held_by_ticker = {row["ticker"]: row for row in vl.list_positions(conn, status="open")}

    def build(tickers, side: str) -> list[Candidate]:
        candidates = []
        for ticker in tickers:
            held = held_by_ticker.get(ticker)
            candidates.append(
                Candidate(
                    ticker=ticker,
                    side=side,
                    composite_score=_num(composite.get(ticker)),
                    rank=_num(rank.get(ticker)),
                    sector=sector_map.get(ticker),
                    held_position_id=held["position_id"] if held else None,
                    held_side=held["side"] if held else None,
                )
            )
        return candidates

    longs = sorted(build(longs_idx, "long"), key=lambda c: -(c.composite_score if c.composite_score is not None else -1e9))
    shorts = sorted(build(shorts_idx, "short"), key=lambda c: (c.composite_score if c.composite_score is not None else 1e9))

    return CandidatesResponse(as_of=bundle["as_of"], longs=longs, shorts=shorts)


@router.get("/{ticker}/analysis", response_model=CandidateAnalysisResponse)
def get_candidate_analysis(ticker: str):
    bundle = compute_pipeline()
    if ticker not in bundle["tickers"]:
        raise HTTPException(status_code=404, detail=f"{ticker} is not in the current universe")

    scoring = bundle["scoring"]
    composite_score = _num(scoring["composite_score"].get(ticker))
    factor_drivers = _top_factor_drivers(scoring["factor_scores"], ticker)

    cik = get_ticker_to_cik().get(ticker)
    accession = _ensure_latest_filing(ticker, cik)

    return CandidateAnalysisResponse(
        ticker=ticker,
        composite_score=composite_score,
        factor_drivers=factor_drivers,
        filing_structure=_build_filing_structure_section(ticker, accession),
        risk_factor_change=_build_risk_factor_section(ticker, accession),
        insider_cluster=_build_insider_section(ticker, cik),
    )


def _top_factor_drivers(factor_scores: dict, ticker: str) -> list[FactorDriver]:
    values = []
    for factor_name, series in factor_scores.items():
        if series is None:
            continue
        value = _num(series.get(ticker))
        if value is not None:
            values.append((factor_name, value))
    values.sort(key=lambda item: -abs(item[1]))
    return [FactorDriver(factor=name, value=value) for name, value in values[:MAX_FACTOR_DRIVERS]]


def _latest_filing_accession(ticker: str) -> str | None:
    with connection() as conn:
        row = conn.execute(
            "SELECT accession_number FROM sec_filings WHERE ticker = ? ORDER BY filing_date DESC LIMIT 1",
            (ticker,),
        ).fetchone()
    return row[0] if row else None


def _ensure_latest_filing(ticker: str, cik: str | None) -> str | None:
    """Populates sec_filings for this one ticker on first use (idempotent,
    hits SEC EDGAR live) and returns the most recent 10-K/10-Q accession
    number, or None if unavailable. include_history=False -- this only
    needs the latest filing (and find_prior_filing() needs at most one
    before that), not the ticker's entire multi-year filing history, which
    is what the CLI backfill's default pulls and can take a long time for
    a company with a long filing history.
    """
    if not cik:
        return None
    accession = _latest_filing_accession(ticker)
    if accession is not None:
        return accession
    try:
        backfill_sec_filings([ticker], {ticker: cik}, include_history=False)
    except Exception:
        return None
    return _latest_filing_accession(ticker)


def _build_filing_structure_section(ticker: str, accession: str | None) -> FilingStructureSection:
    if accession is None:
        return FilingStructureSection(
            available=False, unavailable_reason="No 10-K/10-Q on record at SEC EDGAR for this ticker."
        )
    try:
        with connection() as conn:
            verdict = FilingStructureAnalyzer().analyze(conn, ticker, accession)
    except Exception as exc:
        return FilingStructureSection(available=False, unavailable_reason=f"Analysis failed: {exc}")
    return FilingStructureSection(available=True, accession_number=accession, **verdict.model_dump())


def _build_risk_factor_section(ticker: str, accession: str | None) -> RiskFactorChangeSection:
    if accession is None:
        return RiskFactorChangeSection(
            available=False, unavailable_reason="No 10-K/10-Q on record at SEC EDGAR for this ticker."
        )
    try:
        with connection() as conn:
            verdict = RiskFactorAnalyzer().analyze(conn, ticker, accession)
    except Exception as exc:
        return RiskFactorChangeSection(available=False, unavailable_reason=f"Analysis failed: {exc}")
    return RiskFactorChangeSection(available=True, **verdict.model_dump())


def _build_insider_section(ticker: str, cik: str | None) -> InsiderClusterSection:
    if not cik:
        return InsiderClusterSection(available=False, unavailable_reason="No CIK on record for this ticker.")

    with connection() as conn:
        has_form4 = conn.execute(
            "SELECT 1 FROM sec_form4_transactions WHERE ticker = ? LIMIT 1", (ticker,)
        ).fetchone()
    if has_form4 is None:
        try:
            backfill_form4([ticker], {ticker: cik}, include_history=False, limit=FORM4_FETCH_LIMIT)
        except Exception as exc:
            return InsiderClusterSection(available=False, unavailable_reason=f"SEC EDGAR fetch failed: {exc}")

    try:
        with connection() as conn:
            verdict = InsiderTransactionAnalyzer().analyze(
                conn, ticker, as_of_date=date.today().isoformat(), window_days=INSIDER_WINDOW_DAYS
            )
    except Exception as exc:
        return InsiderClusterSection(available=False, unavailable_reason=f"Analysis failed: {exc}")
    return InsiderClusterSection(available=True, **verdict.model_dump())
