import pandas as pd


def value_factor(fundamentals: pd.DataFrame) -> pd.Series:
    # TODO: e.g. combine earnings yield, book-to-price, FCF yield
    raise NotImplementedError


def momentum_factor(prices: pd.DataFrame, lookback_days: int = 252, skip_days: int = 21) -> pd.Series:
    # TODO: trailing return over lookback_days, excluding the most recent skip_days
    raise NotImplementedError


def quality_factor(fundamentals: pd.DataFrame) -> pd.Series:
    # TODO: e.g. ROE, gross margin stability, debt/equity
    raise NotImplementedError


def growth_factor(fundamentals: pd.DataFrame) -> pd.Series:
    # TODO: e.g. revenue/earnings growth trends
    raise NotImplementedError
