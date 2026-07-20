import pandas as pd
import pytest

import simulation.virtual_ledger as vl
from data.db import connection, init_db


@pytest.fixture()
def db_path(tmp_path):
    path = tmp_path / "test.db"
    init_db(path)
    return path


@pytest.fixture()
def fake_prices(monkeypatch):
    """simulation.virtual_ledger.load_close_on_or_before is imported from
    factors/data_loader.py, which always reads settings.db_path (the real
    project DB, not the tmp_path test DB these tests use) -- there's no
    db_path parameter to redirect it. Monkeypatching it here is what keeps
    these tests isolated from whatever's actually in the live research.db,
    same pattern the existing api-layer tests already use for this exact
    problem (see tests/test_api.py's git history / risk tests).
    """
    price_table: dict[tuple[str, str], float] = {}

    def _fake(tickers, as_of_date):
        return pd.Series({t: price_table[(t, as_of_date)] for t in tickers if (t, as_of_date) in price_table})

    monkeypatch.setattr("simulation.virtual_ledger.load_close_on_or_before", _fake)
    return price_table


# --- deposit / balance -------------------------------------------------------------


def test_deposit_increases_free_cash(db_path):
    with connection(db_path) as conn:
        balance = vl.deposit(conn, 50_000)
    assert balance == {"total_deposited": 50_000.0, "free_cash": 50_000.0, "cash_tied_up": 0.0, "realized_pnl": 0.0}


def test_deposit_rejects_non_positive_amount(db_path):
    with connection(db_path) as conn:
        with pytest.raises(ValueError):
            vl.deposit(conn, 0)


# --- execute / overspend guard ------------------------------------------------------


def test_execute_deducts_cost_basis_from_free_cash(db_path):
    with connection(db_path) as conn:
        vl.deposit(conn, 10_000)
        position = vl.execute_candidate(conn, "AAPL", "long", 4_000, entry_price=200.0, entry_date="2026-01-02")

    assert position["shares"] == pytest.approx(20.0)
    with connection(db_path) as conn:
        balance = vl.get_balance(conn)
    assert balance["free_cash"] == pytest.approx(6_000.0)
    assert balance["cash_tied_up"] == pytest.approx(4_000.0)


def test_execute_rejects_cash_amount_over_free_cash(db_path):
    with connection(db_path) as conn:
        vl.deposit(conn, 1_000)

    # A separate connection/transaction for the failing execute -- so its
    # internal rollback can't undo the already-committed deposit above,
    # matching how a real request never shares a transaction with a prior one.
    with connection(db_path) as conn:
        with pytest.raises(vl.InsufficientFundsError):
            vl.execute_candidate(conn, "AAPL", "long", 5_000, entry_price=200.0, entry_date="2026-01-02")

    with connection(db_path) as conn:
        assert vl.get_balance(conn)["free_cash"] == pytest.approx(1_000.0)
        assert vl.list_positions(conn) == []


def test_execute_rejects_invalid_side(db_path):
    with connection(db_path) as conn:
        vl.deposit(conn, 10_000)
        with pytest.raises(ValueError):
            vl.execute_candidate(conn, "AAPL", "sideways", 1_000, entry_price=100.0, entry_date="2026-01-02")


# --- close -------------------------------------------------------------------------


def test_close_long_position_realizes_gain_into_free_cash(db_path):
    with connection(db_path) as conn:
        vl.deposit(conn, 10_000)
        position = vl.execute_candidate(conn, "AAPL", "long", 4_000, entry_price=200.0, entry_date="2026-01-02")
        closed = vl.close_position(conn, position["position_id"], exit_price=220.0, exit_date="2026-01-05")

    # 20 shares * (220 - 200) = $400 gain
    assert closed["realized_pnl"] == pytest.approx(400.0)
    with connection(db_path) as conn:
        balance = vl.get_balance(conn)
    assert balance["cash_tied_up"] == pytest.approx(0.0)
    assert balance["realized_pnl"] == pytest.approx(400.0)
    assert balance["free_cash"] == pytest.approx(10_400.0)  # full 10,000 deposit + the 400 gain


def test_close_short_position_realizes_loss_on_price_increase(db_path):
    with connection(db_path) as conn:
        vl.deposit(conn, 10_000)
        position = vl.execute_candidate(conn, "TSLA", "short", 3_000, entry_price=100.0, entry_date="2026-01-02")
        closed = vl.close_position(conn, position["position_id"], exit_price=110.0, exit_date="2026-01-05")

    # 30 shares short * (100 - 110) = -$300 (price rose against the short)
    assert closed["realized_pnl"] == pytest.approx(-300.0)
    with connection(db_path) as conn:
        assert vl.get_balance(conn)["free_cash"] == pytest.approx(9_700.0)


def test_close_already_closed_position_raises(db_path):
    with connection(db_path) as conn:
        vl.deposit(conn, 10_000)
        position = vl.execute_candidate(conn, "AAPL", "long", 4_000, entry_price=200.0, entry_date="2026-01-02")
        vl.close_position(conn, position["position_id"], exit_price=210.0, exit_date="2026-01-05")
        with pytest.raises(ValueError):
            vl.close_position(conn, position["position_id"], exit_price=210.0, exit_date="2026-01-06")


