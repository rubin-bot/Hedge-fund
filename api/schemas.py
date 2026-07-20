"""Pydantic response models for the reporting API.

Every monetary/performance field here is SIMULATED (virtual cash ledger)
figures -- see simulation/virtual_ledger.py. Field names avoid implying
real brokerage execution or real money.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# --- Factors (unchanged; still driven by the model's composite scores) ----


class FactorBreakdown(BaseModel):
    ticker: str
    sector: str | None = None
    composite_score: float | None = None
    rank: float | None = None
    factors: dict[str, float | None] = Field(default_factory=dict)


class CrowdingReport(BaseModel):
    crowded: bool
    dominant_factor: str | None = None
    contributions: dict[str, float] = Field(default_factory=dict)
    threshold: float
    num_tickers_used: int | None = None
    reason: str | None = None


class FactorResponse(BaseModel):
    as_of: str
    dropped_factors: list[str]
    coverage: dict[str, float]
    holdings: list[FactorBreakdown]
    crowding: CrowdingReport


# --- Risk (reworked: analyzes the user's real open positions) -------------


class CircuitBreakerStatus(BaseModel):
    status: Literal["OK", "HALTED"]
    tripped: bool
    daily_loss_tripped: bool
    drawdown_tripped: bool
    latest_daily_return: float
    latest_drawdown: float
    reason: str


class RiskDecomposition(BaseModel):
    factor_pct: float | None = None
    specific_pct: float | None = None
    flagged: bool
    target_specific_pct: float | None = None
    tolerance: float | None = None
    reason: str


class CorrelationFlag(BaseModel):
    ticker_a: str
    ticker_b: str
    correlation: float


class StressScenarioResult(BaseModel):
    scenario: str
    window_start: str
    window_end: str
    portfolio_return: float
    coverage: float
    tickers_missing_data: list[str]


class RiskResponse(BaseModel):
    as_of: str
    has_open_positions: bool
    circuit_breaker: CircuitBreakerStatus | None = None  # None = not enough snapshot history yet
    decomposition: RiskDecomposition
    correlation_flags: list[CorrelationFlag]
    stress_tests: list[StressScenarioResult]
    position_limit_breaches: dict[str, float]
    sector_limit_breaches: dict[str, float]
    warnings: list[str]


# --- Performance (reworked: reads account_daily_snapshots / position_daily_marks) --


class EquityPoint(BaseModel):
    date: str
    equity: float


class PerformanceAttribution(BaseModel):
    beta_contribution: float | None = None
    sector_contribution: dict[str, float] = Field(default_factory=dict)
    factor_contribution: float | None = None
    alpha: float | None = None


class PerformanceResponse(BaseModel):
    has_data: bool
    start_date: str | None = None
    end_date: str | None = None
    starting_equity: float | None = None
    ending_equity: float | None = None
    total_return: float | None = None
    equity_curve: list[EquityPoint] = Field(default_factory=list)
    beta: float | None = None
    benchmark_ticker: str | None = None
    benchmark_return: float | None = None
    attribution: PerformanceAttribution | None = None
    monthly_returns: dict[str, float] = Field(default_factory=dict)


# --- AI Commentary (unchanged) ----------------------------------------------


class AICommentaryResponse(BaseModel):
    generated_at: str
    model: str
    period_start: str
    period_end: str
    commentary: str
    key_takeaways: list[str]
    cached: bool


class LPLetterResponse(BaseModel):
    generated_at: str
    model: str
    period_start: str
    period_end: str
    letter: str
    cached: bool


# --- Account / cash ledger --------------------------------------------------


class CashBalance(BaseModel):
    total_deposited: float
    free_cash: float
    cash_tied_up: float
    realized_pnl: float


class DepositRequest(BaseModel):
    amount: float


class RunEndOfDayResponse(BaseModel):
    as_of_date: str
    positions_marked: int
    unrealized_pnl: float
    total_account_value: float


# --- Candidates (today's ranked suggestions, ephemeral -- never persisted) --


class Candidate(BaseModel):
    ticker: str
    side: Literal["long", "short"]
    composite_score: float | None = None
    rank: float | None = None
    sector: str | None = None
    held_position_id: str | None = None  # set if this ticker is already an open position
    held_side: Literal["long", "short"] | None = None  # the side it's actually held on, if any


class CandidatesResponse(BaseModel):
    as_of: str
    longs: list[Candidate]
    shorts: list[Candidate]


class FactorDriver(BaseModel):
    factor: str
    value: float


class FilingStructureSection(BaseModel):
    available: bool
    flagged: bool | None = None
    severity: str | None = None
    anomalies: list[str] = Field(default_factory=list)
    rationale: str | None = None
    accession_number: str | None = None
    unavailable_reason: str | None = None


class RiskFactorChangeSection(BaseModel):
    available: bool
    has_material_changes: bool | None = None
    new_risks: list[str] = Field(default_factory=list)
    removed_risks: list[str] = Field(default_factory=list)
    intensified_risks: list[str] = Field(default_factory=list)
    summary: str | None = None
    unavailable_reason: str | None = None


class InsiderClusterSection(BaseModel):
    available: bool
    verdict: str | None = None
    distinct_buyers: int | None = None
    rationale: str | None = None
    unavailable_reason: str | None = None


class CandidateAnalysisResponse(BaseModel):
    ticker: str
    composite_score: float | None = None
    factor_drivers: list[FactorDriver]
    filing_structure: FilingStructureSection
    risk_factor_change: RiskFactorChangeSection
    insider_cluster: InsiderClusterSection
    transcript_sentiment_available: bool = False
    transcript_sentiment_note: str = "No free full-transcript data source is available in this system."


# --- Positions (the trade lifecycle: open -> held -> closed) ---------------


class Position(BaseModel):
    position_id: str
    ticker: str
    side: Literal["long", "short"]
    shares: float
    entry_price: float
    entry_date: str
    cost_basis: float
    composite_score_at_entry: float | None = None
    status: Literal["open", "closed"]
    exit_price: float | None = None
    exit_date: str | None = None
    realized_pnl: float | None = None
    created_at: str
    closed_at: str | None = None
    # latest EOD mark -- only meaningful while status == "open"; null until
    # the first end-of-day run after execution
    latest_price: float | None = None
    unrealized_pnl: float | None = None
    position_return_pct: float | None = None
    benchmark_return_pct: float | None = None
    excess_return_pct: float | None = None
    mark_date: str | None = None


class PositionsResponse(BaseModel):
    open: list[Position]
    closed: list[Position]


class ExecuteRequest(BaseModel):
    ticker: str
    side: Literal["long", "short"]
    cash_amount: float
    composite_score: float | None = None


class AccountOverviewResponse(BaseModel):
    balance: CashBalance
    positions: PositionsResponse
    circuit_breaker: CircuitBreakerStatus | None = None
    reconciled_today: bool
    latest_snapshot_date: str | None = None
