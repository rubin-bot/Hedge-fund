import sys
import time
from datetime import date, datetime, timedelta, timezone

from config.settings import settings
from data.backfill import _insert_congressional_trades, backfill_13f, now_iso, warn
from data.db import connection, get_sync_state, init_db, set_sync_state
from data.ingestion.analyst_estimates import get_analyst_estimates
from data.ingestion.congressional import house_clerk
from data.ingestion.congressional.base import SourceStructureError
from data.ingestion.finra_short_interest import get_short_interest, recent_settlement_dates
from data.ingestion.fmp_client import FMPClient
from data.ingestion.fred_client import SERIES_BDI_PROXY, SERIES_DXY_PROXY, SERIES_PPI, FREDClient
from data.ingestion.sec_edgar_client import SECEdgarClient
from data.ingestion.yfinance_client import YFinanceClient
from data.universe import get_sp500_tickers, get_ticker_to_cik, sync_sp500_universe

PRICE_LOOKBACK_DAYS = 7  # small rolling window covers weekends/holidays/late revisions
FUNDAMENTALS_REFRESH_DAYS = 7  # fundamentals don't change daily; skip tickers synced recently


def sync_prices(tickers: list[str]) -> None:
    client = YFinanceClient()
    start = date.today() - timedelta(days=PRICE_LOOKBACK_DAYS)
    print(f"[daily_sync] prices: {len(tickers)} tickers since {start}")

    with connection() as conn:
        chunk_size = 50
        for i in range(0, len(tickers), chunk_size):
            chunk = tickers[i : i + chunk_size]
            try:
                df = client.get_ohlcv(chunk, start, date.today())
            except Exception as exc:
                warn(f"price chunk failed: {exc}")
                continue
            fetched_at = now_iso()
            rows = [
                (r.ticker, r.date, r.open, r.high, r.low, r.close, r.volume, fetched_at)
                for r in df.itertuples(index=False)
            ]
            conn.executemany(
                """
                INSERT INTO prices_daily (ticker, date, open, high, low, close, volume, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (ticker, date) DO UPDATE SET
                    open=excluded.open, high=excluded.high, low=excluded.low,
                    close=excluded.close, volume=excluded.volume, fetched_at=excluded.fetched_at
                """,
                rows,
            )
            for ticker in chunk:
                set_sync_state(conn, "yfinance_prices", ticker, date.today().isoformat(), fetched_at)


def sync_fundamentals(tickers: list[str]) -> None:
    if not settings.fmp_api_key:
        warn("FMP_API_KEY not set — skipping fundamentals sync")
        return
    client = FMPClient()
    cutoff = date.today() - timedelta(days=FUNDAMENTALS_REFRESH_DAYS)

    with connection() as conn:
        due = [
            t
            for t in tickers
            if (last := get_sync_state(conn, "fmp_fundamentals", t)) is None
            or datetime.fromisoformat(last).date() < cutoff
        ]
    if not due:
        print("[daily_sync] fundamentals: nothing due for refresh")
        return
    print(f"[daily_sync] fundamentals: {len(due)}/{len(tickers)} tickers due for refresh")

    import json

    with connection() as conn:
        for ticker in due:
            try:
                income = client.get_income_statement(ticker, period="quarter", limit=2)
                ratios = client.get_ratios(ticker, period="quarter", limit=2)
            except Exception as exc:
                warn(f"fundamentals for {ticker} failed: {exc}")
                continue
            fetched_at = now_iso()
            for statement_type, df in (("income_statement", income), ("ratios", ratios)):
                if df.empty:
                    continue
                rows = [
                    (
                        ticker, statement_type,
                        str(row.get("period", "")) + str(row.get("fiscalYear", row.get("date", ""))),
                        row.get("date"), json.dumps(row, default=str), fetched_at,
                    )
                    for row in df.to_dict("records")
                ]
                conn.executemany(
                    """
                    INSERT INTO fundamentals (ticker, statement_type, period, fiscal_date, payload_json, fetched_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT (ticker, statement_type, period) DO UPDATE SET
                        fiscal_date=excluded.fiscal_date, payload_json=excluded.payload_json, fetched_at=excluded.fetched_at
                    """,
                    rows,
                )
            set_sync_state(conn, "fmp_fundamentals", ticker, date.today().isoformat(), fetched_at)


