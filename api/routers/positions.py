"""Execute / close / list positions -- the trade lifecycle
(open -> held -> closed). Also the new Simulated Execution Log data
source, replacing the old fill-log endpoint.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException

import simulation.virtual_ledger as vl
from api.schemas import ExecuteRequest, Position, PositionsResponse
from data.db import connection
from factors.data_loader import load_close_on_or_before

router = APIRouter(prefix="/api/positions", tags=["positions"])


@router.get("", response_model=PositionsResponse)
def list_positions():
    with connection() as conn:
        open_rows = vl.list_positions(conn, status="open")
        closed_rows = vl.list_positions(conn, status="closed")
        marks = vl.get_latest_marks(conn, [row["position_id"] for row in open_rows])

    open_positions = []
    for row in open_rows:
        mark = marks.get(row["position_id"])
        if mark is None:
            open_positions.append(
                Position(**row, latest_price=row["entry_price"], unrealized_pnl=0.0, position_return_pct=0.0)
            )
        else:
            open_positions.append(
                Position(
                    **row,
                    latest_price=mark["price"],
                    unrealized_pnl=mark["unrealized_pnl"],
                    position_return_pct=mark["position_return_pct"],
                    benchmark_return_pct=mark["benchmark_return_pct"],
                    excess_return_pct=mark["excess_return_pct"],
                    mark_date=mark["mark_date"],
                )
            )
    closed_positions = [Position(**row) for row in closed_rows]
    return PositionsResponse(open=open_positions, closed=closed_positions)


@router.post("/execute", response_model=Position, status_code=201)
def execute(body: ExecuteRequest):
    if body.cash_amount <= 0:
        raise HTTPException(status_code=400, detail="cash_amount must be positive")

    today = date.today().isoformat()
    entry_price_series = load_close_on_or_before([body.ticker], today)
    entry_price = entry_price_series.get(body.ticker)
    if entry_price is None:
        raise HTTPException(status_code=422, detail=f"no price data available for {body.ticker}")

    try:
        with connection() as conn:
            result = vl.execute_candidate(
                conn,
                ticker=body.ticker,
                side=body.side,
                cash_amount=body.cash_amount,
                entry_price=float(entry_price),
                entry_date=today,
                composite_score=body.composite_score,
            )
    except vl.InsufficientFundsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except vl.CircuitBreakerHaltedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return Position(
        position_id=result["position_id"],
        ticker=result["ticker"],
        side=result["side"],
        shares=result["shares"],
        entry_price=result["entry_price"],
        entry_date=result["entry_date"],
        cost_basis=result["cost_basis"],
        composite_score_at_entry=body.composite_score,
        status="open",
        created_at=date.today().isoformat(),
        latest_price=result["entry_price"],
        unrealized_pnl=0.0,
        position_return_pct=0.0,
    )


@router.post("/{position_id}/close", response_model=Position)
def close(position_id: str):
    today = date.today().isoformat()

    with connection() as conn:
        existing = vl.get_position(conn, position_id)
        if existing is None:
            raise HTTPException(status_code=404, detail=f"no position with id {position_id!r}")
        ticker = existing["ticker"]

    exit_price_series = load_close_on_or_before([ticker], today)
    exit_price = exit_price_series.get(ticker)
    if exit_price is None:
        raise HTTPException(status_code=422, detail=f"no price data available for {ticker}")

    try:
        with connection() as conn:
            vl.close_position(conn, position_id, exit_price=float(exit_price), exit_date=today)
            updated = vl.get_position(conn, position_id)
    except vl.PositionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return Position(**updated)
