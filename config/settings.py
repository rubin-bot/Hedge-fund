from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    google_api_key: str = ""
    quiverquant_api_key: str = ""
    fred_api_key: str = ""
    fmp_api_key: str = ""

    # SEC requires a descriptive User-Agent (org + contact email) on every request.
    # Override these in .env with your real details before running against SEC EDGAR.
    sec_edgar_contact_name: str = "Hedge Fund Research"
    sec_edgar_contact_email: str = "research@example.com"

    data_dir: Path = PROJECT_ROOT / "data"
    raw_data_dir: Path = PROJECT_ROOT / "data" / "raw"
    processed_data_dir: Path = PROJECT_ROOT / "data" / "processed"
    db_path: Path = PROJECT_ROOT / "data" / "processed" / "research.db"


settings = Settings()
