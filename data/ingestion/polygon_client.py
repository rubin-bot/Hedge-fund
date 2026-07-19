from datetime import date

import pandas as pd

from config.settings import settings
from data.ingestion.base import MarketDataClient

BASE_URL = "https://api.polygon.io"


class PolygonClient(MarketDataClient):
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.polygon_api_key

    def get_prices(self, tickers: list[str], start: date, end: date) -> pd.DataFrame:
        # TODO: call {BASE_URL}/v2/aggs/ticker/{ticker}/range/1/day/{start}/{end}?apiKey=...
        raise NotImplementedError

    def get_fundamentals(self, tickers: list[str]) -> pd.DataFrame:
        # TODO: call {BASE_URL}/vX/reference/financials?ticker=...&apiKey=...
        raise NotImplementedError
