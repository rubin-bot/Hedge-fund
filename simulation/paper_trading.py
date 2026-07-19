import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from config.settings import load_risk_config, load_simulation_config
from data.db import connection, init_db
from factors.data_loader import (
    load_average_daily_volume,
    load_close_on_or_before,
    load_next_session_open,
    load_next_trading_date,
)
from risk.circuit_breaker import DAILY_LOSS_THRESHOLD, DRAWDOWN_THRESHOLD, apply_circuit_breaker, check_circuit_breaker
from simulation.execution import estimate_slippage_bps, simulate_fill_price

MIN_TRADE_SHARES = 1e-6  # ignore sub-share rebalancing noise from float weight math


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class PaperTradingEngine:
    """Simulated execution engine: no real broker connection. Turns target
    portfolio weights into simulated next-session fills (with an ADV-based
    slippage model), tracks cash/positions/P&L, and logs every fill to
    SQLite the same shape a real execution log would use. Wired to
    risk.circuit_breaker so a breach in this engine's OWN equity curve halts
    new position entry on the next rebalance (see rebalance() below) —
    reuses risk/circuit_breaker.py's check_circuit_breaker/apply_circuit_breaker
    verbatim rather than reimplementing that logic here.
    """

    run_id: str
    starting_cash: float
    base_slippage_bps: float | None = None
    commission_bps: float | None = None
    impact_coefficient_bps: float | None = None
    adv_window_days: int | None = None
    circuit_breaker_daily_loss_threshold: float | None = None
    circuit_breaker_drawdown_threshold: float | None = None
    db_path: Path | None = None

    cash: float = field(init=False)
    positions: dict[str, float] = field(default_factory=dict, init=False)
    avg_cost: dict[str, float] = field(default_factory=dict, init=False)
    realized_pnl_cumulative: float = field(init=False, default=0.0)

    def __post_init__(self) -> None:
        # explicit arg > config.yaml > hardcoded default, same precedence
        # pattern used by ScoringEngine/GeminiClient/construct_portfolio elsewhere.
        simulation_cfg = load_simulation_config()
        risk_cfg = load_risk_config()
        self.base_slippage_bps = (
            self.base_slippage_bps if self.base_slippage_bps is not None else simulation_cfg.get("slippage_bps", 5.0)
        )
        self.commission_bps = (
            self.commission_bps if self.commission_bps is not None else simulation_cfg.get("commission_bps", 1.0)
        )
        self.impact_coefficient_bps = (
            self.impact_coefficient_bps
            if self.impact_coefficient_bps is not None
            else simulation_cfg.get("impact_coefficient_bps", 10.0)
        )
        self.adv_window_days = (
            self.adv_window_days if self.adv_window_days is not None else simulation_cfg.get("adv_window_days", 20)
        )
        self.circuit_breaker_daily_loss_threshold = (
            self.circuit_breaker_daily_loss_threshold
            if self.circuit_breaker_daily_loss_threshold is not None
            else risk_cfg.get("circuit_breaker_daily_loss_threshold", DAILY_LOSS_THRESHOLD)
        )
        self.circuit_breaker_drawdown_threshold = (
            self.circuit_breaker_drawdown_threshold
            if self.circuit_breaker_drawdown_threshold is not None
            else risk_cfg.get("circuit_breaker_drawdown_threshold", DRAWDOWN_THRESHOLD)
        )
        self.cash = self.starting_cash
        init_db(self.db_path)

    # --- order generation / fills ---------------------------------------------

    def rebalance(self, target_weights: pd.Series, as_of_date: str) -> dict:
        """as_of_date is the DECISION date: its close is the "intended price"
        used to size orders. Fills happen at the NEXT trading session's open,
        adjusted by the slippage model. Returns a dict with the trades
        executed, any skipped (no next-session data yet -- expected at the
        live edge of the data, not an error), and the circuit-breaker check
        that ran before order generation.
        """
        all_tickers = sorted(set(target_weights.index) | set(self.positions.keys()))
        intended_prices = load_close_on_or_before(all_tickers, as_of_date)
        tradeable = [t for t in all_tickers if t in intended_prices.index and pd.notna(intended_prices[t])]

        market_value = sum(self.positions.get(t, 0.0) * intended_prices[t] for t in tradeable)
        total_equity = self.cash + market_value

        weights_to_use = target_weights.reindex(tradeable, fill_value=0.0)
        circuit_breaker_result = self._check_own_circuit_breaker()
        if circuit_breaker_result is not None and circuit_breaker_result["tripped"] and total_equity > 0:
            current_weights = pd.Series(
                {t: self.positions.get(t, 0.0) * intended_prices[t] / total_equity for t in tradeable}
            )
            # Reused verbatim from risk/circuit_breaker.py -- halts new entries
            # and adds, still allows de-risking trades through.
            weights_to_use = apply_circuit_breaker(weights_to_use, current_weights)

        trades_executed = []
        trades_skipped = []
        for ticker in tradeable:
            target_shares = (
                weights_to_use.get(ticker, 0.0) * total_equity / intended_prices[ticker]
                if intended_prices[ticker]
                else 0.0
            )
            current_shares = self.positions.get(ticker, 0.0)
            trade_shares = target_shares - current_shares
            if abs(trade_shares) < MIN_TRADE_SHARES:
                continue

            next_open = load_next_session_open([ticker], as_of_date)
            if ticker not in next_open.index or pd.isna(next_open[ticker]):
                trades_skipped.append({"ticker": ticker, "reason": "no next-session price data available yet"})
                continue
            fill_date = load_next_trading_date(as_of_date)

            avg_volume = load_average_daily_volume([ticker], as_of_date, self.adv_window_days).get(ticker, 0.0)
            slippage_bps = estimate_slippage_bps(
                trade_shares, avg_volume, self.base_slippage_bps, self.impact_coefficient_bps
            )
            fill_price = simulate_fill_price(next_open[ticker], trade_shares, slippage_bps)
            commission_usd = abs(trade_shares) * fill_price * self.commission_bps / 10_000

            realized_pnl = self._apply_fill(ticker, trade_shares, fill_price)
            self.cash -= trade_shares * fill_price + commission_usd
            self.realized_pnl_cumulative += realized_pnl

            trade = {
                "run_id": self.run_id,
                "ticker": ticker,
                "order_date": as_of_date,
                "fill_date": fill_date,
                "shares": trade_shares,
                "intended_price": float(intended_prices[ticker]),
                "fill_price": fill_price,
                "slippage_bps": slippage_bps,
                "commission_usd": commission_usd,
                "realized_pnl": realized_pnl,
            }
            self._persist_trade(trade)
            trades_executed.append(trade)

        fill_dates = [t["fill_date"] for t in trades_executed if t["fill_date"]]
        snapshot_date = max(fill_dates) if fill_dates else as_of_date
        self.mark_to_market(
            snapshot_date, circuit_breaker_tripped=bool(circuit_breaker_result and circuit_breaker_result["tripped"])
        )

        return {"trades": trades_executed, "skipped": trades_skipped, "circuit_breaker": circuit_breaker_result}

    def _check_own_circuit_breaker(self) -> dict | None:
        """Builds a return series from THIS engine's own persisted equity
        history (not a hypothetical simulation of proposed weights, like
        risk/gate.py uses -- an actual realized trade history) and runs it
        through risk.circuit_breaker.check_circuit_breaker. None if there's
        not yet enough history (fewer than 2 snapshots) to compute a return.
        """
        history = self._load_snapshot_history()
        if len(history) < 2:
            return None
        equity = pd.Series([h["total_equity"] for h in history])
        portfolio_returns = equity.pct_change().dropna()
        if portfolio_returns.empty:
            return None
        return check_circuit_breaker(
            portfolio_returns, self.circuit_breaker_daily_loss_threshold, self.circuit_breaker_drawdown_threshold
        )

    def _apply_fill(self, ticker: str, trade_shares: float, fill_price: float) -> float:
        """Average-cost lot accounting, symmetric for longs and shorts.
        Returns the realized P&L attributable to this fill (0 unless it
        closes some or all of an existing position).
        """
        current_shares = self.positions.get(ticker, 0.0)
        current_avg_cost = self.avg_cost.get(ticker, 0.0)

        if current_shares == 0:
            # opening a fresh position (long or short) from flat
            self.positions[ticker] = trade_shares
            self.avg_cost[ticker] = fill_price
            return 0.0

        same_direction = (trade_shares > 0) == (current_shares > 0)
        if same_direction:
            # adding to an existing position -- blend the cost basis
            final_shares = current_shares + trade_shares
            self.positions[ticker] = final_shares
            self.avg_cost[ticker] = (current_shares * current_avg_cost + trade_shares * fill_price) / final_shares
            return 0.0

        direction = 1 if current_shares > 0 else -1
        if abs(trade_shares) <= abs(current_shares):
            # partial or exact close, no flip -- remaining shares (if any)
            # keep the original cost basis
            realized_pnl = abs(trade_shares) * (fill_price - current_avg_cost) * direction
            final_shares = current_shares + trade_shares
            if final_shares == 0:
                self.positions.pop(ticker, None)
                self.avg_cost.pop(ticker, None)
            else:
                self.positions[ticker] = final_shares
                self.avg_cost[ticker] = current_avg_cost
            return realized_pnl

        # flips direction: fully close the existing position, then open the
        # remainder fresh at fill_price -- split so P&L attribution is
        # correct on the closed portion only.
        realized_pnl = abs(current_shares) * (fill_price - current_avg_cost) * direction
        final_shares = trade_shares + current_shares
        self.positions[ticker] = final_shares
        self.avg_cost[ticker] = fill_price
        return realized_pnl

    # --- valuation / persistence ------------------------------------------------

    def mark_to_market(self, as_of_date: str, circuit_breaker_tripped: bool = False) -> dict:
        """Values the current book as of `as_of_date` and persists the
        snapshot — this is the single place snapshots get written, whether
        called directly (e.g. a daily NAV mark with no rebalance decision) or
        internally at the end of rebalance() (which passes through whether
        the circuit breaker tripped this decision).
        """
        tickers = list(self.positions.keys())
        prices = load_close_on_or_before(tickers, as_of_date) if tickers else pd.Series(dtype=float)

        market_value = 0.0
        unrealized_pnl = 0.0
        for ticker, shares in self.positions.items():
            price = prices.get(ticker)
            if price is None or pd.isna(price):
                continue  # no price yet for this date -- excluded from this snapshot's valuation
            market_value += shares * price
            unrealized_pnl += shares * (price - self.avg_cost.get(ticker, price))

        snapshot = {
            "as_of_date": as_of_date,
            "cash": self.cash,
            "market_value": market_value,
            "total_equity": self.cash + market_value,
            "realized_pnl_cumulative": self.realized_pnl_cumulative,
            "unrealized_pnl": unrealized_pnl,
            "positions": dict(self.positions),
        }
        self._persist_snapshot(as_of_date, snapshot, circuit_breaker_tripped)
        return snapshot

    def _persist_trade(self, trade: dict) -> None:
        trade_id = hashlib.sha256(
            f"{trade['run_id']}|{trade['ticker']}|{trade['order_date']}|{trade['fill_date']}|{trade['shares']}".encode()
        ).hexdigest()[:24]
        with connection(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO paper_trades
                    (trade_id, run_id, ticker, order_date, fill_date, shares, intended_price,
                     fill_price, slippage_bps, commission_usd, realized_pnl, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trade_id, trade["run_id"], trade["ticker"], trade["order_date"], trade["fill_date"],
                    trade["shares"], trade["intended_price"], trade["fill_price"], trade["slippage_bps"],
                    trade["commission_usd"], trade["realized_pnl"], _now_iso(),
                ),
            )
        trade["trade_id"] = trade_id

    def _persist_snapshot(self, as_of_date: str, snapshot: dict, circuit_breaker_tripped: bool) -> None:
        with connection(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO paper_portfolio_snapshots
                    (run_id, as_of_date, cash, market_value, total_equity, realized_pnl_cumulative,
                     unrealized_pnl, circuit_breaker_tripped, positions_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (run_id, as_of_date) DO UPDATE SET
                    cash = excluded.cash,
                    market_value = excluded.market_value,
                    total_equity = excluded.total_equity,
                    realized_pnl_cumulative = excluded.realized_pnl_cumulative,
                    unrealized_pnl = excluded.unrealized_pnl,
                    circuit_breaker_tripped = excluded.circuit_breaker_tripped,
                    positions_json = excluded.positions_json
                """,
                (
                    self.run_id, as_of_date, snapshot["cash"], snapshot["market_value"], snapshot["total_equity"],
                    snapshot["realized_pnl_cumulative"], snapshot["unrealized_pnl"], int(circuit_breaker_tripped),
                    json.dumps(snapshot["positions"]),
                ),
            )

    def _load_snapshot_history(self) -> list[dict]:
        with connection(self.db_path) as conn:
            rows = conn.execute(
                "SELECT as_of_date, total_equity FROM paper_portfolio_snapshots WHERE run_id = ? ORDER BY as_of_date ASC",
                (self.run_id,),
            ).fetchall()
        return [{"as_of_date": row[0], "total_equity": row[1]} for row in rows]
