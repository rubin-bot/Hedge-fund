"""Scraper for the official Senate Electronic Financial Disclosure system
(efdsearch.senate.gov).

NOTE: from this development environment, efdsearch.senate.gov returns HTTP 403
from Akamai's edge bot protection on every request, regardless of User-Agent or
headers — confirmed by direct testing, not an assumption. That means this module
is implemented against the site's well-documented public flow (the same one used
by numerous long-running open-source Senate PTR scrapers) but could NOT be
exercised against the live site during development. Run `check_access()` first
in your actual deployment environment before relying on this for a real sync —
if it also 403s there, you're hitting the same bot protection and will need a
different network/IP or a headless-browser approach instead of plain requests.
"""

import re
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from config.settings import settings
from data.ingestion.congressional.base import SourceStructureError, make_trade_id

BASE_URL = "https://efdsearch.senate.gov"
HOME_URL = f"{BASE_URL}/search/home/"
SEARCH_URL = f"{BASE_URL}/search/"
SEARCH_DATA_URL = f"{BASE_URL}/search/report/data/"
REPORT_TYPE_PTR = 11  # Periodic Transaction Report, per the site's own filter codes
REQUEST_DELAY_SECONDS = 0.5

USER_AGENT = (
    f"{settings.sec_edgar_contact_name} ({settings.sec_edgar_contact_email}) "
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class SenateAccessBlockedError(SourceStructureError):
    """Raised when efdsearch.senate.gov rejects requests outright (e.g. bot
    protection), as opposed to the page loading but having a changed layout.
    Distinguishing these matters: this one means "try a different network,"
    not "go fix the parser."
    """


def check_access() -> None:
    response = requests.get(HOME_URL, headers={"User-Agent": USER_AGENT}, timeout=30)
    if response.status_code == 403:
        raise SenateAccessBlockedError(
            "efdsearch.senate.gov returned HTTP 403. This is Senate eFD's bot "
            "protection blocking this network/IP, not a code issue — retry from "
            "a different network, or use a headless-browser fetch instead of "
            "plain requests."
        )
    response.raise_for_status()


def _get_csrf_token(session: requests.Session, url: str) -> str:
    response = session.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    if response.status_code == 403:
        raise SenateAccessBlockedError(f"efdsearch.senate.gov returned HTTP 403 for {url}.")
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "lxml")
    token_input = soup.find("input", {"name": "csrfmiddlewaretoken"})
    if token_input is None or not token_input.get("value"):
        raise SourceStructureError(
            f"No csrfmiddlewaretoken input found on {url} — the Senate eFD page "
            "structure may have changed."
        )
    return token_input["value"]


def _start_session() -> requests.Session:
    session = requests.Session()
    token = _get_csrf_token(session, HOME_URL)

    response = session.post(
        HOME_URL,
        data={"csrfmiddlewaretoken": token, "prohibition_agreement": "1"},
        headers={"User-Agent": USER_AGENT, "Referer": HOME_URL},
        timeout=30,
    )
    if response.status_code == 403:
        raise SenateAccessBlockedError("efdsearch.senate.gov blocked the agreement POST.")
    response.raise_for_status()
    return session


