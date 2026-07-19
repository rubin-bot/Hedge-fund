import pandas as pd
import pytest

from config.settings import settings
from data.db import connection, init_db
from simulation.execution import estimate_slippage_bps, simulate_fill_price
from simulation.paper_trading import PaperTradingEngine


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "test.db"
    init_db(path)
    # factors.data_loader's loaders always use the global settings.db_path (no
    # db_path param, matching that file's existing style) -- point it at the
    # same temp DB the engine itself writes to via its explicit db_path field.
    monkeypatch.setattr(settings, "db_path", path)
    return path


def _insert_price(conn, ticker, date, open_=None, close=None, volume=None):
    conn.execute(
        "INSERT INTO prices_daily (ticker, date, open, high, low, close, volume, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (ticker, date, open_, close, close, close, volume, "2026-01-01T00:00:00"),
    )


# --- simulation.execution ---------------------------------------------------------


def test_estimate_slippage_bps_scales_with_participation_rate():
    # 1000 shares / 1,000,000 ADV = 0.1% participation -> base_bps + 10*0.1 = 6.0
    result = estimate_slippage_bps(order_shares=1000, avg_daily_volume=1_000_000, base_bps=5.0, impact_coefficient_bps=10.0)
    assert abs(result - 6.0) < 1e-9


def test_estimate_slippage_bps_falls_back_to_base_when_no_volume_data():
    result = estimate_slippage_bps(order_shares=1000, avg_daily_volume=0.0, base_bps=5.0, impact_coefficient_bps=10.0)
    assert result == 5.0


def test_simulate_fill_price_buy_fills_above_reference():
    fill = simulate_fill_price(reference_open=100.0, order_shares=500, slippage_bps=10.0)
    assert abs(fill - 100.10) < 1e-9  # 100 * 1.001


def test_simulate_fill_price_sell_fills_below_reference():
    fill = simulate_fill_price(reference_open=100.0, order_shares=-500, slippage_bps=10.0)
    assert abs(fill - 99.90) < 1e-9  # 100 * 0.999


def test_simulate_fill_price_zero_shares_is_a_no_op():
    assert simulate_fill_price(reference_open=100.0, order_shares=0, slippage_bps=10.0) == 100.0


# --- PaperTradingEngine._apply_fill (lot accounting) -------------------------------


def _engine(db_path, cash=100_000.0):
    return PaperTradingEngine(run_id="test", starting_cash=cash, db_path=db_path)


def test_apply_fill_opens_fresh_position(db_path):
    engine = _engine(db_path)
    realized = engine._apply_fill("AAA", 100, 50.0)
    assert realized == 0.0
    assert engine.positions["AAA"] == 100
    assert engine.avg_cost["AAA"] == 50.0


def test_apply_fill_blends_cost_basis_when_adding(db_path):
    engine = _engine(db_path)
    engine._apply_fill("AAA", 100, 50.0)  # 100 @ 50
    realized = engine._apply_fill("AAA", 100, 60.0)  # + 100 @ 60
    assert realized == 0.0
    assert engine.positions["AAA"] == 200
    assert abs(engine.avg_cost["AAA"] - 55.0) < 1e-9  # (100*50 + 100*60) / 200


def test_apply_fill_partial_close_realizes_pnl_keeps_cost_basis(db_path):
    engine = _engine(db_path)
    engine._apply_fill("AAA", 100, 50.0)  # 100 @ 50
    realized = engine._apply_fill("AAA", -40, 70.0)  # sell 40 @ 70
    assert abs(realized - 40 * (70.0 - 50.0)) < 1e-9  # 800.0
    assert engine.positions["AAA"] == 60
    assert engine.avg_cost["AAA"] == 50.0  # remaining shares keep the original cost basis


def test_apply_fill_exact_close_zeroes_out_position(db_path):
    engine = _engine(db_path)
    engine._apply_fill("AAA", 100, 50.0)
    realized = engine._apply_fill("AAA", -100, 65.0)
    assert abs(realized - 100 * (65.0 - 50.0)) < 1e-9
    assert "AAA" not in engine.positions
    assert "AAA" not in engine.avg_cost


def test_apply_fill_short_position_realizes_pnl_on_cover(db_path):
    engine = _engine(db_path)
    engine._apply_fill("AAA", -100, 50.0)  # short 100 @ 50
    realized = engine._apply_fill("AAA", 40, 30.0)  # cover 40 @ 30 (price dropped -> profit on a short)
    assert abs(realized - 40 * (30.0 - 50.0) * -1) < 1e-9  # 800.0
    assert engine.positions["AAA"] == -60
    assert engine.avg_cost["AAA"] == 50.0


def test_apply_fill_flip_splits_close_and_reopen(db_path):
    engine = _engine(db_path)
    engine._apply_fill("AAA", 100, 50.0)  # long 100 @ 50
    realized = engine._apply_fill("AAA", -150, 70.0)  # sell 150 -> closes the 100, opens a new -50 short
    assert abs(realized - 100 * (70.0 - 50.0)) < 1e-9  # P&L only on the 100 that closed, not the new 50 short
    assert engine.positions["AAA"] == -50
    assert engine.avg_cost["AAA"] == 70.0  # the new short's cost basis is the flip's fill price