def test_close_unknown_position_raises(db_path):
    with connection(db_path) as conn:
        with pytest.raises(vl.PositionNotFoundError):
            vl.close_position(conn, "does-not-exist", exit_price=1.0, exit_date="2026-01-02")


# --- end-of-day reconciliation -------------------------------------------------------


def test_run_end_of_day_marks_open_positions_and_writes_snapshot(db_path, fake_prices):
    fake_prices[("AAPL", "2026-01-05")] = 220.0
    fake_prices[("SPY", "2026-01-02")] = 500.0
    fake_prices[("SPY", "2026-01-05")] = 510.0

    with connection(db_path) as conn:
        vl.deposit(conn, 10_000)
        vl.execute_candidate(conn, "AAPL", "long", 4_000, entry_price=200.0, entry_date="2026-01-02")

    with connection(db_path) as conn:
        result = vl.run_end_of_day(conn, "2026-01-05")

    assert result["positions_marked"] == 1
    # 20 shares * (220 - 200) = $400 unrealized gain
    assert result["unrealized_pnl"] == pytest.approx(400.0)
    assert result["total_account_value"] == pytest.approx(10_400.0)  # 6,000 free + 4,000 tied up + 400 gain

    with connection(db_path) as conn:
        snapshots = vl.get_account_snapshots(conn)
    assert len(snapshots) == 1
    assert snapshots[0]["snapshot_date"] == "2026-01-05"


def test_run_end_of_day_is_idempotent_for_the_same_date(db_path, fake_prices):
    fake_prices[("AAPL", "2026-01-02")] = 200.0
    fake_prices[("SPY", "2026-01-02")] = 500.0

    with connection(db_path) as conn:
        vl.deposit(conn, 10_000)
        vl.execute_candidate(conn, "AAPL", "long", 4_000, entry_price=200.0, entry_date="2026-01-02")
        vl.run_end_of_day(conn, "2026-01-02")
        vl.run_end_of_day(conn, "2026-01-02")  # re-run for the same date

    with connection(db_path) as conn:
        assert len(vl.get_account_snapshots(conn)) == 1  # updated in place, not duplicated


def test_run_end_of_day_skips_positions_with_no_price_data(db_path, fake_prices):
    # no fake price seeded for "ZZZZ" on the mark date -- must be skipped,
    # not crash or fabricate a mark.
    with connection(db_path) as conn:
        vl.deposit(conn, 10_000)
        vl.execute_candidate(conn, "ZZZZ", "long", 1_000, entry_price=50.0, entry_date="2026-01-02")
        result = vl.run_end_of_day(conn, "2026-01-05")

    assert result["positions_marked"] == 0
    assert result["unrealized_pnl"] == 0.0


# --- circuit breaker: built from investment P&L, not total_account_value -----------


def test_circuit_breaker_status_none_with_fewer_than_two_snapshots(db_path, fake_prices):
    fake_prices[("AAPL", "2026-01-02")] = 200.0
    fake_prices[("SPY", "2026-01-02")] = 500.0

    with connection(db_path) as conn:
        assert vl.circuit_breaker_status(conn) is None
        vl.deposit(conn, 10_000)
        vl.execute_candidate(conn, "AAPL", "long", 4_000, entry_price=200.0, entry_date="2026-01-02")
        vl.run_end_of_day(conn, "2026-01-02")
        assert vl.circuit_breaker_status(conn) is None  # still only 1 snapshot


def test_circuit_breaker_ignores_deposit_size_jump(db_path, fake_prices):
    # A large deposit between two EOD runs must NOT register as a fake
    # "return" -- circuit_breaker_status is built from investment P&L
    # (unrealized + realized), which a deposit never touches.
    fake_prices[("AAPL", "2026-01-02")] = 200.0
    fake_prices[("AAPL", "2026-01-05")] = 200.0  # flat price, zero P&L both days
    fake_prices[("SPY", "2026-01-02")] = 500.0
    fake_prices[("SPY", "2026-01-05")] = 500.0

    with connection(db_path) as conn:
        vl.deposit(conn, 10_000)
        vl.execute_candidate(conn, "AAPL", "long", 4_000, entry_price=200.0, entry_date="2026-01-02")
        vl.run_end_of_day(conn, "2026-01-02")
        vl.deposit(conn, 1_000_000)  # huge deposit between the two EOD runs
        vl.run_end_of_day(conn, "2026-01-05")
        status = vl.circuit_breaker_status(conn)

    assert status is not None
    assert status["tripped"] is False
    assert status["latest_daily_return"] == pytest.approx(0.0)


# --- reset ---------------------------------------------------------------------------


def test_reset_clears_everything(db_path, fake_prices):
    fake_prices[("AAPL", "2026-01-02")] = 200.0
    fake_prices[("SPY", "2026-01-02")] = 500.0

    with connection(db_path) as conn:
        vl.deposit(conn, 10_000)
        vl.execute_candidate(conn, "AAPL", "long", 4_000, entry_price=200.0, entry_date="2026-01-02")
        vl.run_end_of_day(conn, "2026-01-02")
        vl.reset(conn)

    with connection(db_path) as conn:
        assert vl.get_balance(conn) == {"total_deposited": 0.0, "free_cash": 0.0, "cash_tied_up": 0.0, "realized_pnl": 0.0}
        assert vl.list_positions(conn) == []
        assert vl.get_account_snapshots(conn) == []