def search_ptr_filings(session: requests.Session, start_date: str, end_date: str = "") -> list[dict]:
    """start_date / end_date as MM/DD/YYYY strings. Returns raw filing rows with
    member name, filed date, and the PTR report URL.
    """
    token = _get_csrf_token(session, SEARCH_URL)

    all_rows = []
    page_start = 0
    page_length = 100

    while True:
        payload = {
            "start": page_start,
            "length": page_length,
            "report_types": f"[{REPORT_TYPE_PTR}]",
            "filer_types": "[]",
            "submitted_start_date": f"{start_date} 00:00:00",
            "submitted_end_date": f"{end_date} 00:00:00" if end_date else "",
            "candidate_state": "",
            "senator_state": "",
            "office_id": "",
            "first_name": "",
            "last_name": "",
            "csrfmiddlewaretoken": token,
        }
        response = session.post(
            SEARCH_DATA_URL,
            data=payload,
            headers={"User-Agent": USER_AGENT, "Referer": SEARCH_URL, "X-Requested-With": "XMLHttpRequest"},
            timeout=30,
        )
        if response.status_code == 403:
            raise SenateAccessBlockedError("efdsearch.senate.gov blocked the search data POST.")
        response.raise_for_status()

        payload_json = response.json()
        if "data" not in payload_json:
            raise SourceStructureError(
                "Senate eFD search response has no 'data' field — the search API response shape may have changed."
            )

        rows = payload_json["data"]
        if not rows:
            break

        for row in rows:
            if len(row) < 5:
                raise SourceStructureError(
                    f"Senate eFD search row has {len(row)} columns, expected at least 5 — "
                    "the search result column layout may have changed."
                )
            link_soup = BeautifulSoup(row[3], "lxml")
            link = link_soup.find("a")
            if link is None or not link.get("href"):
                continue
            all_rows.append(
                {
                    "first_name": BeautifulSoup(row[0], "lxml").get_text(strip=True),
                    "last_name": BeautifulSoup(row[1], "lxml").get_text(strip=True),
                    "office": BeautifulSoup(row[2], "lxml").get_text(strip=True),
                    "report_url": BASE_URL + link["href"],
                    "filed_date": BeautifulSoup(row[4], "lxml").get_text(strip=True),
                }
            )

        if len(rows) < page_length:
            break
        page_start += page_length
        time.sleep(REQUEST_DELAY_SECONDS)

    return all_rows


def parse_ptr_report(session: requests.Session, report_url: str) -> list[dict]:
    response = session.get(report_url, headers={"User-Agent": USER_AGENT}, timeout=30)
    if response.status_code == 403:
        raise SenateAccessBlockedError(f"efdsearch.senate.gov blocked fetching {report_url}.")
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "lxml")
    table = soup.find("table")
    if table is None:
        # Some older/paper filings are scanned PDFs with no HTML transaction table.
        # That's normal, not a structural break — just nothing to parse here.
        return []

    header_cells = [th.get_text(strip=True).lower() for th in table.find_all("th")]
    if not header_cells:
        raise SourceStructureError(f"PTR report table at {report_url} has no header row.")

    def col_index(*names: str) -> int | None:
        for name in names:
            for i, cell in enumerate(header_cells):
                if name in cell:
                    return i
        return None

    ticker_col = col_index("ticker", "symbol")
    asset_col = col_index("asset")
    type_col = col_index("type")
    date_col = col_index("date")
    amount_col = col_index("amount")

    if date_col is None or type_col is None or amount_col is None:
        raise SourceStructureError(
            f"PTR report table at {report_url} is missing expected columns "
            f"(found headers: {header_cells}) — the table layout may have changed."
        )

    transactions = []
    for tr in table.find_all("tr")[1:]:
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if not cells or len(cells) <= max(filter(None, [ticker_col, asset_col, type_col, date_col, amount_col])):
            continue
        transactions.append(
            {
                "ticker": cells[ticker_col] if ticker_col is not None and cells[ticker_col] not in ("", "--") else None,
                "asset": cells[asset_col] if asset_col is not None else None,
                "transaction_type": cells[type_col],
                "transaction_date": cells[date_col],
                "amount_range": cells[amount_col],
            }
        )
    return transactions


def get_trades(start_date: str, end_date: str = "") -> list[dict]:
    """start_date / end_date as MM/DD/YYYY strings."""
    check_access()
    session = _start_session()
    filings = search_ptr_filings(session, start_date, end_date)

    trades = []
    for filing in filings:
        time.sleep(REQUEST_DELAY_SECONDS)
        member_name = f"{filing['first_name']} {filing['last_name']}".strip()
        transactions = parse_ptr_report(session, filing["report_url"])
        fetched_at = datetime.now(timezone.utc).isoformat()

        for tx in transactions:
            trades.append(
                {
                    "trade_id": make_trade_id(
                        "senate", member_name, tx["ticker"], tx["transaction_date"], tx["amount_range"], "senate_efd"
                    ),
                    "chamber": "senate",
                    "member_name": member_name,
                    "ticker": tx["ticker"],
                    "asset_description": tx["asset"],
                    "transaction_type": tx["transaction_type"],
                    "transaction_date": tx["transaction_date"],
                    "disclosure_date": filing["filed_date"],
                    "amount_range": tx["amount_range"],
                    "source": "senate_efd",
                    "filing_url": filing["report_url"],
                    "fetched_at": fetched_at,
                }
            )
    return trades
