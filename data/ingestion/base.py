from abc import ABC, abstractmethod
from datetime import date

import pandas as pd


class MarketDataClient(ABC):
    @abstractmethod
    def get_prices(self, tickers: list[str], start: date, end: date) -> pd.DataFrame:
        raise NotImplementedError

    @abstractmethod
    def get_fundamentals(self, tickers: list[str]) -> pd.DataFrame:
        raise NotImplementedError
