import pandas as pd


def select_long_short_candidates(scores: pd.Series, num_longs: int, num_shorts: int) -> tuple[pd.Index, pd.Index]:
    ranked = scores.sort_values(ascending=False)
    longs = ranked.head(num_longs).index
    shorts = ranked.tail(num_shorts).index
    return longs, shorts


def equal_weight_positions(longs: pd.Index, shorts: pd.Index, gross_exposure: float) -> pd.Series:
    long_weight = gross_exposure / 2 / len(longs)
    short_weight = -gross_exposure / 2 / len(shorts)
    weights = pd.Series(long_weight, index=longs)
    weights = pd.concat([weights, pd.Series(short_weight, index=shorts)])
    return weights
