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

    def get_ohlcv(self, tickers: list[str], start: date, end: date) -> pd.DataFrame:
        """Long-format OHLCV (ticker, date, open, high, low, close, volume) —
        the shape the prices_daily SQLite table expects, as opposed to
        get_prices()'s wide Close-only DataFrame used by the factor layer.
        """
        data = yf.download(tickers, start=start, end=end, auto_adjust=True, group_by="ticker")
        columns = ["Open", "High", "Low", "Close", "Volume"]

        frames = []
        if isinstance(data.columns, pd.MultiIndex):
            for ticker in tickers:
                if ticker not in data.columns.get_level_values(0):
                    continue
                sub = data[ticker][columns].dropna(how="all")
                sub = sub.reset_index().rename(columns={"Date": "date"})
                sub["ticker"] = ticker
                frames.append(sub)
        else:
            sub = data[columns].dropna(how="all").reset_index().rename(columns={"Date": "date"})
            sub["ticker"] = tickers[0]
            frames.append(sub)

        if not frames:
            return pd.DataFrame(columns=["ticker", "date", "open", "high", "low", "close", "volume"])

        combined = pd.concat(frames, ignore_index=True)
        combined = combined.rename(
            columns={"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"}
        )
        combined["date"] = pd.to_datetime(combined["date"]).dt.strftime("%Y-%m-%d")
        return combined[["ticker", "date", "open", "high", "low", "close", "volume"]]

    def get_financial_statements(self, ticker: str) -> dict[str, pd.DataFrame]:
        """Free, multi-period (annual) statements — the fallback fundamentals
        source when no FMP key is configured. Each DataFrame is transposed to
        period-rows so callers can iterate fiscal years directly.
        """
        info = yf.Ticker(ticker)
        return {
            "balance_sheet": info.balance_sheet.T,
            "income_statement": info.financials.T,
            "cash_flow": info.cashflow.T,
            "market_cap": info.info.get("marketCap"),
            "shares_outstanding": info.info.get("sharesOutstanding"),
        }

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
