from datetime import date

import pandas as pd
import requests

from config.settings import settings
from data.ingestion.base import MarketDataClient

BASE_URL = "https://financialmodelingprep.com/stable"


class FMPClient(MarketDataClient):
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.fmp_api_key
        if not self.api_key:
            raise ValueError("FMP_API_KEY is not set in .env")

    def _get(self, path: str, **params) -> list[dict]:
        params["apikey"] = self.api_key
        response = requests.get(f"{BASE_URL}/{path}", params=params, timeout=30)
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict) and "Error Message" in payload:
            raise RuntimeError(f"FMP error for {path}: {payload['Error Message']}")
        return payload

    def get_prices(self, tickers: list[str], start: date, end: date) -> pd.DataFrame:
        raise NotImplementedError("Use YFinanceClient for prices; FMP is used for fundamentals here")

    def get_income_statement(self, ticker: str, period: str = "annual", limit: int = 40) -> pd.DataFrame:
        return pd.DataFrame(self._get("income-statement", symbol=ticker, period=period, limit=limit))

    def get_balance_sheet(self, ticker: str, period: str = "annual", limit: int = 40) -> pd.DataFrame:
        return pd.DataFrame(self._get("balance-sheet-statement", symbol=ticker, period=period, limit=limit))

    def get_cash_flow_statement(self, ticker: str, period: str = "annual", limit: int = 40) -> pd.DataFrame:
        return pd.DataFrame(self._get("cash-flow-statement", symbol=ticker, period=period, limit=limit))

    def get_ratios(self, ticker: str, period: str = "annual", limit: int = 40) -> pd.DataFrame:
        return pd.DataFrame(self._get("ratios", symbol=ticker, period=period, limit=limit))

    def get_fundamentals(self, tickers: list[str]) -> pd.DataFrame:
        frames = []
        for ticker in tickers:
            income = self.get_income_statement(ticker, limit=1)
            ratios = self.get_ratios(ticker, limit=1)
            if income.empty:
                continue
            row = income.iloc[0].to_dict()
            if not ratios.empty:
                row.update(ratios.iloc[0].to_dict())
            frames.append(row)
        return pd.DataFrame(frames).set_index("symbol") if frames else pd.DataFrame()