def sync_sec_filings_and_form4(tickers: list[str], ticker_to_cik: dict[str, str]) -> None:
    client = SECEdgarClient()
    print(f"[daily_sync] SEC filings + Form 4: {len(tickers)} tickers")

    with connection() as conn:
        for ticker in tickers:
            cik = ticker_to_cik.get(ticker)
            if not cik:
                continue
            last_sync = get_sync_state(conn, "sec_filings_index", ticker)
            min_date = last_sync[:10] if last_sync else None

            try:
                filings = client.list_filings(cik, ["10-K", "10-Q"], include_history=False)
            except Exception as exc:
                warn(f"SEC filings for {ticker} failed: {exc}")
                continue
            new_filings = [f for f in filings if not min_date or f["filing_date"] > min_date]
            fetched_at = now_iso()
            if new_filings:
                rows = [
                    (
                        f["accession_number"], ticker, f["cik"], f["form_type"], f["filing_date"],
                        f["period_of_report"], client.document_url(f["cik"], f["accession_number"], f["primary_document"]),
                        None, fetched_at,
                    )
                    for f in new_filings
                ]
                conn.executemany(
                    """
                    INSERT INTO sec_filings
                        (accession_number, ticker, cik, form_type, filing_date, period_of_report, primary_doc_url, local_path, fetched_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (accession_number) DO UPDATE SET fetched_at=excluded.fetched_at
                    """,
                    rows,
                )
            set_sync_state(conn, "sec_filings_index", ticker, date.today().isoformat(), fetched_at)

            try:
                transactions = client.get_form4_transactions(cik, ticker, include_history=False)
            except Exception as exc:
                warn(f"Form 4 for {ticker} failed: {exc}")
                continue
            if min_date:
                transactions = [tx for tx in transactions if (tx["transaction_date"] or "") > min_date]
            if transactions:
                rows = [
                    (
                        tx["accession_number"], tx["ticker"], tx["cik"], tx["filer_name"],
                        int(tx["is_director"]), int(tx["is_officer"]), tx["officer_title"],
                        tx["transaction_date"], tx["transaction_code"], tx["shares"],
                        tx["price_per_share"], tx["shares_owned_after"], tx["acquired_disposed"], fetched_at,
                    )
                    for tx in transactions
                ]
                conn.executemany(
                    """
                    INSERT INTO sec_form4_transactions
                        (accession_number, ticker, cik, filer_name, is_director, is_officer, officer_title,
                         transaction_date, transaction_code, shares, price_per_share, shares_owned_after,
                         acquired_disposed, fetched_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (accession_number, transaction_date, transaction_code, shares) DO NOTHING
                    """,
                    rows,
                )


def sync_13f() -> None:
    print("[daily_sync] 13F: checking for a newly published quarterly data set")
    client = SECEdgarClient()
    with connection() as conn:
        last = get_sync_state(conn, "sec_13f", "latest")
    urls = client.list_13f_dataset_urls()
    latest_label = urls[0].rsplit("/", 1)[-1] if urls else None
    if latest_label and latest_label != last:
        backfill_13f(max_quarters=1)  # only the newest data set
        with connection() as conn:
            set_sync_state(conn, "sec_13f", "latest", latest_label, now_iso())
    else:
        print("[daily_sync] 13F: no new data set since last sync")


def sync_congressional() -> None:
    year = date.today().year
    with connection() as conn:
        last = get_sync_state(conn, "house_clerk", str(year))
    min_filing_date = None
    if last:
        min_filing_date = datetime.fromisoformat(last).strftime("%m/%d/%Y")

    print(f"[daily_sync] House Clerk congressional trades since {min_filing_date or 'year start'}")
    try:
        trades = house_clerk.get_trades(year, min_filing_date=min_filing_date)
    except SourceStructureError as exc:
        warn(f"House Clerk sync aborted: {exc}")
        return

    with connection() as conn:
        _insert_congressional_trades(conn, trades)
        set_sync_state(conn, "house_clerk", str(year), date.today().isoformat(), now_iso())
    print(f"[daily_sync] House Clerk: {len(trades)} new trades")

    # Senate eFD is attempted but expected to be flaky/blocked from some networks —
    # see data/ingestion/congressional/senate_efd.py for why. A failure here is
    # logged, not fatal to the rest of the sync.
    try:
        from data.ingestion.congressional import senate_efd

        with connection() as conn:
            last_senate = get_sync_state(conn, "senate_efd", str(year))
        start = datetime.fromisoformat(last_senate).strftime("%m/%d/%Y") if last_senate else f"01/01/{year}"
        senate_trades = senate_efd.get_trades(start)
        with connection() as conn:
            _insert_congressional_trades(conn, senate_trades)
            set_sync_state(conn, "senate_efd", str(year), date.today().isoformat(), now_iso())
        print(f"[daily_sync] Senate eFD: {len(senate_trades)} new trades")
    except SourceStructureError as exc:
        warn(f"Senate eFD sync skipped: {exc}")


