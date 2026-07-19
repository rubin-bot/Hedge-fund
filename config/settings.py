from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    anthropic_api_key: str = ""
    fmp_api_key: str = ""
    polygon_api_key: str = ""
    quiverquant_api_key: str = ""
    fred_api_key: str = ""

    data_provider: Literal["fmp", "polygon"] = "fmp"

    data_dir: Path = PROJECT_ROOT / "data"
    raw_data_dir: Path = PROJECT_ROOT / "data" / "raw"
    processed_data_dir: Path = PROJECT_ROOT / "data" / "processed"


settings = Settings()
