import sqlite3
from contextlib import contextmanager
from pathlib import Path

from config.settings import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS sp500_universe (
    ticker TEXT NOT NULL,
    company_name TEXT,
    sector TEXT,
    sub_industry TEXT,
    cik TEXT,
    as_of_date TEXT NOT NULL,
    PRIMARY KEY (ticker, as_of_date)
);

CREATE TABLE IF NOT EXISTS prices_daily (
    ticker TEXT NOT NULL,
    date TEXT NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume INTEGER,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS fundamentals (
    ticker TEXT NOT NULL,
    statement_type TEXT NOT NULL,
    period TEXT NOT NULL,
    fiscal_date TEXT,
    payload_json TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (ticker, statement_type, period)
);

CREATE TABLE IF NOT EXISTS sec_filings (
    accession_number TEXT PRIMARY KEY,
    ticker TEXT NOT NULL,
    cik TEXT NOT NULL,
    form_type TEXT NOT NULL,
    filing_date TEXT NOT NULL,
    period_of_report TEXT,
    primary_doc_url TEXT,
    local_path TEXT,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sec_form4_transactions (
    accession_number TEXT NOT NULL,
    ticker TEXT NOT NULL,
    cik TEXT NOT NULL,
    filer_name TEXT,
    is_director INTEGER,
    is_officer INTEGER,
    officer_title TEXT,
    transaction_date TEXT,
    transaction_code TEXT,
    shares REAL,
    price_per_share REAL,
    shares_owned_after REAL,
    acquired_disposed TEXT,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (accession_number, transaction_date, transaction_code, shares)
);

CREATE TABLE IF NOT EXISTS sec_13f_holdings (
    report_period TEXT NOT NULL,
    filer_cik TEXT NOT NULL,
    filer_name TEXT,
    cusip TEXT NOT NULL,
    ticker TEXT,
    issuer_name TEXT,
    shares REAL,
    value_usd REAL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (report_period, filer_cik, cusip)
);

CREATE TABLE IF NOT EXISTS congressional_trades (
    trade_id TEXT PRIMARY KEY,
    chamber TEXT NOT NULL,
    member_name TEXT NOT NULL,
    ticker TEXT,
    asset_description TEXT,
    transaction_type TEXT,
    transaction_date TEXT,
    disclosure_date TEXT,
    amount_range TEXT,
    source TEXT NOT NULL,
    filing_url TEXT,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fred_series (
    series_id TEXT NOT NULL,
    date TEXT NOT NULL,
    value REAL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (series_id, date)
);

CREATE TABLE IF NOT EXISTS short_interest (
    ticker TEXT NOT NULL,
    settlement_date TEXT NOT NULL,
    short_interest_shares INTEGER,
    avg_daily_volume INTEGER,
    days_to_cover REAL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (ticker, settlement_date)
);

CREATE TABLE IF NOT EXISTS analyst_estimates (
    ticker TEXT NOT NULL,
    as_of_date TEXT NOT NULL,
    metric TEXT NOT NULL,
    period TEXT NOT NULL,
    value REAL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (ticker, as_of_date, metric, period)
);

-- Tracks the watermark each ingestion source has reached per entity, so daily
-- syncs can pull only what's new instead of re-fetching everything.
CREATE TABLE IF NOT EXISTS sync_state (
    source TEXT NOT NULL,
    entity_key TEXT NOT NULL,
    last_synced_at TEXT NOT NULL,
    last_data_date TEXT,
    PRIMARY KEY (source, entity_key)
);
"""


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or settings.db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: Path | None = None) -> None:
    with connection(db_path) as conn:
        conn.executescript(SCHEMA)


@contextmanager
def connection(db_path: Path | None = None):
    conn = get_connection(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def get_sync_state(conn: sqlite3.Connection, source: str, entity_key: str) -> str | None:
    row = conn.execute(
        "SELECT last_data_date FROM sync_state WHERE source = ? AND entity_key = ?",
        (source, entity_key),
    ).fetchone()
    return row[0] if row else None


def set_sync_state(conn: sqlite3.Connection, source: str, entity_key: str, last_data_date: str, synced_at: str) -> None:
    conn.execute(
        """
        INSERT INTO sync_state (source, entity_key, last_synced_at, last_data_date)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (source, entity_key)
        DO UPDATE SET last_synced_at = excluded.last_synced_at, last_data_date = excluded.last_data_date
        """,
        (source, entity_key, synced_at, last_data_date),
    )