# --- PaperTradingEngine.rebalance() end-to-end -------------------------------------


def test_rebalance_fills_at_next_session_open_with_slippage_and_logs_trade(db_path):
    with connection(db_path) as conn:
        _insert_price(conn, "AAA", "2026-01-05", open_=99.0, close=100.0, volume=1_000_000)
        _insert_price(conn, "AAA", "2026-01-06", open_=101.0, close=101.0, volume=1_000_000)

    engine = _engine(db_path, cash=100_000.0)
    result = engine.rebalance(pd.Series({"AAA": 1.0}), as_of_date="2026-01-05")

    assert len(result["trades"]) == 1
    trade = result["trades"][0]
    assert trade["ticker"] == "AAA"
    assert trade["order_date"] == "2026-01-05"
    assert trade["fill_date"] == "2026-01-06"
    assert trade["intended_price"] == 100.0  # 2026-01-05's close, not its open
    assert trade["shares"] > 0  # a buy, sized off ~100k cash / $100 intended price
    assert trade["fill_price"] > 101.0  # a buy fills ABOVE the next-session open (101.0) due to slippage
    assert trade["slippage_bps"] > 0

    with connection(db_path) as conn:
        row = conn.execute("SELECT ticker, shares, fill_price FROM paper_trades WHERE run_id = 'test'").fetchone()
        assert row is not None
        assert row[0] == "AAA"
        snapshot = conn.execute(
            "SELECT total_equity, circuit_breaker_tripped FROM paper_portfolio_snapshots WHERE run_id = 'test' AND as_of_date = '2026-01-06'"
        ).fetchone()
        assert snapshot is not None
        assert snapshot[1] == 0  # not tripped -- no prior history to even evaluate yet


def test_rebalance_skips_ticker_with_no_next_session_data(db_path):
    with connection(db_path) as conn:
        _insert_price(conn, "AAA", "2026-01-05", open_=99.0, close=100.0, volume=1_000_000)
        # no row after 2026-01-05 -- "as_of_date" is the latest date ingested

    engine = _engine(db_path)
    result = engine.rebalance(pd.Series({"AAA": 1.0}), as_of_date="2026-01-05")

    assert result["trades"] == []
    assert len(result["skipped"]) == 1
    assert result["skipped"][0]["ticker"] == "AAA"
    assert "AAA" not in engine.positions


# --- circuit breaker halts new simulated trades (the requested test) --------------


def test_circuit_breaker_blocks_new_position_but_allows_reducing_existing_one(db_path):
    with connection(db_path) as conn:
        # D1 decision date for the initial buy; D2 is its fill date.
        _insert_price(conn, "AAA", "2026-02-02", open_=100.0, close=100.0, volume=1_000_000)
        _insert_price(conn, "AAA", "2026-02-03", open_=100.0, close=100.0, volume=1_000_000)
        # D3: AAA craters -60% -- this is what trips the breaker via the
        # engine's own realized equity curve (mark_to_market'd directly below,
        # simulating a day passing with no rebalance decision).
        _insert_price(conn, "AAA", "2026-02-04", open_=41.0, close=40.0, volume=1_000_000)
        _insert_price(conn, "BBB", "2026-02-04", open_=40.0, close=40.0, volume=1_000_000)
        # D4: fill date for the post-crash rebalance below.
        _insert_price(conn, "AAA", "2026-02-05", open_=40.0, close=40.0, volume=1_000_000)
        _insert_price(conn, "BBB", "2026-02-05", open_=40.0, close=40.0, volume=1_000_000)

    engine = _engine(db_path, cash=100_000.0)

    # Establish the initial ~full position in AAA.
    first = engine.rebalance(pd.Series({"AAA": 1.0}), as_of_date="2026-02-02")
    assert first["circuit_breaker"] is None  # no history yet to evaluate
    shares_before_crash = engine.positions["AAA"]
    assert shares_before_crash > 0

    # A day passes with no rebalance decision -- mark-to-market alone captures
    # the crash and persists the snapshot the circuit breaker will react to.
    engine.mark_to_market("2026-02-04")

    # Now propose adding a brand-new position (BBB) while reducing AAA.
    second = engine.rebalance(pd.Series({"AAA": 0.3, "BBB": 0.1}), as_of_date="2026-02-04")

    assert second["circuit_breaker"] is not None
    assert second["circuit_breaker"]["tripped"] is True

    executed_tickers = {t["ticker"] for t in second["trades"]}
    assert "BBB" not in executed_tickers  # new position blocked
    assert "BBB" not in engine.positions
    assert "AAA" in executed_tickers  # de-risking trade still went through
    aaa_trade = next(t for t in second["trades"] if t["ticker"] == "AAA")
    assert aaa_trade["shares"] < 0  # it's a sell (reduce), not a buy (add)
    assert 0 < engine.positions["AAA"] < shares_before_crash  # reduced, not closed, not added to
