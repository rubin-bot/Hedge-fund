from datetime import date, datetime, timezone
from io import StringIO

import pandas as pd
import requests

from config.settings import settings
from data.db import connection

WIKIPEDIA_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
REQUIRED_COLUMNS = ["Symbol", "Security", "GICS Sector", "GICS Sub-Industry", "CIK"]


class UniverseSourceError(RuntimeError):
    """Raised when the Wikipedia S&P 500 table no longer has the expected shape."""


def fetch_sp500_table() -> pd.DataFrame:
    headers = {
        "User-Agent": f"{settings.sec_edgar_contact_name} ({settings.sec_edgar_contact_email})"
    }
    response = requests.get(WIKIPEDIA_URL, headers=headers, timeout=30)
    response.raise_for_status()

    tables = pd.read_html(StringIO(response.text))
    constituents = tables[0]

    missing = [col for col in REQUIRED_COLUMNS if col not in constituents.columns]
    if missing:
        raise UniverseSourceError(
            f"Wikipedia S&P 500 table is missing expected columns {missing} — "
            "page layout has likely changed; fetch_sp500_table() needs updating."
        )
    return constituents


def sync_sp500_universe() -> int:
    constituents = fetch_sp500_table()
    as_of = date.today().isoformat()
    fetched_at = datetime.now(timezone.utc).isoformat()

    rows = [
        (
            record["Symbol"].replace(".", "-"),  # yfinance/most APIs use BRK-B, not BRK.B
            record["Security"],
            record["GICS Sector"],
            record["GICS Sub-Industry"],
            str(record["CIK"]).zfill(10),
            as_of,
        )
        for record in constituents.to_dict("records")
    ]

    with connection() as conn:
        conn.executemany(
            """
            INSERT INTO sp500_universe (ticker, company_name, sector, sub_industry, cik, as_of_date)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (ticker, as_of_date) DO UPDATE SET
                company_name = excluded.company_name,
                sector = excluded.sector,
                sub_industry = excluded.sub_industry,
                cik = excluded.cik
            """,
            rows,
        )
    return len(rows)


def get_sp500_tickers() -> list[str]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT ticker FROM sp500_universe
            WHERE as_of_date = (SELECT MAX(as_of_date) FROM sp500_universe)
            ORDER BY ticker
            """
        ).fetchall()
    return [r[0] for r in rows]


def get_ticker_to_cik() -> dict[str, str]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT ticker, cik FROM sp500_universe
            WHERE as_of_date = (SELECT MAX(as_of_date) FROM sp500_universe)
            """
        ).fetchall()
    return dict(rows)


def get_normalized_name_to_ticker() -> dict[str, str]:
    from data.ingestion.sec_edgar_client import normalize_company_name

    with connection() as conn:
        rows = conn.execute(
            """
            SELECT ticker, company_name FROM sp500_universe
            WHERE as_of_date = (SELECT MAX(as_of_date) FROM sp500_universe)
            """
        ).fetchall()
    return {normalize_company_name(name): ticker for ticker, name in rows}
