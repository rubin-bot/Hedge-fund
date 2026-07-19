def estimate_slippage_bps(
    order_shares: float, avg_daily_volume: float, base_bps: float, impact_coefficient_bps: float
) -> float:
    """Linear-in-participation-rate slippage model: slippage grows with how
    large the order is relative to how much of this stock normally trades in
    a day. This is the direct reading of "based on order size relative to
    average daily volume" — simpler than square-root market-impact models
    (which also need a volatility term to be meaningful); upgrade path if
    this proves too crude once real fill data exists to calibrate against.

    avg_daily_volume <= 0 (no volume data, e.g. a thinly-backfilled ticker)
    falls back to base_bps only — there's nothing to estimate impact against.
    """
    if avg_daily_volume <= 0:
        return base_bps
    participation_pct = 100 * abs(order_shares) / avg_daily_volume
    return base_bps + impact_coefficient_bps * participation_pct


def simulate_fill_price(reference_open: float, order_shares: float, slippage_bps: float) -> float:
    """Buys fill ABOVE the reference open, sells fill BELOW it — slippage
    always costs the trader, never helps, by construction.
    """
    if order_shares == 0:
        return reference_open
    direction = 1 if order_shares > 0 else -1
    return reference_open * (1 + direction * slippage_bps / 10_000)
