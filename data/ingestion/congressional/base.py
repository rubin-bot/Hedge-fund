import hashlib
from pathlib import Path

from config.settings import settings

CACHE_DIR = settings.raw_data_dir / "congressional"


class SourceStructureError(RuntimeError):
    """Raised when a congressional data source's page/document layout no longer
    matches what the scraper expects. This should surface loudly (not be caught
    and swallowed) — it means a human needs to look at the source and update the
    parser, not that the data happens to be missing this run.
    """


def make_trade_id(
    chamber: str,
    member_name: str,
    ticker: str | None,
    transaction_date: str | None,
    amount_range: str | None,
    source: str,
) -> str:
    raw = "|".join([chamber, member_name, ticker or "", transaction_date or "", amount_range or "", source])
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def cache_path(*parts: str) -> Path:
    path = CACHE_DIR.joinpath(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