def sync_fred() -> None:
    if not settings.fred_api_key:
        warn("FRED_API_KEY not set — skipping FRED sync")
        return
    client = FREDClient()
    with connection() as conn:
        for series_id in (SERIES_PPI, SERIES_DXY_PROXY, SERIES_BDI_PROXY):
            last = get_sync_state(conn, "fred", series_id)
            start = datetime.fromisoformat(last).date() + timedelta(days=1) if last else date.today() - timedelta(days=30)
            if start > date.today():
                continue
            df = client.get_series(series_id, start, date.today())
            fetched_at = now_iso()
            rows = [(series_id, d.strftime("%Y-%m-%d"), v if v == v else None, fetched_at) for d, v in df["value"].items()]
            if rows:
                conn.executemany(
                    """
                    INSERT INTO fred_series (series_id, date, value, fetched_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT (series_id, date) DO UPDATE SET value=excluded.value, fetched_at=excluded.fetched_at
                    """,
                    rows,
                )
            set_sync_state(conn, "fred", series_id, date.today().isoformat(), fetched_at)
            print(f"[daily_sync] FRED {series_id}: {len(rows)} new observations")


def sync_short_interest(tickers: list[str]) -> None:
    latest_date = recent_settlement_dates(1)
    if not latest_date:
        return
    settlement_date = latest_date[0]

    with connection() as conn:
        already_synced = get_sync_state(conn, "finra_short_interest", settlement_date.isoformat())
    if already_synced:
        print(f"[daily_sync] short interest: {settlement_date} already synced")
        return

    print(f"[daily_sync] short interest: fetching {settlement_date} for {len(tickers)} tickers")
    fetched_at = now_iso()
    rows = []
    for ticker in tickers:
        df = get_short_interest(ticker, settlement_date)
        time.sleep(0.1)
        if df.empty:
            continue
        row = df.iloc[0]
        rows.append(
            (
                ticker, str(row["settlementDate"]), int(row["currentShortPositionQuantity"]),
                int(row["averageDailyVolumeQuantity"]), float(row["daysToCoverQuantity"]), fetched_at,
            )
        )

    if not rows:
        print(f"[daily_sync] short interest: {settlement_date} not published yet")
        return

    with connection() as conn:
        conn.executemany(
            """
            INSERT INTO short_interest (ticker, settlement_date, short_interest_shares, avg_daily_volume, days_to_cover, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (ticker, settlement_date) DO UPDATE SET
                short_interest_shares=excluded.short_interest_shares,
                avg_daily_volume=excluded.avg_daily_volume,
                days_to_cover=excluded.days_to_cover, fetched_at=excluded.fetched_at
            """,
            rows,
        )
        set_sync_state(conn, "finra_short_interest", settlement_date.isoformat(), date.today().isoformat(), fetched_at)
    print(f"[daily_sync] short interest: {len(rows)}/{len(tickers)} tickers")


def sync_analyst_estimates(tickers: list[str]) -> None:
    print(f"[daily_sync] analyst estimates: {len(tickers)} tickers")
    with connection() as conn:
        for ticker in tickers:
            last = get_sync_state(conn, "analyst_estimates", ticker)
            if last and datetime.fromisoformat(last).date() == date.today():
                continue  # already refreshed today
            try:
                records = get_analyst_estimates(ticker)
            except Exception as exc:
                warn(f"analyst estimates for {ticker} failed: {exc}")
                continue
            fetched_at = now_iso()
            rows = [(r["ticker"], r["as_of_date"], r["metric"], r["period"], r["value"], fetched_at) for r in records]
            conn.executemany(
                """
                INSERT INTO analyst_estimates (ticker, as_of_date, metric, period, value, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (ticker, as_of_date, metric, period) DO UPDATE SET value=excluded.value, fetched_at=excluded.fetched_at
                """,
                rows,
            )
            set_sync_state(conn, "analyst_estimates", ticker, date.today().isoformat(), fetched_at)


def main() -> None:
    init_db()
    print("[daily_sync] syncing S&P 500 universe...")
    sync_sp500_universe()

    tickers = get_sp500_tickers()
    ticker_to_cik = get_ticker_to_cik()

    sync_prices(tickers)
    sync_fundamentals(tickers)
    sync_sec_filings_and_form4(tickers, ticker_to_cik)
    sync_13f()
    sync_congressional()
    sync_fred()
    sync_short_interest(tickers)
    sync_analyst_estimates(tickers)

    print("[daily_sync] done")


if __name__ == "__main__":
    main()
