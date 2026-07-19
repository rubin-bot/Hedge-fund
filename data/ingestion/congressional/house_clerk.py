import re
import time
import zipfile
from datetime import datetime, timezone
from io import BytesIO

import pdfplumber
import requests

from config.settings import settings
from data.ingestion.congressional.base import SourceStructureError, cache_path, make_trade_id

USER_AGENT = f"{settings.sec_edgar_contact_name} ({settings.sec_edgar_contact_email})"
INDEX_URL_TEMPLATE = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip"
PTR_PDF_URL_TEMPLATE = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{doc_id}.pdf"
REQUEST_DELAY_SECONDS = 0.5
# Below this many consecutive zero-transaction PDFs, assume it's just normal
# variance (empty/withdrawn filings); above it, assume the PDF layout broke.
MIN_FILINGS_BEFORE_FAIL_FAST = 5

DATE_RE = re.compile(r"\d{2}/\d{2}/\d{4}")
AMOUNT_RE = re.compile(r"\$[\d,]+\s*-\s*\$[\d,]+|\$[\d,]+\+?|Over\s*\$[\d,.]+\s*\w*")
TICKER_RE = re.compile(r"\(([A-Z]{1,6}(?:[./][A-Z]{1,3})?)\)")
# Fallback pattern for rows where pdfplumber merges every column into one blob
# cell (happens for the first row of a table on some pages).
BLOB_RE = re.compile(
    r"^(?P<asset>.+?)\s+(?P<type>[SPE](?:\s*\(partial\)|\s*\(full\))?)\s+"
    r"(?P<date>\d{2}/\d{2}/\d{4})\s+(?P<notif>\d{2}/\d{2}/\d{4})\s+"
    r"(?P<amount>\$[\d,]+\s*-\s*\$[\d,]+|\$[\d,]+\+?|Over\s*\$[\d,.]+\s*\w*)",
    re.DOTALL,
)


def _is_date(value: str | None) -> bool:
    return bool(value and DATE_RE.fullmatch(value.strip()))


def fetch_filing_index(year: int) -> list[dict]:
    """Download and parse the House Clerk's annual financial disclosure index,
    filtered to Periodic Transaction Reports (FilingType 'P') — the filing type
    that actually contains individual stock trades.
    """
    url = INDEX_URL_TEMPLATE.format(year=year)
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    response.raise_for_status()

    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        txt_name = f"{year}FD.txt"
        if txt_name not in archive.namelist():
            raise SourceStructureError(
                f"House Clerk index ZIP for {year} doesn't contain {txt_name} — "
                "the index file naming/structure may have changed."
            )
        text = archive.read(txt_name).decode("utf-8", errors="replace")

    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        raise SourceStructureError(f"House Clerk index for {year} is empty.")

    header = lines[0].split("\t")
    required = {"Last", "First", "FilingType", "StateDst", "Year", "FilingDate", "DocID"}
    missing = required - set(header)
    if missing:
        raise SourceStructureError(
            f"House Clerk index header is missing expected columns {missing} — "
            "the index column layout may have changed."
        )

    records = []
    for line in lines[1:]:
        fields = line.split("\t")
        if len(fields) != len(header):
            continue
        row = dict(zip(header, fields))
        if row["FilingType"] == "P":
            records.append(row)
    return records


def _parse_row(row: list[str | None]) -> dict | None:
    if len(row) < 7:
        return None
    asset_cell, tx_type, date_cell, notif_cell, amount_cell = row[2], row[3], row[4], row[5], row[6]

    if (
        _is_date(date_cell)
        and _is_date(notif_cell)
        and amount_cell
        and AMOUNT_RE.search(amount_cell)
        and tx_type
        and tx_type.strip()[:1] in "SPE"
    ):
        ticker_match = TICKER_RE.search(asset_cell or "")
        return {
            "asset": (asset_cell or "").replace("\n", " ").strip(),
            "ticker": ticker_match.group(1) if ticker_match else None,
            "transaction_type": tx_type.strip(),
            "transaction_date": date_cell.strip(),
            "notification_date": notif_cell.strip(),
            "amount_range": amount_cell.strip(),
        }

    # Fallback: some rows squash every column into the first non-empty cell.
    blob = next((cell for cell in row if cell), None)
    if not blob:
        return None
    match = BLOB_RE.match(blob.replace("\n", " ").strip())
    if not match:
        return None
    ticker_match = TICKER_RE.search(match.group("asset"))
    return {
        "asset": match.group("asset").strip(),
        "ticker": ticker_match.group(1) if ticker_match else None,
        "transaction_type": match.group("type").strip(),
        "transaction_date": match.group("date"),
        "notification_date": match.group("notif"),
        "amount_range": match.group("amount").strip(),
    }


