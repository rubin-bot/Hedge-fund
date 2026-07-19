import csv
import re
import time
import zipfile
from collections.abc import Iterator
from pathlib import Path
from xml.etree import ElementTree as ET

import requests
from sec_edgar_downloader import Downloader

from config.settings import settings

# SEC's fair-access policy asks for no more than ~10 requests/second; this keeps
# bulk per-ticker loops comfortably under that.
SEC_REQUEST_DELAY_SECONDS = 0.15
THIRTEEN_F_INDEX_URL = "https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets"
THIRTEEN_F_ZIP_PATTERN = re.compile(r"/files/structureddata/data/form-13f-data-sets/([\w.-]+_form13f\.zip)")

COMPANY_SUFFIXES = re.compile(
    r"\b(INC|INCORPORATED|CORP|CORPORATION|CO|COMPANY|LTD|LIMITED|LLC|PLC|GROUP|HOLDINGS?|THE)\b\.?"
)


def normalize_company_name(name: str) -> str:
    upper = re.sub(r"[^\w\s]", " ", name.upper())
    stripped = COMPANY_SUFFIXES.sub("", upper)
    return re.sub(r"\s+", " ", stripped).strip()


class SECEdgarClient:
    def __init__(self, download_dir: Path | None = None):
        self.download_dir = download_dir or settings.raw_data_dir / "sec_filings"
        self.user_agent = f"{settings.sec_edgar_contact_name} ({settings.sec_edgar_contact_email})"
        self.downloader = Downloader(
            settings.sec_edgar_contact_name, settings.sec_edgar_contact_email, str(self.download_dir)
        )

    def _get_json(self, url: str) -> dict:
        response = requests.get(url, headers={"User-Agent": self.user_agent}, timeout=30)
        response.raise_for_status()
        return response.json()

    def get_submissions(self, cik: str) -> dict:
        return self._get_json(f"https://data.sec.gov/submissions/CIK{str(cik).zfill(10)}.json")

    def document_url(self, cik: str, accession_number: str, document: str) -> str:
        cik_int = str(int(cik))
        acc_nodash = accession_number.replace("-", "")
        basename = document.split("/")[-1]
        return f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/{basename}"

    def list_filings(self, cik: str, form_types: list[str], include_history: bool = True) -> list[dict]:
        cik10 = str(cik).zfill(10)
        submissions = self.get_submissions(cik10)
        records = self._extract_filings(submissions["filings"]["recent"], cik10, form_types)

        if include_history:
            for page in submissions["filings"].get("files", []):
                time.sleep(SEC_REQUEST_DELAY_SECONDS)
                page_data = self._get_json(f"https://data.sec.gov/submissions/{page['name']}")
                records.extend(self._extract_filings(page_data, cik10, form_types))

        return records

    @staticmethod
    def _extract_filings(data: dict, cik10: str, form_types: list[str]) -> list[dict]:
        required = ["accessionNumber", "filingDate", "form", "primaryDocument"]
        missing = [field for field in required if field not in data]
        if missing:
            raise RuntimeError(
                f"SEC submissions payload is missing expected fields {missing} — "
                "the submissions API schema may have changed."
            )

        report_dates = data.get("reportDate", [])
        records = []
        for i, form in enumerate(data["form"]):
            if form not in form_types:
                continue
            records.append(
                {
                    "cik": cik10,
                    "accession_number": data["accessionNumber"][i],
                    "form_type": form,
                    "filing_date": data["filingDate"][i],
                    "period_of_report": report_dates[i] if i < len(report_dates) else None,
                    "primary_document": data["primaryDocument"][i],
                }
            )
        return records

    # --- 10-K / 10-Q full-document download (uses sec-edgar-downloader for the heavy lifting) ---

    def download_filing(self, ticker: str, form_type: str = "10-K", limit: int = 1) -> list[Path]:
        self.downloader.get(form_type, ticker, limit=limit, download_details=True)
        filing_root = self.download_dir / "sec-edgar-filings" / ticker / form_type
        if not filing_root.exists():
            return []
        return sorted(filing_root.glob("*/primary-document.html"))

    # --- Form 4 insider transactions ---

    def get_form4_transactions(self, cik: str, ticker: str, include_history: bool = False) -> list[dict]:
        filings = self.list_filings(cik, ["4", "4/A"], include_history=include_history)
        transactions = []
        for filing in filings:
            time.sleep(SEC_REQUEST_DELAY_SECONDS)
            url = self.document_url(filing["cik"], filing["accession_number"], filing["primary_document"])
            response = requests.get(url, headers={"User-Agent": self.user_agent}, timeout=30)
            response.raise_for_status()
            transactions.extend(
                _parse_form4_xml(response.text, filing["accession_number"], ticker, filing["cik"])
            )
        return transactions

    # --- Form 13F institutional holdings (quarterly bulk structured data set) ---

    def list_13f_dataset_urls(self) -> list[str]:
        response = requests.get(THIRTEEN_F_INDEX_URL, headers={"User-Agent": self.user_agent}, timeout=30)
        response.raise_for_status()
        matches = THIRTEEN_F_ZIP_PATTERN.findall(response.text)
        if not matches:
            raise RuntimeError(
                "No 13F data set ZIP links found on the SEC data sets page — "
                "the page layout may have changed."
            )
        return [f"https://www.sec.gov{'/files/structureddata/data/form-13f-data-sets/' + name}" for name in matches]

    def download_13f_dataset(self, zip_url: str) -> Path:
        filename = zip_url.rsplit("/", 1)[-1]
        dest = self.download_dir / "13f" / filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            return dest  # already cached — don't re-download an unchanged quarterly file

        response = requests.get(zip_url, headers={"User-Agent": self.user_agent}, stream=True, timeout=120)
        response.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in response.iter_content(chunk_size=1 << 20):
                f.write(chunk)
        return dest


