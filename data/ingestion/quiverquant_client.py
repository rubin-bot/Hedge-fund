import pandas as pd

from config.settings import settings

BASE_URL = "https://api.quiverquant.com/beta"


class QuiverQuantClient:
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.quiverquant_api_key

    def get_congress_trading(self, ticker: str) -> pd.DataFrame:
        # TODO: call {BASE_URL}/historical/congresstrading/{ticker}
        raise NotImplementedError

    def get_insider_trading(self, ticker: str) -> pd.DataFrame:
        # TODO: call {BASE_URL}/historical/insiders/{ticker}
        raise NotImplementedError
