from datetime import date

import pandas as pd
import yfinance as yf

# yfinance scrapes Yahoo Finance's public pages rather than calling a documented
# API, so field availability/naming can shift without notice — this is the free
# tradeoff described to the user vs. FMP's paid, structured analyst-estimates API.


def _price_target_records(ticker: str, as_of: str, targets: dict) -> list[dict]:
    return [
        {"ticker": ticker, "as_of_date": as_of, "metric": f"price_target_{key}", "period": "current", "value": value}
        for key, value in (targets or {}).items()
    ]


def _estimate_records(ticker: str, as_of: str, df: pd.DataFrame, prefix: str) -> list[dict]:
    if df is None or df.empty:
        return []
    records = []
    for period, row in df.iterrows():
        for column, value in row.items():
            if column == "currency" or pd.isna(value):
                continue
            records.append(
                {
                    "ticker": ticker,
                    "as_of_date": as_of,
                    "metric": f"{prefix}_{column}",
                    "period": str(period),
                    "value": float(value),
                }
            )
    return records


def _recommendation_records(ticker: str, as_of: str, df: pd.DataFrame) -> list[dict]:
    if df is None or df.empty:
        return []
    records = []
    for _, row in df.iterrows():
        period = str(row["period"])
        for column in ("strongBuy", "buy", "hold", "sell", "strongSell"):
            if column in row and pd.notna(row[column]):
                records.append(
                    {
                        "ticker": ticker,
                        "as_of_date": as_of,
                        "metric": f"rec_{column}",
                        "period": period,
                        "value": float(row[column]),
                    }
                )
    return records


def get_analyst_estimates(ticker: str) -> list[dict]:
    """Returns a flat list of {ticker, as_of_date, metric, period, value} records —
    the shape the analyst_estimates SQLite table expects — pulled from yfinance's
    free (unofficial) analyst data: price targets, EPS/revenue estimates, and
    buy/hold/sell recommendation counts.
    """
    as_of = date.today().isoformat()
    info = yf.Ticker(ticker)

    records = []
    records += _price_target_records(ticker, as_of, info.analyst_price_targets)
    records += _estimate_records(ticker, as_of, info.earnings_estimate, "eps_estimate")
    records += _estimate_records(ticker, as_of, info.revenue_estimate, "revenue_estimate")
    records += _recommendation_records(ticker, as_of, info.recommendations)
    return records
