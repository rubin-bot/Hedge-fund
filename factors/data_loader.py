import json

import pandas as pd

from data.db import connection

SECTOR_ETF_MAP = {
    "Information Technology": "XLK",
    "Financials": "XLF",
    "Energy": "XLE",
    "Health Care": "XLV",
    "Industrials": "XLI",
    "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP",
    "Utilities": "XLU",
    "Materials": "XLB",
    "Communication Services": "XLC",
    "Real Estate": "XLRE",
}


def _placeholders(items: list) -> str:
    return ",".join("?" * len(items))


def load_universe(tickers: list[str] | None = None) -> pd.DataFrame:
    query = """
        SELECT ticker, company_name, sector, sub_industry, cik FROM sp500_universe
        WHERE as_of_date = (SELECT MAX(as_of_date) FROM sp500_universe)
    """
    params: list = []
    if tickers:
        query += f" AND ticker IN ({_placeholders(tickers)})"
        params = tickers
    with connection() as conn:
        return pd.read_sql_query(query, conn, params=params)


def load_sector_map(tickers: list[str] | None = None) -> pd.Series:
    df = load_universe(tickers)
    return df.set_index("ticker")["sector"]


def load_prices_wide(tickers: list[str], start_date: str | None = None) -> pd.DataFrame:
    """Wide-format Close prices: date index, one column per ticker."""
    query = f"SELECT ticker, date, close FROM prices_daily WHERE ticker IN ({_placeholders(tickers)})"
    params = list(tickers)
    if start_date:
        query += " AND date >= ?"
        params.append(start_date)
    with connection() as conn:
        df = pd.read_sql_query(query, conn, params=params)
    if df.empty:
        return pd.DataFrame()
    return df.pivot(index="date", columns="ticker", values="close").sort_index()


def load_volume_wide(tickers: list[str], start_date: str | None = None) -> pd.DataFrame:
    query = f"SELECT ticker, date, volume FROM prices_daily WHERE ticker IN ({_placeholders(tickers)})"
    params = list(tickers)
    if start_date:
        query += " AND date >= ?"
        params.append(start_date)
    with connection() as conn:
        df = pd.read_sql_query(query, conn, params=params)
    if df.empty:
        return pd.DataFrame()
    return df.pivot(index="date", columns="ticker", values="volume").sort_index()


def load_sector_etf_prices(start_date: str | None = None) -> pd.DataFrame:
    return load_prices_wide(list(set(SECTOR_ETF_MAP.values())), start_date)


def load_close_on_or_before(tickers: list[str], as_of_date: str) -> pd.Series:
    """Each ticker's most recent close on or before as_of_date. Used both as
    the "intended price" for sizing a paper-trading order at decision time,
    and for mark-to-market pricing.
    """
    query = f"""
        SELECT ticker, close FROM prices_daily
        WHERE ticker IN ({_placeholders(tickers)}) AND date <= ?
          AND date = (
              SELECT MAX(date) FROM prices_daily p2
              WHERE p2.ticker = prices_daily.ticker AND p2.date <= ?
          )
    """
    with connection() as conn:
        df = pd.read_sql_query(query, conn, params=list(tickers) + [as_of_date, as_of_date])
    if df.empty:
        return pd.Series(dtype=float)
    return df.set_index("ticker")["close"]


def load_next_session_open(tickers: list[str], as_of_date: str) -> pd.Series:
    """Each ticker's open on the first trading date STRICTLY AFTER as_of_date
    — the simulated fill reference price for a paper-trading order decided on
    as_of_date. A ticker with no such row yet (as_of_date is the latest date
    ingested for it) is simply absent from the result; callers should treat
    that as "can't fill yet" and skip rather than crash — expected at the
    live edge of the data, not a pipeline bug.
    """
    query = f"""
        SELECT ticker, open FROM prices_daily
        WHERE ticker IN ({_placeholders(tickers)}) AND date > ?
          AND date = (
              SELECT MIN(date) FROM prices_daily p2
              WHERE p2.ticker = prices_daily.ticker AND p2.date > ?
          )
    """
    with connection() as conn:
        df = pd.read_sql_query(query, conn, params=list(tickers) + [as_of_date, as_of_date])
    if df.empty:
        return pd.Series(dtype=float)
    return df.set_index("ticker")["open"]


