from pathlib import Path

from sec_edgar_downloader import Downloader

from config.settings import settings

# SEC requires a descriptive User-Agent (company name + contact email) on every request.
SEC_CONTACT_NAME = "Hedge Fund Research"
SEC_CONTACT_EMAIL = "research@example.com"


class SECEdgarClient:
    def __init__(self, download_dir: Path | None = None):
        self.download_dir = download_dir or settings.raw_data_dir / "sec_filings"
        self.downloader = Downloader(SEC_CONTACT_NAME, SEC_CONTACT_EMAIL, str(self.download_dir))

    def download_filing(self, ticker: str, form_type: str = "10-K", limit: int = 1) -> list[Path]:
        self.downloader.get(form_type, ticker, limit=limit, download_details=True)
        filing_root = self.download_dir / "sec-edgar-filings" / ticker / form_type
        if not filing_root.exists():
            return []
        return sorted(filing_root.glob("*/primary-document.html"))
