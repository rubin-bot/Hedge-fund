import pandas as pd

DAILY_LOSS_THRESHOLD = 0.025
DRAWDOWN_THRESHOLD = 0.08


def simulate_portfolio_returns(weights: pd.Series, prices: pd.DataFrame) -> pd.Series:
    """Daily returns the CURRENT target weights would have realized over the
    given price history. Computed directly from weights + a price panel
    rather than depending on simulation.paper_trading's rebalance() (still
    NotImplementedError) — this keeps the risk layer self-contained, able to
    evaluate a proposed portfolio without depending on an unrelated,
    unfinished module.
    """
    returns = prices[weights.index].pct_change()
    return (returns.fillna(0.0) * weights).sum(axis=1)


def simulate_portfolio_value(portfolio_returns: pd.Series, starting_value: float = 1.0) -> pd.Series:
    return starting_value * (1 + portfolio_returns).cumprod()


def check_circuit_breaker(
    portfolio_returns: pd.Series,
    daily_loss_threshold: float = DAILY_LOSS_THRESHOLD,
    drawdown_threshold: float = DRAWDOWN_THRESHOLD,
) -> dict:
    """Evaluates the two circuit-breaker conditions against a simulated daily
    return series: the most recent day's loss, and drawdown from the running
    peak of the simulated cumulative value. Either condition tripping halts
    new position entry (see apply_circuit_breaker).
    """
    value = simulate_portfolio_value(portfolio_returns)
    latest_return = float(portfolio_returns.iloc[-1])
    running_peak = value.cummax()
    drawdown = (value - running_peak) / running_peak
    latest_drawdown = float(drawdown.iloc[-1])

    daily_loss_tripped = latest_return <= -daily_loss_threshold
    drawdown_tripped = latest_drawdown <= -drawdown_threshold

    reasons = []
    if daily_loss_tripped:
        reasons.append(f"daily loss {latest_return:.2%} breached -{daily_loss_threshold:.2%} threshold")
    if drawdown_tripped:
        reasons.append(f"drawdown from peak {latest_drawdown:.2%} breached -{drawdown_threshold:.2%} threshold")

    return {
        "tripped": daily_loss_tripped or drawdown_tripped,
        "daily_loss_tripped": daily_loss_tripped,
        "drawdown_tripped": drawdown_tripped,
        "latest_daily_return": latest_return,
        "latest_drawdown": latest_drawdown,
        "reason": "; ".join(reasons) if reasons else "within thresholds",
    }


def apply_circuit_breaker(proposed_weights: pd.Series, previous_weights: pd.Series | None) -> pd.Series:
    """Applies "halt new position entry" once the circuit breaker trips.

    A real circuit breaker should still let a book de-risk during a crash,
    just not add to it — freezing the whole portfolio would prevent exactly
    the risk-reducing trades you'd want during a drawdown. Per ticker:
      - previous weight is 0 (would-be new position) -> blocked, stays 0
      - same sign as previous, |proposed| <= |previous| (reduce/close)     -> allowed
      - same sign as previous, |proposed| >  |previous| (add to position)  -> blocked, reverts to previous
      - opposite sign to previous (flip long<->short)                     -> blocked, reverts to previous
    """
    previous = previous_weights if previous_weights is not None else pd.Series(dtype=float)
    all_tickers = proposed_weights.index.union(previous.index)
    proposed = proposed_weights.reindex(all_tickers, fill_value=0.0)
    prior = previous.reindex(all_tickers, fill_value=0.0)

    final = prior.copy()
    # proposed*prior > 0 requires both nonzero AND the same sign (a product
    # involving a zero, or of opposite signs, is <= 0) -- exactly "reducing an
    # existing position" once combined with the magnitude check below. Every
    # other case (new position, add-to-existing, sign flip) keeps `final` at
    # its `prior.copy()` default, i.e. blocked/reverted.
    same_sign = proposed * prior > 0
    reducing = same_sign & (proposed.abs() <= prior.abs())
    final[reducing] = proposed[reducing]
    return final
