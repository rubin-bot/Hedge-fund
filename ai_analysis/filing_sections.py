import re
import sqlite3
import warnings
from pathlib import Path

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

# Some filers' primary documents open with an XML declaration (XBRL viewer wrapper)
# even though the bulk of the document is HTML; bs4's "lxml" parser handles this fine
# in practice but warns about it on every call — silence it rather than switching
# parsers, since the warning doesn't reflect an actual parsing problem here.
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

from data.ingestion.sec_edgar_client import SECEdgarClient

# Standard SEC Regulation S-K item structure. Used by filing_structure_analyzer.py
# to spot missing/out-of-order items. 10-Qs reuse item numbers 1-4 across Part I
# and Part II (e.g. Part I Item 1 = Financial Statements, Part II Item 1 = Legal
# Proceedings) — extract_item_sections() below can't disambiguate which part a
# bare item number belongs to, so for 10-Qs only "1A" (unique to Part II) is
# reliably unambiguous. That's sufficient for the risk-factor analyzer, which
# only needs 1A; the structure analyzer treats duplicate 1-4 item numbers in a
# 10-Q as expected rather than an anomaly.
EXPECTED_10K_ITEMS = [
    "1", "1A", "1B", "2", "3", "4",  # Part I
    "5", "6", "7", "7A", "8", "9", "9A", "9B",  # Part II
    "10", "11", "12", "13", "14",  # Part III
    "15",  # Part IV
]
EXPECTED_10Q_ITEMS = [
    "1", "2", "3", "4",  # Part I
    "1", "1A", "2", "3", "4", "5", "6",  # Part II (numbers overlap Part I by design)
]

# TOC entries sit within a few dozen characters of each other (item number,
# short title, page number); a real heading is followed by substantial body
# text before the next item heading appears. This threshold is what
# distinguishes "this match is a TOC line" from "this match is the actual
# section start" without needing to locate "PART I"/"PART II" markers, which
# aren't consistently present in every filer's HTML.
MIN_REAL_SECTION_CHARS = 500

_ITEM_PATTERN = re.compile(r"Item\s+(\d{1,2}[A-Za-z]?)\.\s+\S", re.IGNORECASE)


class FilingSectionNotFoundError(RuntimeError):
    """Raised when a required item section (e.g. Item 1A for a 10-K/10-Q)
    can't be confidently located in a filing's text. This means the filing's
    HTML structure doesn't match what the parser expects — a human needs to
    look at it, not silently treat the section as empty.
    """


def get_filing_text(conn: sqlite3.Connection, ticker: str, accession_number: str) -> str:
    """Returns the HTML-stripped full text of a filing, fetching and caching
    it to disk on first use. Requires the filing to already be indexed in
    sec_filings (via data.backfill.backfill_sec_filings) — a missing row
    there is a real pipeline bug, not expected sparsity, so this raises
    rather than silently returning "".
    """
    row = conn.execute(
        "SELECT ticker, cik, primary_doc_url, local_path FROM sec_filings WHERE accession_number = ?",
        (accession_number,),
    ).fetchone()
    if row is None:
        raise FilingSectionNotFoundError(
            f"No sec_filings row for accession_number={accession_number!r} — "
            "run data.backfill.backfill_sec_filings for this ticker first."
        )
    _db_ticker, _cik, primary_doc_url, local_path = row

    if local_path and Path(local_path).exists():
        html_path = Path(local_path)
    else:
        html_path = SECEdgarClient().fetch_filing_text(accession_number, primary_doc_url)
        conn.execute(
            "UPDATE sec_filings SET local_path = ? WHERE accession_number = ?",
            (str(html_path), accession_number),
        )

    html = html_path.read_text(encoding="utf-8", errors="replace")
    return BeautifulSoup(html, "lxml").get_text(separator="\n")


def extract_item_sections(text: str, form_type: str) -> dict[str, str]:
    """Splits filing text into {item_number: section_text} by locating each
    "Item N." heading. See MIN_REAL_SECTION_CHARS above for how real headings
    are distinguished from the Table-of-Contents listing that precedes them.
    """
    matches = [(m.group(1).upper(), m.start()) for m in _ITEM_PATTERN.finditer(text)]
    if not matches:
        raise FilingSectionNotFoundError(
            f"No 'Item N.' headings found in {form_type} text ({len(text)} chars) — "
            "the filing's HTML structure may not match the expected format."
        )

    real_headings = []
    for i, (item_key, start) in enumerate(matches):
        next_start = matches[i + 1][1] if i + 1 < len(matches) else len(text)
        if next_start - start > MIN_REAL_SECTION_CHARS:
            real_headings.append((item_key, start))

    if not real_headings:
        raise FilingSectionNotFoundError(
            f"All {len(matches)} 'Item N.' matches in this {form_type} looked like "
            "Table-of-Contents entries (no substantial text followed any of them) — "
            "the filing's HTML structure may not match the expected format."
        )

    sections: dict[str, str] = {}
    for i, (item_key, start) in enumerate(real_headings):
        end = real_headings[i + 1][1] if i + 1 < len(real_headings) else len(text)
        sections[item_key] = text[start:end].strip()  # last occurrence wins if an item key repeats
    return sections


def get_risk_factors_section(conn: sqlite3.Connection, ticker: str, accession_number: str) -> str:
    row = conn.execute(
        "SELECT form_type FROM sec_filings WHERE accession_number = ?", (accession_number,)
    ).fetchone()
    if row is None:
        raise FilingSectionNotFoundError(f"No sec_filings row for accession_number={accession_number!r}")
    form_type = row[0]

    text = get_filing_text(conn, ticker, accession_number)
    sections = extract_item_sections(text, form_type)
    if "1A" not in sections:
        raise FilingSectionNotFoundError(
            f"Item 1A (Risk Factors) not found in {form_type} {accession_number} for {ticker}."
        )
    return sections["1A"]


def find_prior_filing(conn: sqlite3.Connection, ticker: str, accession_number: str) -> dict | None:
    """Finds the chronologically-prior filing of the same form_type for the
    same ticker, for the risk-factor change comparison. Returns None if this
    is the earliest filing of its type on record — an expected condition for
    a ticker's first backfilled filing, not an error.
    """
    current = conn.execute(
        "SELECT ticker, form_type, filing_date FROM sec_filings WHERE accession_number = ?",
        (accession_number,),
    ).fetchone()
    if current is None:
        raise FilingSectionNotFoundError(f"No sec_filings row for accession_number={accession_number!r}")
    _ticker, form_type, filing_date = current

    prior = conn.execute(
        """
        SELECT accession_number, filing_date FROM sec_filings
        WHERE ticker = ? AND form_type = ? AND filing_date < ? AND accession_number != ?
        ORDER BY filing_date DESC LIMIT 1
        """,
        (ticker, form_type, filing_date, accession_number),
    ).fetchone()
    if prior is None:
        return None
    return {"accession_number": prior[0], "filing_date": prior[1]}
