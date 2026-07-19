from dataclasses import dataclass, field

import pandas as pd


@dataclass
class PaperTradingEngine:
    starting_cash: float
    slippage_bps: float = 5
    commission_bps: float = 1
    cash: float = field(init=False)
    positions: pd.Series = field(default_factory=pd.Series, init=False)
    history: list[dict] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        self.cash = self.starting_cash

    def rebalance(self, target_weights: pd.Series, prices: pd.Series) -> None:
        # TODO: compute trades from current positions to target_weights, apply slippage/commission,
        # update self.cash and self.positions
        raise NotImplementedError

    def mark_to_market(self, prices: pd.Series, as_of: pd.Timestamp) -> float:
        portfolio_value = self.cash + float((self.positions * prices).sum())
        self.history.append({"date": as_of, "portfolio_value": portfolio_value})
        return portfolio_value
