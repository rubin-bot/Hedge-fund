"""Normalizes fundamentals from either provider (FMP's JSON API or yfinance's
free scraped statements) into one canonical per-period field set, so the
factor engine can read a single schema regardless of which source populated
the `fundamentals` table for a given ticker.

Any field the source doesn't provide is left as None rather than guessed —
notably yfinance reports financial-sector balance sheets without a
current/non-current split (no Current Assets/Current Liabilities), which the
Piotroski calculation below must skip that criterion for rather than crash on.
"""

import math

import pandas as pd

CANONICAL_FIELDS = [
    "fiscal_date",
    "total_assets",
    "current_assets",
    "current_liabilities",
    "total_liabilities",
    "stockholders_equity",
    "retained_earnings",
    "total_debt",
    "cash_and_equivalents",
    "shares_outstanding",
    "revenue",
    "gross_profit",
    "ebit",
    "ebitda",
    "net_income",
    "operating_cash_flow",
    "capital_expenditure",
    "market_cap",
]


def _num(value) -> float | None:
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(value) else value


def normalize_yfinance_period(
    fiscal_date: str,
    balance_row: pd.Series,
    income_row: pd.Series | None,
    cash_flow_row: pd.Series | None,
    market_cap: float | None,
) -> dict:
    def bs(field: str) -> float | None:
        return _num(balance_row.get(field)) if balance_row is not None else None

    def inc(field: str) -> float | None:
        return _num(income_row.get(field)) if income_row is not None else None

    def cf(field: str) -> float | None:
        return _num(cash_flow_row.get(field)) if cash_flow_row is not None else None

    return {
        "fiscal_date": fiscal_date,
        "total_assets": bs("Total Assets"),
        "current_assets": bs("Current Assets"),
        "current_liabilities": bs("Current Liabilities"),
        "total_liabilities": bs("Total Liabilities Net Minority Interest"),
        "stockholders_equity": bs("Stockholders Equity"),
        "retained_earnings": bs("Retained Earnings"),
        "total_debt": bs("Total Debt"),
        "cash_and_equivalents": bs("Cash And Cash Equivalents"),
        "shares_outstanding": bs("Ordinary Shares Number"),
        "revenue": inc("Total Revenue"),
        "gross_profit": inc("Gross Profit"),
        "ebit": inc("EBIT"),
        "ebitda": inc("EBITDA"),
        "net_income": inc("Net Income"),
        "operating_cash_flow": cf("Operating Cash Flow"),
        "capital_expenditure": cf("Capital Expenditure"),
        "market_cap": _num(market_cap),
    }


def normalize_fmp_period(income_row: dict, balance_row: dict | None, ratios_row: dict | None) -> dict:
    balance_row = balance_row or {}
    ratios_row = ratios_row or {}
    return {
        "fiscal_date": income_row.get("date"),
        "total_assets": _num(balance_row.get("totalAssets")),
        "current_assets": _num(balance_row.get("totalCurrentAssets")),
        "current_liabilities": _num(balance_row.get("totalCurrentLiabilities")),
        "total_liabilities": _num(balance_row.get("totalLiabilities")),
        "stockholders_equity": _num(balance_row.get("totalStockholdersEquity")),
        "retained_earnings": _num(balance_row.get("retainedEarnings")),
        "total_debt": _num(balance_row.get("totalDebt")),
        "cash_and_equivalents": _num(balance_row.get("cashAndCashEquivalents")),
        "shares_outstanding": _num(income_row.get("weightedAverageShsOut")),
        "revenue": _num(income_row.get("revenue")),
        "gross_profit": _num(income_row.get("grossProfit")),
        "ebit": _num(income_row.get("operatingIncome")),
        "ebitda": _num(income_row.get("ebitda")),
        "net_income": _num(income_row.get("netIncome")),
        "operating_cash_flow": None,  # requires cash-flow-statement endpoint, not fetched by default
        "capital_expenditure": None,
        "market_cap": _num(ratios_row.get("marketCap")),
    }
