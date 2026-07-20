"""Gemini-backed weekly commentary and LP-letter endpoints.

Both reuse ai_analysis/cache.py's ai_analysis_cache table (see
WeeklyCommentaryAnalyzer/LPLetterAnalyzer for why), so re-requesting the same
period without `regenerate=true` never re-calls Gemini.
"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Query

import simulation.virtual_ledger as vl
from ai_analysis.cache import get_cached
from ai_analysis.lp_letter import ANALYZER_NAME as LP_LETTER_ANALYZER
from ai_analysis.lp_letter import PORTFOLIO_SENTINEL as LP_LETTER_SENTINEL
from ai_analysis.lp_letter import LPLetterAnalyzer
from ai_analysis.weekly_commentary import ANALYZER_NAME as COMMENTARY_ANALYZER
from ai_analysis.weekly_commentary import PORTFOLIO_SENTINEL as COMMENTARY_SENTINEL
from ai_analysis.weekly_commentary import WeeklyCommentaryAnalyzer
from api import ledger_service
from data.db import connection

router = APIRouter(prefix="/api/ai", tags=["ai"])


def _default_period() -> tuple[str, str]:
    end = date.today()
    start = end - timedelta(days=7)
    return start.isoformat(), end.isoformat()


def _summarize_positions(positions: list[dict], period_start: str, period_end: str) -> dict:
    """Condenses the position lifecycle log into the figures a
    commentary/letter actually needs, filtered to positions opened or
    closed within the period. A live book can accumulate a long history --
    dumping every position ever opened into the Gemini prompt both wastes
    tokens the model needs for a full-length response (this is what
    truncated the first version of this endpoint's output past
    `max_output_tokens`) and buries the signal a human reader would
    actually want: what happened this period, not the whole account history.
    """
    opened = [p for p in positions if period_start <= p["entry_date"] <= period_end]
    closed = [
        p for p in positions
        if p["status"] == "closed" and p["exit_date"] and period_start <= p["exit_date"] <= period_end
    ]

    if not opened and not closed:
        return {"positions_opened": 0, "positions_closed": 0}

    total_realized_pnl = sum(p["realized_pnl"] for p in closed if p["realized_pnl"] is not None)
    ranked = sorted(closed, key=lambda p: p["realized_pnl"] or 0.0)

    return {
        "positions_opened": [
            {"ticker": p["ticker"], "side": p["side"], "cost_basis": p["cost_basis"]} for p in opened
        ],
        "positions_closed": len(closed),
        "total_realized_pnl_usd": total_realized_pnl,
        "biggest_winners_by_realized_pnl_usd": [
            {"ticker": p["ticker"], "side": p["side"], "realized_pnl": p["realized_pnl"]} for p in ranked[-5:][::-1]
        ],
        "biggest_losers_by_realized_pnl_usd": [
            {"ticker": p["ticker"], "side": p["side"], "realized_pnl": p["realized_pnl"]} for p in ranked[:5]
        ],
    }


def _gather_context(period_start: str, period_end: str) -> dict:
    with connection() as conn:
        positions = vl.list_positions(conn)
    risk = ledger_service.build_risk_response()
    performance = ledger_service.get_performance()
    return {
        "positions_summary": _summarize_positions(positions, period_start, period_end),
        "risk": risk,
        "performance": performance,
    }


@router.get("/commentary")
def get_weekly_commentary(
    period_start: str | None = Query(default=None),
    period_end: str | None = Query(default=None),
    regenerate: bool = Query(default=False, description="Force a fresh Gemini call, bypassing the analysis cache."),
):
    start, end = period_start or _default_period()[0], period_end or _default_period()[1]
    analyzer = WeeklyCommentaryAnalyzer()
    with connection() as conn:
        was_cached = not regenerate and get_cached(conn, COMMENTARY_ANALYZER, COMMENTARY_SENTINEL, f"{start}_{end}") is not None
        result = analyzer.analyze(
            conn, period_start=start, period_end=end, context=_gather_context(start, end), force_refresh=regenerate,
        )
    return {
        "generated_at": date.today().isoformat(),
        "model": analyzer.client.model,
        "period_start": start,
        "period_end": end,
        "commentary": result.commentary,
        "key_takeaways": result.key_takeaways,
        "cached": was_cached,
    }


@router.get("/lp-letter")
def get_lp_letter(
    period_start: str | None = Query(default=None),
    period_end: str | None = Query(default=None),
    regenerate: bool = Query(default=False, description="Force a fresh Gemini call, bypassing the analysis cache."),
):
    start, end = period_start or _default_period()[0], period_end or _default_period()[1]
    analyzer = LPLetterAnalyzer()
    with connection() as conn:
        was_cached = not regenerate and get_cached(conn, LP_LETTER_ANALYZER, LP_LETTER_SENTINEL, f"{start}_{end}") is not None
        result = analyzer.analyze(
            conn, period_start=start, period_end=end, context=_gather_context(start, end), force_refresh=regenerate,
        )
    return {
        "generated_at": date.today().isoformat(),
        "model": analyzer.client.model,
        "period_start": start,
        "period_end": end,
        "letter": result.letter,
        "cached": was_cached,
    }
