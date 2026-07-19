import argparse
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone

from config.settings import settings
from data.db import connection, init_db, set_sync_state
from data.ingestion.analyst_estimates import get_analyst_estimates
from data.ingestion.congressional import house_clerk, senate_efd
from data.ingestion.congressional.base import SourceStructureError
from data.ingestion.finra_short_interest import get_short_interest, recent_settlement_dates
from data.ingestion.fmp_client import FMPClient
from data.ingestion.fred_client import SERIES_BDI_PROXY, SERIES_DXY_PROXY, SERIES_PPI, FREDClient
from data.ingestion.fundamentals_normalizer import normalize_fmp_period, normalize_yfinance_period
from data.ingestion.sec_edgar_client import SECEdgarClient, parse_13f_holdings
from data.ingestion.yfinance_client import YFinanceClient
from data.universe import get_normalized_name_to_ticker, get_sp500_tickers, get_ticker_to_cik, sync_sp500_universe


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def warn(message: str) -> None:
    print(f"[backfill] WARNING: {message}", file=sys.stderr)


def backfill_prices(tickers: list[str], years: int) -> None:
    client = YFinanceClient()
    start = date.today() - timedelta(days=365 * years)
    print(f"[backfill] prices: {len(tickers)} tickers, {years}y history")

    # yfinance handles batches of tickers in one call far more efficiently than
    # one call per ticker, so this chunks rather than looping per-ticker.
    chunk_size = 50
    with connection() as conn:
        for i in range(0, len(tickers), chunk_size):
            chunk = tickers[i : i + chunk_size]
            try:
                df = client.get_ohlcv(chunk, start, date.today())
            except Exception as exc:
                warn(f"price chunk {chunk[:3]}... failed: {exc}")
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
                set_sync_state(conn, "yfinance_prices", ticker, date.today().isoformat(), now_iso())
            print(f"[backfill] prices: {i + len(chunk)}/{len(tickers)}")


def _store_normalized_fundamentals(conn, ticker: str, records: list[dict], fetched_at: str) -> None:
    rows = [
        (ticker, "normalized_annual", record["fiscal_date"], record["fiscal_date"], json.dumps(record), fetched_at)
        for record in records
        if record.get("fiscal_date")
    ]
    if not rows:
        return
    conn.executemany(
        """
        INSERT INTO fundamentals (ticker, statement_type, period, fiscal_date, payload_json, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (ticker, statement_type, period) DO UPDATE SET
            fiscal_date=excluded.fiscal_date, payload_json=excluded.payload_json, fetched_at=excluded.fetched_at
        """,
        rows,
    )


def _fundamentals_from_fmp(client: FMPClient, ticker: str) -> list[dict]:
    income = client.get_income_statement(ticker, period="annual", limit=10)
    balance = client.get_balance_sheet(ticker, period="annual", limit=10)
    ratios = client.get_ratios(ticker, period="annual", limit=10)
    if income.empty:
        return []

    balance_by_date = {row["date"]: row for row in balance.to_dict("records")} if not balance.empty else {}
    ratios_by_date = {row["date"]: row for row in ratios.to_dict("records")} if not ratios.empty else {}

    return [
        normalize_fmp_period(income_row, balance_by_date.get(income_row.get("date")), ratios_by_date.get(income_row.get("date")))
        for income_row in income.to_dict("records")
    ]


def _fundamentals_from_yfinance(client: YFinanceClient, ticker: str) -> list[dict]:
    statements = client.get_financial_statements(ticker)
    balance, income, cash_flow = statements["balance_sheet"], statements["income_statement"], statements["cash_flow"]
    if balance.empty:
        return []

    records = []
    for fiscal_ts in balance.index:
        fiscal_date = fiscal_ts.strftime("%Y-%m-%d")
        balance_row = balance.loc[fiscal_ts]
        income_row = income.loc[fiscal_ts] if fiscal_ts in income.index else None
        cash_flow_row = cash_flow.loc[fiscal_ts] if fiscal_ts in cash_flow.index else None
        records.append(
            normalize_yfinance_period(fiscal_date, balance_row, income_row, cash_flow_row, statements["market_cap"])
        )
    return records


def backfill_fundamentals(tickers: list[str]) -> None:
    fmp_client = FMPClient() if settings.fmp_api_key else None
    yf_client = YFinanceClient()
    if fmp_client:
        print(f"[backfill] fundamentals: {len(tickers)} tickers (FMP)")
    else:
        print(f"[backfill] fundamentals: {len(tickers)} tickers (yfinance free statements — no FMP_API_KEY set)")

    with connection() as conn:
        for n, ticker in enumerate(tickers, 1):
            try:
                if fmp_client:
                    records = _fundamentals_from_fmp(fmp_client, ticker)
                else:
                    records = _fundamentals_from_yfinance(yf_client, ticker)
            except Exception as exc:
                warn(f"fundamentals for {ticker} failed: {exc}")
                continue

            fetched_at = now_iso()
            _store_normalized_fundamentals(conn, ticker, records, fetched_at)
            set_sync_state(conn, "fundamentals", ticker, date.today().isoformat(), fetched_at)
            if n % 25 == 0:
                print(f"[backfill] fundamentals: {n}/{len(tickers)}")


