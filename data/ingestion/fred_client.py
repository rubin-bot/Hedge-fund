from datetime import date

import pandas as pd

from config.settings import settings

BASE_URL = "https://api.stlouisfed.org/fred"


class FREDClient:
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.fred_api_key

    def get_series(self, series_id: str, start: date, end: date) -> pd.DataFrame:
        # TODO: call {BASE_URL}/series/observations?series_id=...&api_key=...&file_type=json
        raise NotImplementedError
