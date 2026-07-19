import pandas as pd

from factors.congressional import compute_congressional_factor
from factors.crowding import detect_factor_crowding
from factors.insider_activity import compute_insider_activity
from factors.institutional_flow import compute_institutional_flow
from factors.scoring import composite_score, sector_neutral_zscore


def test_sector_neutral_zscore_compares_within_sector_only():
    values = pd.Series({"A": 1.0, "B": 2.0, "C": 3.0, "D": 10.0, "E": 20.0, "F": 30.0})
    sectors = pd.Series({"A": "Tech", "B": "Tech", "C": "Tech", "D": "Fin", "E": "Fin", "F": "Fin"})
    result = sector_neutral_zscore(values, sectors)
    # both sectors' middle ticker should land at 0 despite wildly different raw scales
    assert result["B"] == 0.0
    assert result["E"] == 0.0
    assert result["C"] > result["B"] > result["A"]


def test_composite_score_drops_sparse_optional_factor_and_renormalizes():
    momentum = pd.Series({"A": 1.0, "B": -1.0, "C": 0.5})
    value = pd.Series({"A": -1.0, "B": 1.0, "C": 0.0})
    congressional = pd.Series({"A": 2.0})  # only 1/3 covered

    composite, diagnostics = composite_score(
        {"momentum": momentum, "value": value, "congressional": congressional},
        {"momentum": 0.5, "value": 0.3, "congressional": 0.2},
        optional_factors={"congressional"},
        min_coverage=0.5,
    )
    assert "congressional" in diagnostics["dropped_factors"]
    # remaining weights (momentum 0.5, value 0.3) renormalize to sum to 1
    expected_a = (1.0 * 0.5 + -1.0 * 0.3) / 0.8
    assert abs(composite["A"] - expected_a) < 1e-9


def test_composite_score_renormalizes_per_ticker_for_missing_factor():
    momentum = pd.Series({"A": 1.0, "B": 1.0})
    value = pd.Series({"A": 1.0})  # B has no value score
    composite, _ = composite_score({"momentum": momentum, "value": value}, {"momentum": 0.5, "value": 0.5})
    assert abs(composite["B"] - 1.0) < 1e-9  # B scored entirely off momentum, weight renormalized to 1


def test_composite_score_raises_for_missing_required_factor():
    try:
        composite_score({"momentum": pd.Series(dtype=float)}, {"momentum": 1.0})
        assert False, "expected ValueError for a required factor with no data"
    except ValueError:
        pass


def test_crowding_detector_contributions_sum_to_one():
    tickers = [f"T{i}" for i in range(20)]
    a = pd.Series(range(20), index=tickers, dtype=float)
    b = pd.Series(range(20, 0, -1), index=tickers, dtype=float)
    c = pd.Series([i % 3 for i in range(20)], index=tickers, dtype=float)
    result = detect_factor_crowding({"a": a, "b": b, "c": c}, {"a": 0.34, "b": 0.33, "c": 0.33})
    assert abs(sum(result["contributions"].values()) - 1.0) < 1e-9


def test_crowding_detector_flags_dominant_factor():
    tickers = [f"T{i}" for i in range(20)]
    dominant = pd.Series(range(20), index=tickers, dtype=float)
    weak = pd.Series([i % 2 for i in range(20)], index=tickers, dtype=float)
    result = detect_factor_crowding(
        {"dominant": dominant, "weak": weak}, {"dominant": 0.9, "weak": 0.1}, threshold=0.4
    )
    assert result["crowded"]
    assert result["dominant_factor"] == "dominant"


def test_insider_activity_cluster_buy_beats_single_buyer_and_routine_codes_ignored():
    form4 = pd.DataFrame(
        [
            {"ticker": "AAA", "filer_name": "Alice", "transaction_code": "P", "shares": 1000, "price_per_share": 50, "transaction_date": "2026-07-01"},
            {"ticker": "AAA", "filer_name": "Bob", "transaction_code": "P", "shares": 1000, "price_per_share": 50, "transaction_date": "2026-07-05"},
            {"ticker": "BBB", "filer_name": "Carol", "transaction_code": "P", "shares": 1000, "price_per_share": 50, "transaction_date": "2026-07-01"},
            {"ticker": "CCC", "filer_name": "Dave", "transaction_code": "M", "shares": 5000, "price_per_share": 50, "transaction_date": "2026-07-01"},
        ]
    )
    market_cap = pd.Series({"AAA": 1e9, "BBB": 1e9, "CCC": 1e9})
    result = compute_insider_activity(form4, market_cap)
    assert result["AAA"] > result["BBB"]
    assert "CCC" not in result.index  # routine option-exercise code excluded entirely


def test_institutional_flow_needs_two_quarters():
    thirteen_f = pd.DataFrame(
        [
            {"ticker": "AAA", "report_period": "31-MAR-2026", "shares": 1000, "filer_cik": "1"},
            {"ticker": "AAA", "report_period": "30-JUN-2026", "shares": 1500, "filer_cik": "1"},
            {"ticker": "CCC", "report_period": "31-MAR-2026", "shares": 1000, "filer_cik": "1"},
        ]
    )
    result = compute_institutional_flow(thirteen_f)
    assert "AAA" in result.index
    assert "CCC" not in result.index  # only one quarter on file — can't compute a trend


def test_congressional_factor_degrades_gracefully_when_sparse():
    sparse = pd.DataFrame([{"ticker": "AAA", "member_name": "Rep X", "transaction_type": "P", "amount_range": "$1,001 - $15,000"}])
    assert compute_congressional_factor(sparse) is None

    empty = pd.DataFrame(columns=["ticker", "member_name", "transaction_type", "amount_range"])
    assert compute_congressional_factor(empty) is None