def fetch_ptr_pdf(year: int, doc_id: str) -> bytes:
    cached = cache_path("house", str(year), f"{doc_id}.pdf")
    if cached.exists():
        return cached.read_bytes()

    url = PTR_PDF_URL_TEMPLATE.format(year=year, doc_id=doc_id)
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    response.raise_for_status()
    cached.write_bytes(response.content)
    return response.content


def parse_ptr_pdf(pdf_bytes: bytes) -> list[dict]:
    transactions = []
    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                for row in table:
                    parsed = _parse_row(row)
                    if parsed:
                        transactions.append(parsed)
    return transactions


def get_trades(year: int, min_filing_date: str | None = None) -> list[dict]:
    """Fetch and parse all House Periodic Transaction Reports for a year.

    min_filing_date (MM/DD/YYYY string, matching the index's own format) restricts
    to filings on/after that date — used by the daily sync job to avoid re-parsing
    PDFs already ingested in a prior run.
    """
    filings = fetch_filing_index(year)
    if min_filing_date:
        min_date = datetime.strptime(min_filing_date, "%m/%d/%Y")
        filings = [f for f in filings if datetime.strptime(f["FilingDate"], "%m/%d/%Y") >= min_date]

    trades = []
    processed = 0
    zero_extraction = 0

    for filing in filings:
        member_name = f"{filing['First']} {filing['Last']}".strip()
        time.sleep(REQUEST_DELAY_SECONDS)

        try:
            pdf_bytes = fetch_ptr_pdf(year, filing["DocID"])
        except requests.HTTPError:
            continue  # individual missing/withdrawn filing — not a structural problem

        try:
            transactions = parse_ptr_pdf(pdf_bytes)
        except Exception as exc:
            raise SourceStructureError(f"Failed to parse PTR PDF {filing['DocID']}: {exc}") from exc

        processed += 1
        if not transactions:
            zero_extraction += 1
        elif processed >= MIN_FILINGS_BEFORE_FAIL_FAST and zero_extraction == processed - 1:
            # first filing that DID extract successfully after a suspicious run of
            # zeroes — not a structural break, just sparse data. Reset the counter.
            zero_extraction = 0

        if processed >= MIN_FILINGS_BEFORE_FAIL_FAST and zero_extraction == processed:
            raise SourceStructureError(
                f"The first {processed} House PTR PDFs all yielded zero parsed "
                "transactions — the PDF table layout has likely changed and "
                "house_clerk.py's parser needs updating."
            )

        fetched_at = datetime.now(timezone.utc).isoformat()
        for tx in transactions:
            trades.append(
                {
                    "trade_id": make_trade_id(
                        "house", member_name, tx["ticker"], tx["transaction_date"], tx["amount_range"], "house_clerk"
                    ),
                    "chamber": "house",
                    "member_name": member_name,
                    "ticker": tx["ticker"],
                    "asset_description": tx["asset"],
                    "transaction_type": tx["transaction_type"],
                    "transaction_date": tx["transaction_date"],
                    "disclosure_date": filing["FilingDate"],
                    "amount_range": tx["amount_range"],
                    "source": "house_clerk",
                    "filing_url": PTR_PDF_URL_TEMPLATE.format(year=year, doc_id=filing["DocID"]),
                    "fetched_at": fetched_at,
                }
            )

    return trades
