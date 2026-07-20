import numpy as np

from api.engine_service import _num

# --- engine_service._num ----------------------------------------------------------


def test_num_casts_plain_and_numpy_scalars():
    assert _num(3) == 3.0
    assert _num(np.float64(1.5)) == 1.5
    assert _num(np.bool_(True)) == 1.0


def test_num_returns_none_for_missing():
    assert _num(None) is None
    assert _num(float("nan")) is None
    assert _num(np.nan) is None
