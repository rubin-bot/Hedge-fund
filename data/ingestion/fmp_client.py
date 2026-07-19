from datetime import date

import pandas as pd

from config.settings import settings
from data.ingestion.base import MarketDataClient

BASE_URL = "https://financialmodelingprep.com/api/v3"


class FMPClient(MarketDataClient):
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.fmp_api_key

    def get_prices(self, tickers: list[str], start: date, end: date) -> pd.DataFrame:
        # TODO: call {BASE_URL}/historical-price-full/{ticker}?from=...&to=...&apikey=...
        raise NotImplementedError

    def get_fundamentals(self, tickers: list[str]) -> pd.DataFrame:
        # TODO: call {BASE_URL}/income-statement/{ticker}?apikey=... plus balance-sheet / ratios
        raise NotImplementedError
