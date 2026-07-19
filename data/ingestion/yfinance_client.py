from datetime import date

import pandas as pd
import yfinance as yf

from data.ingestion.base import MarketDataClient


class YFinanceClient(MarketDataClient):
    def get_prices(self, tickers: list[str], start: date, end: date) -> pd.DataFrame:
        data = yf.download(tickers, start=start, end=end, auto_adjust=True, group_by="ticker")
        if isinstance(data.columns, pd.MultiIndex):
            return data.xs("Close", axis=1, level=1)
        return data[["Close"]].rename(columns={"Close": tickers[0]})

    def get_fundamentals(self, tickers: list[str]) -> pd.DataFrame:
        rows = []
        for ticker in tickers:
            info = yf.Ticker(ticker).info
            rows.append(
                {
                    "ticker": ticker,
                    "market_cap": info.get("marketCap"),
                    "trailing_pe": info.get("trailingPE"),
                    "price_to_book": info.get("priceToBook"),
                    "return_on_equity": info.get("returnOnEquity"),
                    "revenue_growth": info.get("revenueGrowth"),
                    "gross_margins": info.get("grossMargins"),
                    "debt_to_equity": info.get("debtToEquity"),
                }
            )
        return pd.DataFrame(rows).set_index("ticker")