def _find_text(elem: ET.Element | None, path: str) -> str | None:
    if elem is None:
        return None
    found = elem.find(path)
    if found is None or found.text is None:
        return None
    return found.text.strip()


def _find_float(elem: ET.Element | None, path: str) -> float | None:
    text = _find_text(elem, path)
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _parse_form4_xml(xml_text: str, accession_number: str, ticker: str, cik: str) -> list[dict]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise RuntimeError(f"Form 4 {accession_number} is not valid XML: {exc}") from exc

    if root.tag != "ownershipDocument":
        raise RuntimeError(
            f"Form 4 {accession_number} has unexpected root element '{root.tag}' "
            "(expected 'ownershipDocument') — the SEC Form 4 XML schema may have changed."
        )

    owner = root.find("reportingOwner")
    filer_name = _find_text(owner, "reportingOwnerId/rptOwnerName")
    relationship = owner.find("reportingOwnerRelationship") if owner is not None else None
    is_officer = _find_text(relationship, "isOfficer") == "true"
    is_director = _find_text(relationship, "isDirector") == "true"
    officer_title = _find_text(relationship, "officerTitle")

    transactions = []
    for table_name, tx_tag in (
        ("nonDerivativeTable", "nonDerivativeTransaction"),
        ("derivativeTable", "derivativeTransaction"),
    ):
        table = root.find(table_name)
        if table is None:
            continue
        for tx in table.findall(tx_tag):
            transactions.append(
                {
                    "accession_number": accession_number,
                    "ticker": ticker,
                    "cik": cik,
                    "filer_name": filer_name,
                    "is_officer": is_officer,
                    "is_director": is_director,
                    "officer_title": officer_title,
                    "transaction_date": _find_text(tx, "transactionDate/value"),
                    "transaction_code": _find_text(tx, "transactionCoding/transactionCode"),
                    "shares": _find_float(tx, "transactionAmounts/transactionShares/value"),
                    "price_per_share": _find_float(tx, "transactionAmounts/transactionPricePerShare/value"),
                    "shares_owned_after": _find_float(
                        tx, "postTransactionAmounts/sharesOwnedFollowingTransaction/value"
                    ),
                    "acquired_disposed": _find_text(tx, "transactionAmounts/transactionAcquiredDisposedCode/value"),
                }
            )
    return transactions


def parse_13f_holdings(zip_path: Path, universe_names_by_normalized: dict[str, str]) -> Iterator[dict]:
    """Stream a quarterly 13F structured data set ZIP and yield only holdings whose
    issuer name matches our S&P 500 universe (matched by normalized company name,
    since the bulk file doesn't carry tickers — only CUSIP/issuer name).
    """
    with zipfile.ZipFile(zip_path) as archive:
        filer_cik_by_accession = _read_tsv_column_map(archive, "SUBMISSION.tsv", "ACCESSION_NUMBER", "CIK")
        filer_name_by_accession = _read_tsv_column_map(
            archive, "COVERPAGE.tsv", "ACCESSION_NUMBER", "FILINGMANAGER_NAME"
        )
        report_period_by_accession = _read_tsv_column_map(
            archive, "SUBMISSION.tsv", "ACCESSION_NUMBER", "PERIODOFREPORT"
        )

        with archive.open("INFOTABLE.tsv") as raw:
            reader = csv.DictReader(_decode_lines(raw), delimiter="\t")
            required = {"ACCESSION_NUMBER", "NAMEOFISSUER", "CUSIP", "VALUE", "SSHPRNAMT"}
            missing = required - set(reader.fieldnames or [])
            if missing:
                raise RuntimeError(
                    f"INFOTABLE.tsv is missing expected columns {missing} — "
                    "the SEC 13F data set schema may have changed."
                )

            for row in reader:
                normalized = normalize_company_name(row["NAMEOFISSUER"])
                ticker = universe_names_by_normalized.get(normalized)
                if ticker is None:
                    continue
                accession = row["ACCESSION_NUMBER"]
                yield {
                    "report_period": report_period_by_accession.get(accession),
                    "filer_cik": filer_cik_by_accession.get(accession),
                    "filer_name": filer_name_by_accession.get(accession),
                    "cusip": row["CUSIP"],
                    "ticker": ticker,
                    "issuer_name": row["NAMEOFISSUER"],
                    "shares": _safe_float(row.get("SSHPRNAMT")),
                    "value_usd": _safe_float(row.get("VALUE"), scale=1000),  # VALUE is reported in $ thousands
                }


def _decode_lines(raw) -> Iterator[str]:
    for line in raw:
        yield line.decode("utf-8", errors="replace")


def _read_tsv_column_map(archive: zipfile.ZipFile, member: str, key_col: str, value_col: str) -> dict[str, str]:
    with archive.open(member) as raw:
        reader = csv.DictReader(_decode_lines(raw), delimiter="\t")
        if key_col not in (reader.fieldnames or []) or value_col not in (reader.fieldnames or []):
            raise RuntimeError(f"{member} is missing expected columns {key_col!r}/{value_col!r}")
        return {row[key_col]: row[value_col] for row in reader}


def _safe_float(text: str | None, scale: float = 1.0) -> float | None:
    if not text:
        return None
    try:
        return float(text) * scale
    except ValueError:
        return None
