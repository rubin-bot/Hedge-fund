"""Virtual cash ledger + manual candidate-execution daily loop.

This supersedes PaperTradingEngine (simulation/paper_trading.py) for the
API's "decide and track" flow: the user picks specific candidates and a
dollar amount rather than the system auto-rebalancing to target weights.
paper_trading.py is left untouched -- nothing else in the repo depends on
removing it, and its `paper_trades`/`paper_portfolio_snapshots` tables
just go unused going forward.

Every function here takes an open `sqlite3.Connection` (same convention as
ai_analysis/'s analyzers) -- callers wrap calls in
`with data.db.connection() as conn: ...`, which commits on a clean exit.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone

import pandas as pd

from factors.data_loader import load_close_on_or_before
from risk.circuit_breaker import check_circuit_breaker

RESET_TABLES = (
    "cash_deposits",
    "positions",
    "position_daily_marks",
    "account_daily_snapshots",
    # The old engine's tables are fully superseded by the above -- cleared
    # too so a future session doesn't find stale seeded rows in a table
    # nothing writes to anymore.
    "paper_trades",
    "paper_portfolio_snapshots",
)


class InsufficientFundsError(RuntimeError):
    """Raised when an execute would exceed available free cash."""


class CircuitBreakerHaltedError(RuntimeError):
    """Raised when a new-position Execute is attempted while the circuit
    breaker is tripped. Closing a position is never blocked by this --
    see risk/circuit_breaker.py's "halt entries, allow de-risking" rule,
    which this reuses rather than reimplementing.
    """


class PositionNotFoundError(RuntimeError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


# --- Cash balance: always derived from the ledger, never stored ----------


def get_balance(conn: sqlite3.Connection) -> dict:
    total_deposited = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM cash_deposits").fetchone()[0]
    cash_tied_up = conn.execute(
        "SELECT COALESCE(SUM(cost_basis), 0) FROM positions WHERE status = 'open'"
    ).fetchone()[0]
    realized_pnl = conn.execute(
        "SELECT COALESCE(SUM(realized_pnl), 0) FROM positions WHERE status = 'closed'"
    ).fetchone()[0]
    return {
        "total_deposited": float(total_deposited),
        "free_cash": float(total_deposited - cash_tied_up + realized_pnl),
        "cash_tied_up": float(cash_tied_up),
        "realized_pnl": float(realized_pnl),
    }


def deposit(conn: sqlite3.Connection, amount: float) -> dict:
    if amount <= 0:
        raise ValueError("deposit amount must be positive")
    conn.execute(
        "INSERT INTO cash_deposits (deposit_id, amount, deposited_at) VALUES (?, ?, ?)",
        (_new_id(), amount, _now_iso()),
    )
    return get_balance(conn)


# --- Circuit breaker, built from the account's own P&L history -----------


def circuit_breaker_status(conn: sqlite3.Connection) -> dict | None:
    """None means "not enough snapshot history to evaluate yet" (fewer
    than 2 account_daily_snapshots rows) -- distinct from an evaluated
    "not tripped" result, same as PaperTradingEngine's own convention.

    Built from unrealized_pnl + realized_pnl_cumulative (the account's
    actual investment P&L), NOT total_account_value -- total_account_value
    includes free_cash, so a deposit would register as a fake positive
    "return" and permanently deflate the drawdown-from-peak check via
    cummax(). See the plan doc / this session's design review for why.
    """
    df = pd.read_sql_query(
        "SELECT snapshot_date, unrealized_pnl, realized_pnl_cumulative FROM account_daily_snapshots ORDER BY snapshot_date",
        conn,
    )
    if len(df) < 2:
        return None

    investment_value = df["unrealized_pnl"] + df["realized_pnl_cumulative"]
    returns = investment_value.pct_change()
    # investment_value can be exactly 0 before any P&L exists (deposited but
    # never traded, or every position closed flat) -- pct_change through a
    # zero produces +/-inf, not a real "return"; treat as flat rather than
    # feeding a division artifact into the breaker.
    returns = returns.replace([float("inf"), float("-inf")], 0.0).fillna(0.0)
    return check_circuit_breaker(returns)


# --- Execute / close -------------------------------------------------------


def execute_candidate(
    conn: sqlite3.Connection,
    ticker: str,
    side: str,
    cash_amount: float,
    entry_price: float,
    entry_date: str,
    composite_score: float | None = None,
) -> dict:
    if side not in ("long", "short"):
        raise ValueError(f"side must be 'long' or 'short', got {side!r}")
    if cash_amount <= 0:
        raise ValueError("cash_amount must be positive")
    if entry_price <= 0:
        raise ValueError("entry_price must be positive")

    cb = circuit_breaker_status(conn)
    if cb is not None and cb["tripped"]:
        raise CircuitBreakerHaltedError(f"circuit breaker tripped: {cb['reason']} -- new entries are halted")

    # BEGIN IMMEDIATE acquires SQLite's write lock up front, so the
    # free-cash check below and the INSERT that follows it are atomic
    # against a second concurrent Execute (e.g. a double-click) -- a plain
    # read-then-insert on two connections could otherwise both see
    # sufficient free cash and both proceed, overspending it. Only issued
    # if this connection isn't already mid-transaction (e.g. from an
    # earlier write on a connection a caller is reusing) -- SQLite errors
    # on a nested BEGIN, and the caller's existing transaction already
    # provides the same isolation for this connection either way.
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    try:
        balance = get_balance(conn)
        if cash_amount > balance["free_cash"] + 1e-6:
            raise InsufficientFundsError(
                f"cash_amount {cash_amount:.2f} exceeds free cash {balance['free_cash']:.2f}"
            )
        position_id = _new_id()
        shares = cash_amount / entry_price
        conn.execute(
            """
            INSERT INTO positions (
                position_id, ticker, side, shares, entry_price, entry_date,
                cost_basis, composite_score_at_entry, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)
            """,
            (position_id, ticker, side, shares, entry_price, entry_date, cash_amount, composite_score, _now_iso()),
        )
    except Exception:
        conn.rollback()
        raise

    return {
        "position_id": position_id,
        "ticker": ticker,
        "side": side,
        "shares": shares,
        "entry_price": entry_price,
        "entry_date": entry_date,
        "cost_basis": cash_amount,
    }


def close_position(conn: sqlite3.Connection, position_id: str, exit_price: float, exit_date: str) -> dict:
    if exit_price <= 0:
        raise ValueError("exit_price must be positive")

    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT ticker, side, shares, entry_price, status FROM positions WHERE position_id = ?",
            (position_id,),
        ).fetchone()
        if row is None:
            raise PositionNotFoundError(f"no position with id {position_id!r}")
        ticker, side, shares, entry_price, status = row
        if status != "open":
            raise ValueError(f"position {position_id} is already {status}")

        realized_pnl = shares * (exit_price - entry_price) if side == "long" else shares * (entry_price - exit_price)

        conn.execute(
            """
            UPDATE positions
            SET status = 'closed', exit_price = ?, exit_date = ?, realized_pnl = ?, closed_at = ?
            WHERE position_id = ?
            """,
            (exit_price, exit_date, realized_pnl, _now_iso(), position_id),
        )
    except Exception:
        conn.rollback()
        raise

    return {
        "position_id": position_id,
        "ticker": ticker,
        "side": side,
        "exit_price": exit_price,
        "exit_date": exit_date,
        "realized_pnl": float(realized_pnl),
    }


# --- Reads -----------------------------------------------------------------


def list_positions(conn: sqlite3.Connection, status: str | None = None) -> list[dict]:
    query = "SELECT * FROM positions"
    params: list[str] = []
    if status:
        query += " WHERE status = ?"
        params.append(status)
    query += " ORDER BY created_at DESC"
    return pd.read_sql_query(query, conn, params=params).to_dict(orient="records")


def get_position(conn: sqlite3.Connection, position_id: str) -> dict | None:
    df = pd.read_sql_query("SELECT * FROM positions WHERE position_id = ?", conn, params=[position_id])
    if df.empty:
        return None
    return df.iloc[0].to_dict()


def get_latest_marks(conn: sqlite3.Connection, position_ids: list[str]) -> dict[str, dict]:
    """Each open position's most recent position_daily_marks row, keyed by
    position_id. A position executed since the last EOD run simply won't
    have an entry yet -- callers should fall back to a zero-P&L mark at
    entry_price in that case, not treat a missing key as an error.
    """
    if not position_ids:
        return {}
    placeholders = ",".join("?" * len(position_ids))
    df = pd.read_sql_query(
        f"""
        SELECT m.* FROM position_daily_marks m
        INNER JOIN (
            SELECT position_id, MAX(mark_date) AS mark_date
            FROM position_daily_marks WHERE position_id IN ({placeholders})
            GROUP BY position_id
        ) latest ON m.position_id = latest.position_id AND m.mark_date = latest.mark_date
        """,
        conn,
        params=position_ids,
    )
    return {row["position_id"]: row for row in df.to_dict(orient="records")}


def get_account_snapshots(conn: sqlite3.Connection) -> list[dict]:
    return pd.read_sql_query(
        "SELECT * FROM account_daily_snapshots ORDER BY snapshot_date", conn
    ).to_dict(orient="records")


def latest_snapshot_date(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT MAX(snapshot_date) FROM account_daily_snapshots").fetchone()
    return row[0] if row else None


def has_any_activity(conn: sqlite3.Connection) -> bool:
    """Whether there's anything at all to reconcile -- an empty account
    (no deposits, no positions ever) shouldn't auto-trigger an EOD run.
    """
    deposit_count = conn.execute("SELECT COUNT(*) FROM cash_deposits").fetchone()[0]
    position_count = conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
    return bool(deposit_count or position_count)


# --- End-of-day reconciliation ---------------------------------------------


def run_end_of_day(conn: sqlite3.Connection, as_of_date: str, benchmark_ticker: str = "SPY") -> dict:
    """Marks every OPEN position to its latest available close, records
    its return vs. SPY over its holding period, then writes one
    account_daily_snapshots row for as_of_date. Upserts on (position_id,
    mark_date) / snapshot_date -- safe to call more than once for the
    same date (the manual button and the auto-on-load trigger can both
    fire for "today" without creating duplicate history).

    account_daily_snapshots rows are only ever written here, and only for
    the date passed in -- closing a position never itself touches a past
    snapshot row, which is what keeps this a trustworthy, append-mostly
    returns series for the circuit breaker.
    """
    open_positions = conn.execute(
        "SELECT position_id, ticker, side, shares, entry_price, entry_date, cost_basis FROM positions WHERE status = 'open'"
    ).fetchall()

    unrealized_pnl_total = 0.0
    positions_marked = 0
    for position_id, ticker, side, shares, entry_price, entry_date, cost_basis in open_positions:
        price_series = load_close_on_or_before([ticker], as_of_date)
        price = price_series.get(ticker)
        if price is None or pd.isna(price):
            continue  # no price data for this date yet -- skip rather than fabricate a mark

        unrealized_pnl = shares * (price - entry_price) if side == "long" else shares * (entry_price - price)
        position_return_pct = unrealized_pnl / cost_basis if cost_basis else 0.0

        bench_entry = load_close_on_or_before([benchmark_ticker], entry_date).get(benchmark_ticker)
        bench_now = load_close_on_or_before([benchmark_ticker], as_of_date).get(benchmark_ticker)
        if bench_entry and bench_now and not pd.isna(bench_entry) and not pd.isna(bench_now) and bench_entry != 0:
            benchmark_return_pct = (bench_now / bench_entry) - 1.0
        else:
            benchmark_return_pct = 0.0

        conn.execute(
            """
            INSERT INTO position_daily_marks
                (position_id, mark_date, price, unrealized_pnl, position_return_pct, benchmark_return_pct, excess_return_pct)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (position_id, mark_date) DO UPDATE SET
                price = excluded.price, unrealized_pnl = excluded.unrealized_pnl,
                position_return_pct = excluded.position_return_pct,
                benchmark_return_pct = excluded.benchmark_return_pct,
                excess_return_pct = excluded.excess_return_pct
            """,
            (
                position_id, as_of_date, float(price), unrealized_pnl, position_return_pct,
                benchmark_return_pct, position_return_pct - benchmark_return_pct,
            ),
        )
        unrealized_pnl_total += unrealized_pnl
        positions_marked += 1

    balance = get_balance(conn)
    total_account_value = balance["free_cash"] + balance["cash_tied_up"] + unrealized_pnl_total

    conn.execute(
        """
        INSERT INTO account_daily_snapshots
            (snapshot_date, free_cash, cash_tied_up, unrealized_pnl, realized_pnl_cumulative, total_account_value, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (snapshot_date) DO UPDATE SET
            free_cash = excluded.free_cash, cash_tied_up = excluded.cash_tied_up,
            unrealized_pnl = excluded.unrealized_pnl, realized_pnl_cumulative = excluded.realized_pnl_cumulative,
            total_account_value = excluded.total_account_value
        """,
        (
            as_of_date, balance["free_cash"], balance["cash_tied_up"], unrealized_pnl_total,
            balance["realized_pnl"], total_account_value, _now_iso(),
        ),
    )

    return {
        "as_of_date": as_of_date,
        "positions_marked": positions_marked,
        "unrealized_pnl": unrealized_pnl_total,
        "total_account_value": total_account_value,
    }


# --- Reset -------------------------------------------------------------------


def reset(conn: sqlite3.Connection) -> None:
    for table in RESET_TABLES:
        conn.execute(f"DELETE FROM {table}")
