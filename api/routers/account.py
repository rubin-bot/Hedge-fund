"""Virtual cash ledger endpoints: deposit, balance, reset, and end-of-day
reconciliation (both the manual trigger and the auto-on-load path via
`overview`). Thin wrapper over simulation/virtual_ledger.py -- no trading
logic lives here.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException

import simulation.virtual_ledger as vl
from api.schemas import (
    AccountOverviewResponse,
    CashBalance,
    DepositRequest,
    Position,
    PositionsResponse,
    RunEndOfDayResponse,
)
from data.db import connection

router = APIRouter(prefix="/api/account", tags=["account"])


@router.get("/balance", response_model=CashBalance)
def get_balance():
    with connection() as conn:
        return vl.get_balance(conn)


@router.post("/deposit", response_model=CashBalance)
def deposit(body: DepositRequest):
    if body.amount <= 0:
        raise HTTPException(status_code=400, detail="deposit amount must be positive")
    with connection() as conn:
        return vl.deposit(conn, body.amount)


@router.post("/reset")
def reset():
    with connection() as conn:
        vl.reset(conn)
    return {"status": "reset"}


@router.post("/run-end-of-day", response_model=RunEndOfDayResponse)
def run_end_of_day():
    today = date.today().isoformat()
    with connection() as conn:
        return vl.run_end_of_day(conn, today)


@router.get("/overview", response_model=AccountOverviewResponse)
def overview():
    """The Portfolio page's single data source for account state: balance,
    open/closed positions with their latest marks, and circuit-breaker
    status -- auto-reconciling first if today hasn't been marked yet.
    """
    today = date.today().isoformat()
    with connection() as conn:
        latest = vl.latest_snapshot_date(conn)
        if latest != today and vl.has_any_activity(conn):
            vl.run_end_of_day(conn, today)
            latest = today

        balance = vl.get_balance(conn)
        open_rows = vl.list_positions(conn, status="open")
        closed_rows = vl.list_positions(conn, status="closed")
        marks = vl.get_latest_marks(conn, [row["position_id"] for row in open_rows])
        circuit_breaker = vl.circuit_breaker_status(conn)

    open_positions = [_merge_position_with_mark(row, marks.get(row["position_id"])) for row in open_rows]
    closed_positions = [Position(**row) for row in closed_rows]

    return AccountOverviewResponse(
        balance=CashBalance(**balance),
        positions=PositionsResponse(open=open_positions, closed=closed_positions),
        circuit_breaker=circuit_breaker,
        reconciled_today=latest == today,
        latest_snapshot_date=latest,
    )


def _merge_position_with_mark(row: dict, mark: dict | None) -> Position:
    if mark is None:
        # executed since the last EOD run -- no mark yet, show flat at entry
        return Position(**row, latest_price=row["entry_price"], unrealized_pnl=0.0, position_return_pct=0.0)
    return Position(
        **row,
        latest_price=mark["price"],
        unrealized_pnl=mark["unrealized_pnl"],
        position_return_pct=mark["position_return_pct"],
        benchmark_return_pct=mark["benchmark_return_pct"],
        excess_return_pct=mark["excess_return_pct"],
        mark_date=mark["mark_date"],
    )