def load_next_trading_date(as_of_date: str) -> str | None:
    """The next trading session's date after as_of_date, read off the shared
    US equity trading calendar embedded in prices_daily (any actively-traded
    ticker reflects it, so this isn't ticker-specific like the two loaders
    above). Used to timestamp a paper-trading fill's fill_date. None if
    as_of_date is the most recent date ingested for every ticker.
    """
    with connection() as conn:
        row = conn.execute("SELECT MIN(date) FROM prices_daily WHERE date > ?", (as_of_date,)).fetchone()
    return row[0] if row and row[0] else None


def load_average_daily_volume(tickers: list[str], as_of_date: str, window: int = 20) -> pd.Series:
    """Trailing `window`-session average volume ending on/before as_of_date,
    per ticker. Computed in pandas (group by ticker, take the trailing
    `window` rows, average) rather than a single SQL query with a per-group
    row limit, matching this file's existing style of simple queries plus
    pandas shaping.
    """
    query = f"""
        SELECT ticker, date, volume FROM prices_daily
        WHERE ticker IN ({_placeholders(tickers)}) AND date <= ? AND volume IS NOT NULL
        ORDER BY ticker, date ASC
    """
    with connection() as conn:
        df = pd.read_sql_query(query, conn, params=list(tickers) + [as_of_date])
    if df.empty:
        return pd.Series(dtype=float)
    return df.groupby("ticker")["volume"].apply(lambda s: s.tail(window).mean())


def load_fundamentals_history(tickers: list[str]) -> dict[str, pd.DataFrame]:
    """ticker -> DataFrame of canonical fundamentals, one row per fiscal period,
    sorted ascending by fiscal_date (oldest first) so callers can diff YoY.
    """
    query = f"""
        SELECT ticker, fiscal_date, payload_json FROM fundamentals
        WHERE statement_type = 'normalized_annual' AND ticker IN ({_placeholders(tickers)})
        ORDER BY ticker, fiscal_date ASC
    """
    with connection() as conn:
        raw = pd.read_sql_query(query, conn, params=list(tickers))

    result: dict[str, pd.DataFrame] = {}
    for ticker, group in raw.groupby("ticker"):
        records = [json.loads(payload) for payload in group["payload_json"]]
        result[ticker] = pd.DataFrame(records)
    return result


def load_form4(tickers: list[str], min_date: str | None = None) -> pd.DataFrame:
    query = f"""
        SELECT ticker, filer_name, is_director, is_officer, officer_title, transaction_date,
               transaction_code, shares, price_per_share, shares_owned_after, acquired_disposed
        FROM sec_form4_transactions WHERE ticker IN ({_placeholders(tickers)})
    """
    params = list(tickers)
    if min_date:
        query += " AND transaction_date >= ?"
        params.append(min_date)
    with connection() as conn:
        return pd.read_sql_query(query, conn, params=params)


def load_13f_holdings(tickers: list[str]) -> pd.DataFrame:
    query = f"""
        SELECT report_period, filer_cik, filer_name, cusip, ticker, issuer_name, shares, value_usd
        FROM sec_13f_holdings WHERE ticker IN ({_placeholders(tickers)})
        ORDER BY ticker, report_period ASC
    """
    with connection() as conn:
        return pd.read_sql_query(query, conn, params=list(tickers))


def load_congressional_trades(tickers: list[str] | None = None) -> pd.DataFrame:
    query = """
        SELECT chamber, member_name, ticker, transaction_type, transaction_date,
               disclosure_date, amount_range, source
        FROM congressional_trades WHERE ticker IS NOT NULL
    """
    params: list = []
    if tickers:
        query += f" AND ticker IN ({_placeholders(tickers)})"
        params = tickers
    with connection() as conn:
        return pd.read_sql_query(query, conn, params=params)


def load_analyst_estimates(tickers: list[str]) -> pd.DataFrame:
    query = f"""
        SELECT ticker, as_of_date, metric, period, value FROM analyst_estimates
        WHERE ticker IN ({_placeholders(tickers)})
        ORDER BY ticker, metric, period, as_of_date ASC
    """
    with connection() as conn:
        return pd.read_sql_query(query, conn, params=list(tickers))


def load_fred_series(series_id: str) -> pd.Series:
    with connection() as conn:
        df = pd.read_sql_query(
            "SELECT date, value FROM fred_series WHERE series_id = ? ORDER BY date ASC",
            conn,
            params=[series_id],
        )
    if df.empty:
        return pd.Series(dtype=float)
    return df.set_index("date")["value"]
