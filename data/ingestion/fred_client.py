from datetime import date

import pandas as pd
import requests

from config.settings import settings

BASE_URL = "https://api.stlouisfed.org/fred"

# FRED doesn't publish the (proprietary, Baltic Exchange-owned) Baltic Dry Index
# itself, so this is the closest free proxy: producer prices in the deep-sea
# dry/bulk freight transportation industry, which tracks the same underlying
# ocean-shipping cost cycle BDI represents, just measured as a PPI rather than
# a spot charter-rate index.
SERIES_PPI = "PPIACO"  # Producer Price Index for All Commodities
SERIES_DXY_PROXY = "DTWEXBGS"  # Nominal Broad U.S. Dollar Index (Fed's trade-weighted DXY proxy)
SERIES_BDI_PROXY = "PCU483111483111"  # PPI: Deep Sea Freight Transportation (NAICS 483111)


class FREDClient:
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.fred_api_key
        if not self.api_key:
            raise ValueError("FRED_API_KEY is not set in .env")

    def get_series(self, series_id: str, start: date | None = None, end: date | None = None) -> pd.DataFrame:
        params = {
            "series_id": series_id,
            "api_key": self.api_key,
            "file_type": "json",
        }
        if start:
            params["observation_start"] = start.isoformat()
        if end:
            params["observation_end"] = end.isoformat()

        response = requests.get(f"{BASE_URL}/series/observations", params=params, timeout=30)
        response.raise_for_status()
        payload = response.json()

        if "observations" not in payload:
            raise RuntimeError(f"FRED response for {series_id} has no 'observations' field: {payload}")

        df = pd.DataFrame(payload["observations"])
        df["date"] = pd.to_datetime(df["date"])
        df["value"] = pd.to_numeric(df["value"], errors="coerce")  # FRED uses "." for missing values
        return df[["date", "value"]].set_index("date")

    def get_ppi(self, start: date | None = None, end: date | None = None) -> pd.DataFrame:
        return self.get_series(SERIES_PPI, start, end)

    def get_dollar_index_proxy(self, start: date | None = None, end: date | None = None) -> pd.DataFrame:
        return self.get_series(SERIES_DXY_PROXY, start, end)

    def get_baltic_dry_proxy(self, start: date | None = None, end: date | None = None) -> pd.DataFrame:
        return self.get_series(SERIES_BDI_PROXY, start, end)
