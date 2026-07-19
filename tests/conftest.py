import pandas as pd
import pytest


@pytest.fixture
def sample_prices() -> pd.DataFrame:
    dates = pd.date_range("2025-01-01", periods=5, freq="B")
    return pd.DataFrame(
        {"AAA": [100, 101, 102, 101, 103], "BBB": [50, 49, 51, 52, 50]},
        index=dates,
    )
