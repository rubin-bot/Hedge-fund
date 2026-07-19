from datetime import date, timedelta

import pandas as pd
import requests

BASE_URL = "https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest"


def _last_day_of_month(year: int, month: int) -> date:
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


def _nearest_prior_weekday(d: date) -> date:
    while d.weekday() >= 5:  # Sat=5, Sun=6
        d -= timedelta(days=1)
    return d


def recent_settlement_dates(n: int = 8) -> list[date]:
    """FINRA short interest settlement dates fall on the 15th and last calendar
    day of each month (moved back to the nearest weekday). This walks backward
    from today generating candidates, most recent first — the FINRA-published
    holiday-adjusted schedule can differ by a day or two around holidays, which
    is why callers should try several candidates rather than assume the first one.
    """
    candidates: set[date] = set()
    year, month = date.today().year, date.today().month
    while len({d for d in candidates if d <= date.today()}) < n:
        candidates.add(_nearest_prior_weekday(_last_day_of_month(year, month)))
        candidates.add(_nearest_prior_weekday(date(year, month, 15)))
        month -= 1
        if month == 0:
            month, year = 12, year - 1
    return sorted((d for d in candidates if d <= date.today()), reverse=True)[:n]


def get_short_interest(ticker: str, settlement_date: date) -> pd.DataFrame:
    payload = {
        "limit": 10,
        "compareFilters": [
            {"compareType": "EQUAL", "fieldName": "symbolCode", "fieldValue": ticker},
            {"compareType": "EQUAL", "fieldName": "settlementDate", "fieldValue": settlement_date.isoformat()},
        ],
    }
    response = requests.post(BASE_URL, json=payload, headers={"Accept": "application/json"}, timeout=30)
    response.raise_for_status()
    if not response.text.strip():
        return pd.DataFrame()  # no filing published for this settlement date yet
    return pd.DataFrame(response.json())


def get_latest_short_interest(ticker: str, max_lookback: int = 8) -> pd.DataFrame:
    for settlement_date in recent_settlement_dates(max_lookback):
        df = get_short_interest(ticker, settlement_date)
        if not df.empty:
            return df
    return pd.DataFrame()
