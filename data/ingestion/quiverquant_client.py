import pandas as pd
import requests

from config.settings import settings

BASE_URL = "https://api.quiverquant.com/beta"


class QuiverQuantClient:
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.quiverquant_api_key

    def _get(self, path: str) -> list[dict]:
        headers = {"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"}
        response = requests.get(f"{BASE_URL}/{path}", headers=headers, timeout=30)
        response.raise_for_status()
        return response.json()

    def get_congress_trading(self, ticker: str) -> pd.DataFrame:
        return pd.DataFrame(self._get(f"historical/congresstrading/{ticker}"))

    def get_insider_trading(self, ticker: str) -> pd.DataFrame:
        # TODO: confirm the correct endpoint path — "historical/insiders/{ticker}" and
        # "historical/insidertrading/{ticker}" both 404 against the beta API as of this
        # writing; check current QuiverQuant docs before relying on this method.
        raise NotImplementedError
