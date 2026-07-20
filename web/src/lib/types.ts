// Mirrors api/schemas.py exactly -- keep these two files in sync by hand
// (no shared codegen yet; the FastAPI layer is the source of truth).

export type Side = "long" | "short";

// --- Factors ----------------------------------------------------------------

export interface FactorBreakdown {
  ticker: string;
  sector: string | null;
  composite_score: number | null;
  rank: number | null;
  factors: Record<string, number | null>;
}

export interface CrowdingReport {
  crowded: boolean;
  dominant_factor: string | null;
  contributions: Record<string, number>;
  threshold: number;
  num_tickers_used: number | null;
  reason: string | null;
}

export interface FactorResponse {
  as_of: string;
  dropped_factors: string[];
  coverage: Record<string, number>;
  holdings: FactorBreakdown[];
  crowding: CrowdingReport;
}

// --- Risk (analyzes real open positions) -------------------------------------

export type CircuitBreakerStatusValue = "OK" | "HALTED";

export interface CircuitBreakerStatus {
  status: CircuitBreakerStatusValue;
  tripped: boolean;
  daily_loss_tripped: boolean;
  drawdown_tripped: boolean;
  latest_daily_return: number;
  latest_drawdown: number;
  reason: string;
}

export interface RiskDecomposition {
  factor_pct: number | null;
  specific_pct: number | null;
  flagged: boolean;
  target_specific_pct: number | null;
  tolerance: number | null;
  reason: string;
}

export interface CorrelationFlag {
  ticker_a: string;
  ticker_b: string;
  correlation: number;
}

export interface StressScenarioResult {
  scenario: string;
  window_start: string;
  window_end: string;
  portfolio_return: number;
  coverage: number;
  tickers_missing_data: string[];
}

export interface RiskResponse {
  as_of: string;
  has_open_positions: boolean;
  circuit_breaker: CircuitBreakerStatus | null; // null = not enough snapshot history yet
  decomposition: RiskDecomposition;
  correlation_flags: CorrelationFlag[];
  stress_tests: StressScenarioResult[];
  position_limit_breaches: Record<string, number>;
  sector_limit_breaches: Record<string, number>;
  warnings: string[];
}

// --- Performance --------------------------------------------------------------

export interface EquityPoint {
  date: string;
  equity: number;
}

export interface PerformanceAttribution {
  beta_contribution: number | null;
  sector_contribution: Record<string, number>;
  factor_contribution: number | null;
  alpha: number | null;
}

export interface PerformanceResponse {
  has_data: boolean;
  start_date: string | null;
  end_date: string | null;
  starting_equity: number | null;
  ending_equity: number | null;
  total_return: number | null;
  equity_curve: EquityPoint[];
  beta: number | null;
  benchmark_ticker: string | null;
  benchmark_return: number | null;
  attribution: PerformanceAttribution | null;
  monthly_returns: Record<string, number>;
}

// --- AI Commentary --------------------------------------------------------------

export interface AICommentaryResponse {
  generated_at: string;
  model: string;
  period_start: string;
  period_end: string;
  commentary: string;
  key_takeaways: string[];
  cached: boolean;
}

export interface LPLetterResponse {
  generated_at: string;
  model: string;
  period_start: string;
  period_end: string;
  letter: string;
  cached: boolean;
}

// --- Account / cash ledger -----------------------------------------------------

export interface CashBalance {
  total_deposited: number;
  free_cash: number;
  cash_tied_up: number;
  realized_pnl: number;
}

export interface RunEndOfDayResponse {
  as_of_date: string;
  positions_marked: number;
  unrealized_pnl: number;
  total_account_value: number;
}

// --- Candidates (today's ranked suggestions, ephemeral) -------------------------

export interface Candidate {
  ticker: string;
  side: Side;
  composite_score: number | null;
  rank: number | null;
  sector: string | null;
  held_position_id: string | null;
  held_side: Side | null;
}

export interface CandidatesResponse {
  as_of: string;
  longs: Candidate[];
  shorts: Candidate[];
}

export interface FactorDriver {
  factor: string;
  value: number;
}

export interface FilingStructureSection {
  available: boolean;
  flagged: boolean | null;
  severity: string | null;
  anomalies: string[];
  rationale: string | null;
  accession_number: string | null;
  unavailable_reason: string | null;
}

export interface RiskFactorChangeSection {
  available: boolean;
  has_material_changes: boolean | null;
  new_risks: string[];
  removed_risks: string[];
  intensified_risks: string[];
  summary: string | null;
  unavailable_reason: string | null;
}

export interface InsiderClusterSection {
  available: boolean;
  verdict: string | null;
  distinct_buyers: number | null;
  rationale: string | null;
  unavailable_reason: string | null;
}

export interface CandidateAnalysisResponse {
  ticker: string;
  composite_score: number | null;
  factor_drivers: FactorDriver[];
  filing_structure: FilingStructureSection;
  risk_factor_change: RiskFactorChangeSection;
  insider_cluster: InsiderClusterSection;
  transcript_sentiment_available: boolean;
  transcript_sentiment_note: string;
}

// --- Positions (the trade lifecycle: open -> held -> closed) --------------------

export type PositionStatus = "open" | "closed";

export interface Position {
  position_id: string;
  ticker: string;
  side: Side;
  shares: number;
  entry_price: number;
  entry_date: string;
  cost_basis: number;
  composite_score_at_entry: number | null;
  status: PositionStatus;
  exit_price: number | null;
  exit_date: string | null;
  realized_pnl: number | null;
  created_at: string;
  closed_at: string | null;
  latest_price: number | null;
  unrealized_pnl: number | null;
  position_return_pct: number | null;
  benchmark_return_pct: number | null;
  excess_return_pct: number | null;
  mark_date: string | null;
}

export interface PositionsResponse {
  open: Position[];
  closed: Position[];
}

export interface AccountOverviewResponse {
  balance: CashBalance;
  positions: PositionsResponse;
  circuit_breaker: CircuitBreakerStatus | null;
  reconciled_today: boolean;
  latest_snapshot_date: string | null;
}
