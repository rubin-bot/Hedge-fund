import pandas as pd

from factors.scoring import zscore


def _safe_div(numerator, denominator):
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def compute_piotroski_f_score(history: pd.DataFrame) -> tuple[float, int] | None:
    """Piotroski F-Score: 9 binary pass/fail criteria comparing the latest
    fiscal period to the prior one, covering profitability, leverage/liquidity,
    and operating efficiency. Classically scored 0-9 (higher = stronger
    fundamentals). Two criteria (current ratio, gross margin change) require
    balance-sheet fields that free data doesn't provide for financial-sector
    companies (no current/non-current split, no gross profit line) — those
    criteria are simply excluded from both the numerator and denominator
    rather than scored as a fail, so a bank isn't penalized for a distinction
    that doesn't apply to how banks report.

    Returns (score_fraction, num_criteria_applicable) so a ticker missing some
    criteria is still comparable — sector-neutral z-scoring downstream doesn't
    care about the raw 0-9 scale, only relative standing.
    """
    if history is None or len(history) < 2:
        return None
    curr, prior = history.iloc[-1], history.iloc[-2]

    points = 0
    applicable = 0

    def score(condition: bool | None):
        nonlocal points, applicable
        if condition is None:
            return
        applicable += 1
        if condition:
            points += 1

    roa_curr = _safe_div(curr.get("net_income"), curr.get("total_assets"))
    roa_prior = _safe_div(prior.get("net_income"), prior.get("total_assets"))
    score(roa_curr is not None and roa_curr > 0)
    score(curr.get("operating_cash_flow") is not None and curr.get("operating_cash_flow") > 0)
    score(roa_curr is not None and roa_prior is not None and roa_curr > roa_prior)
    if curr.get("operating_cash_flow") is not None and curr.get("net_income") is not None:
        score(curr["operating_cash_flow"] > curr["net_income"])
    else:
        score(None)

    leverage_curr = _safe_div(curr.get("total_debt"), curr.get("total_assets"))
    leverage_prior = _safe_div(prior.get("total_debt"), prior.get("total_assets"))
    score(leverage_curr is not None and leverage_prior is not None and leverage_curr < leverage_prior)

    current_ratio_curr = _safe_div(curr.get("current_assets"), curr.get("current_liabilities"))
    current_ratio_prior = _safe_div(prior.get("current_assets"), prior.get("current_liabilities"))
    if current_ratio_curr is not None and current_ratio_prior is not None:
        score(current_ratio_curr > current_ratio_prior)
    else:
        score(None)  # not applicable — typically financial-sector balance sheets

    shares_curr, shares_prior = curr.get("shares_outstanding"), prior.get("shares_outstanding")
    if shares_curr is not None and shares_prior is not None:
        score(shares_curr <= shares_prior * 1.01)  # 1% tolerance for rounding/small buyback noise
    else:
        score(None)

    margin_curr = _safe_div(curr.get("gross_profit"), curr.get("revenue"))
    margin_prior = _safe_div(prior.get("gross_profit"), prior.get("revenue"))
    if margin_curr is not None and margin_prior is not None:
        score(margin_curr > margin_prior)
    else:
        score(None)

    turnover_curr = _safe_div(curr.get("revenue"), curr.get("total_assets"))
    turnover_prior = _safe_div(prior.get("revenue"), prior.get("total_assets"))
    score(turnover_curr is not None and turnover_prior is not None and turnover_curr > turnover_prior)

    if applicable == 0:
        return None
    return points / applicable, applicable


def compute_altman_z_score(history: pd.DataFrame, sector: str | None) -> float | None:
    """Classic 5-ratio Altman Z-Score, a bankruptcy/distress predictor:
    Z = 1.2*(working capital/assets) + 1.4*(retained earnings/assets)
      + 3.3*(EBIT/assets) + 0.6*(market cap/total liabilities) + 1.0*(revenue/assets)

    Deliberately excluded for the Financials sector: the model was built on
    manufacturers, and its leverage-heavy inputs treat bank balance sheets
    (where debt funds the core business, not distress) as automatically
    near-bankrupt — this is standard practice in credit/equity research, not
    a data-availability workaround.
    """
    if sector == "Financials" or history is None or history.empty:
        return None
    row = history.iloc[-1]

    total_assets = row.get("total_assets")
    if not total_assets:
        return None
    current_assets, current_liabilities = row.get("current_assets"), row.get("current_liabilities")
    if current_assets is None or current_liabilities is None:
        return None  # no current/non-current split — same limitation as the Piotroski current-ratio criterion

    working_capital = current_assets - current_liabilities
    retained_earnings = row.get("retained_earnings")
    ebit = row.get("ebit")
    market_cap = row.get("market_cap")
    total_liabilities = row.get("total_liabilities")
    revenue = row.get("revenue")

    if None in (retained_earnings, ebit, market_cap, total_liabilities, revenue) or not total_liabilities:
        return None

    return (
        1.2 * (working_capital / total_assets)
        + 1.4 * (retained_earnings / total_assets)
        + 3.3 * (ebit / total_assets)
        + 0.6 * (market_cap / total_liabilities)
        + 1.0 * (revenue / total_assets)
    )


def compute_quality(fundamentals_history: dict[str, pd.DataFrame], sector_map: pd.Series) -> pd.Series:
    piotroski, altman = {}, {}

    for ticker, history in fundamentals_history.items():
        f_score = compute_piotroski_f_score(history)
        if f_score is not None:
            piotroski[ticker] = f_score[0]

        z_score = compute_altman_z_score(history, sector_map.get(ticker))
        if z_score is not None:
            altman[ticker] = z_score

    components = pd.DataFrame({"piotroski": pd.Series(piotroski), "altman_z": pd.Series(altman)})
    if components.empty:
        return pd.Series(dtype=float)

    normalized = components.apply(zscore)
    return normalized.mean(axis=1, skipna=True)