def backfill_sec_filings(tickers: list[str], ticker_to_cik: dict[str, str]) -> None:
    client = SECEdgarClient()
    print(f"[backfill] SEC 10-K/10-Q index: {len(tickers)} tickers")

    with connection() as conn:
        for n, ticker in enumerate(tickers, 1):
            cik = ticker_to_cik.get(ticker)
            if not cik:
                continue
            try:
                filings = client.list_filings(cik, ["10-K", "10-Q"], include_history=True)
            except Exception as exc:
                warn(f"SEC filings for {ticker} failed: {exc}")
                continue

            fetched_at = now_iso()
            rows = [
                (
                    f["accession_number"],
                    ticker,
                    f["cik"],
                    f["form_type"],
                    f["filing_date"],
                    f["period_of_report"],
                    client.document_url(f["cik"], f["accession_number"], f["primary_document"]),
                    None,
                    fetched_at,
                )
                for f in filings
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
            if n % 25 == 0:
                print(f"[backfill] SEC filings: {n}/{len(tickers)}")


def backfill_form4(tickers: list[str], ticker_to_cik: dict[str, str]) -> None:
    client = SECEdgarClient()
    print(f"[backfill] Form 4 insider transactions: {len(tickers)} tickers")

    with connection() as conn:
        for n, ticker in enumerate(tickers, 1):
            cik = ticker_to_cik.get(ticker)
            if not cik:
                continue
            try:
                transactions = client.get_form4_transactions(cik, ticker, include_history=True)
            except Exception as exc:
                warn(f"Form 4 for {ticker} failed: {exc}")
                continue

            fetched_at = now_iso()
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
            set_sync_state(conn, "sec_form4", ticker, date.today().isoformat(), fetched_at)
            if n % 25 == 0:
                print(f"[backfill] Form 4: {n}/{len(tickers)}")


def backfill_13f(max_quarters: int = 4) -> None:
    client = SECEdgarClient()
    names = get_normalized_name_to_ticker()
    print(f"[backfill] 13F institutional holdings: last {max_quarters} data sets")

    urls = client.list_13f_dataset_urls()[:max_quarters]
    with connection() as conn:
        for url in urls:
            zip_path = client.download_13f_dataset(url)
            fetched_at = now_iso()
            rows = [
                (
                    h["report_period"], h["filer_cik"], h["filer_name"], h["cusip"],
                    h["ticker"], h["issuer_name"], h["shares"], h["value_usd"], fetched_at,
                )
                for h in parse_13f_holdings(zip_path, names)
            ]
            conn.executemany(
                """
                INSERT INTO sec_13f_holdings
                    (report_period, filer_cik, filer_name, cusip, ticker, issuer_name, shares, value_usd, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (report_period, filer_cik, cusip) DO UPDATE SET
                    shares=excluded.shares, value_usd=excluded.value_usd, fetched_at=excluded.fetched_at
                """,
                rows,
            )
            print(f"[backfill] 13F: {url.rsplit('/', 1)[-1]} -> {len(rows)} matching holdings")


def backfill_congressional(years: list[int]) -> None:
    print(f"[backfill] congressional trades: House years {years}")
    fetched_at = now_iso()
    with connection() as conn:
        for year in years:
            try:
                trades = house_clerk.get_trades(year)
            except SourceStructureError as exc:
                warn(f"House Clerk backfill for {year} aborted: {exc}")
                continue
            _insert_congressional_trades(conn, trades)
            print(f"[backfill] House {year}: {len(trades)} trades")

    print("[backfill] congressional trades: Senate (may be blocked by bot protection — see senate_efd.py)")
    try:
        start = f"01/01/{min(years)}"
        trades = senate_efd.get_trades(start)
        with connection() as conn:
            _insert_congressional_trades(conn, trades)
        print(f"[backfill] Senate: {len(trades)} trades")
    except SourceStructureError as exc:
        warn(f"Senate eFD backfill skipped: {exc}")


def _insert_congressional_trades(conn, trades: list[dict]) -> None:
    rows = [
        (
            t["trade_id"], t["chamber"], t["member_name"], t["ticker"], t["asset_description"],
            t["transaction_type"], t["transaction_date"], t["disclosure_date"], t["amount_range"],
            t["source"], t["filing_url"], t["fetched_at"],
        )
        for t in trades
    ]
    conn.executemany(
        """
        INSERT INTO congressional_trades
            (trade_id, chamber, member_name, ticker, asset_description, transaction_type,
             transaction_date, disclosure_date, amount_range, source, filing_url, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (trade_id) DO NOTHING
        """,
        rows,
    )


def backfill_fred(years: int) -> None:
    if not settings.fred_api_key:
        warn("FRED_API_KEY not set — skipping FRED backfill")
        return
    client = FREDClient()
    start = date.today() - timedelta(days=365 * years)
    print(f"[backfill] FRED series: PPI, DXY proxy, BDI proxy ({years}y)")

    with connection() as conn:
        for series_id in (SERIES_PPI, SERIES_DXY_PROXY, SERIES_BDI_PROXY):
            df = client.get_series(series_id, start, date.today())
            fetched_at = now_iso()
            rows = [
                (series_id, d.strftime("%Y-%m-%d"), v if v == v else None, fetched_at)  # NaN check
                for d, v in df["value"].items()
            ]
            conn.executemany(
                """
                INSERT INTO fred_series (series_id, date, value, fetched_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (series_id, date) DO UPDATE SET value=excluded.value, fetched_at=excluded.fetched_at
                """,
                rows,
            )
            set_sync_state(conn, "fred", series_id, date.today().isoformat(), fetched_at)
            print(f"[backfill] FRED {series_id}: {len(rows)} observations")


def backfill_short_interest(tickers: list[str], periods: int) -> None:
    print(f"[backfill] short interest: {len(tickers)} tickers, last {periods} settlement dates")
    dates = recent_settlement_dates(periods)

    with connection() as conn:
        for n, ticker in enumerate(tickers, 1):
            fetched_at = now_iso()
            rows = []
            for settlement_date in dates:
                df = get_short_interest(ticker, settlement_date)
                time.sleep(0.1)
                if df.empty:
                    continue
                row = df.iloc[0]
                rows.append(
                    (
                        ticker, str(row["settlementDate"]),
                        int(row["currentShortPositionQuantity"]), int(row["averageDailyVolumeQuantity"]),
                        float(row["daysToCoverQuantity"]), fetched_at,
                    )
                )
            if rows:
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
            set_sync_state(conn, "finra_short_interest", ticker, date.today().isoformat(), fetched_at)
            if n % 25 == 0:
                print(f"[backfill] short interest: {n}/{len(tickers)}")


def backfill_analyst_estimates(tickers: list[str]) -> None:
    print(f"[backfill] analyst estimates: {len(tickers)} tickers")
    with connection() as conn:
        for n, ticker in enumerate(tickers, 1):
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
            if n % 25 == 0:
                print(f"[backfill] analyst estimates: {n}/{len(tickers)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Full historical backfill of the data infrastructure layer")
    parser.add_argument("--tickers", nargs="+", help="Subset of tickers to backfill (default: full S&P 500)")
    parser.add_argument("--price-years", type=int, default=10)
    parser.add_argument("--fred-years", type=int, default=10)
    parser.add_argument("--congressional-years", nargs="+", type=int, default=[date.today().year])
    parser.add_argument("--short-interest-periods", type=int, default=26)
    parser.add_argument("--thirteen-f-quarters", type=int, default=4)
    parser.add_argument(
        "--skip",
        nargs="+",
        default=[],
        choices=["prices", "fundamentals", "sec", "form4", "13f", "congressional", "fred", "short-interest", "analyst-estimates"],
    )
    args = parser.parse_args()

    init_db()
    print("[backfill] syncing S&P 500 universe...")
    sync_sp500_universe()

    tickers = args.tickers or get_sp500_tickers()
    ticker_to_cik = get_ticker_to_cik()

    if "prices" not in args.skip:
        backfill_prices(tickers, args.price_years)
    if "fundamentals" not in args.skip:
        backfill_fundamentals(tickers)
    if "sec" not in args.skip:
        backfill_sec_filings(tickers, ticker_to_cik)
    if "form4" not in args.skip:
        backfill_form4(tickers, ticker_to_cik)
    if "13f" not in args.skip:
        backfill_13f(args.thirteen_f_quarters)
    if "congressional" not in args.skip:
        backfill_congressional(args.congressional_years)
    if "fred" not in args.skip:
        backfill_fred(args.fred_years)
    if "short-interest" not in args.skip:
        backfill_short_interest(tickers, args.short_interest_periods)
    if "analyst-estimates" not in args.skip:
        backfill_analyst_estimates(tickers)

    print("[backfill] done")


if __name__ == "__main__":
    main()
