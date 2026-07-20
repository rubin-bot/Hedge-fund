import type {
  AccountOverviewResponse,
  AICommentaryResponse,
  CandidateAnalysisResponse,
  CandidatesResponse,
  CashBalance,
  FactorResponse,
  LPLetterResponse,
  PerformanceResponse,
  Position,
  PositionsResponse,
  RiskResponse,
  RunEndOfDayResponse,
  Side,
} from "./types";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

// `no-store`: every view here is live simulation state (the FastAPI layer
// already has its own pipeline-level TTL cache -- see api/cache.py), so
// Next's own fetch cache would just add a second, uncoordinated cache on
// top of that one.
async function apiGet<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, { cache: "no-store" });
  if (!response.ok) {
    const body = await response.text().catch(() => "");
    throw new Error(`API request to ${path} failed: ${response.status} ${body}`);
  }
  return response.json() as Promise<T>;
}

async function apiPost<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    cache: "no-store",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) {
    const errorBody = await response.text().catch(() => "");
    throw new Error(`API request to ${path} failed: ${response.status} ${errorBody}`);
  }
  return response.json() as Promise<T>;
}

// --- Factors / Risk / Performance --------------------------------------------

export function getFactors(): Promise<FactorResponse> {
  return apiGet<FactorResponse>("/api/factors");
}

export function getRisk(): Promise<RiskResponse> {
  return apiGet<RiskResponse>("/api/risk");
}

export function getPerformance(): Promise<PerformanceResponse> {
  return apiGet<PerformanceResponse>("/api/performance");
}

// --- AI Commentary -------------------------------------------------------------

export function getCommentary(regenerate = false): Promise<AICommentaryResponse> {
  const suffix = regenerate ? "?regenerate=true" : "";
  return apiGet<AICommentaryResponse>(`/api/ai/commentary${suffix}`);
}

export function getLPLetter(regenerate = false): Promise<LPLetterResponse> {
  const suffix = regenerate ? "?regenerate=true" : "";
  return apiGet<LPLetterResponse>(`/api/ai/lp-letter${suffix}`);
}

// --- Account / cash ledger -------------------------------------------------------

export function getAccountOverview(): Promise<AccountOverviewResponse> {
  return apiGet<AccountOverviewResponse>("/api/account/overview");
}

export function getBalance(): Promise<CashBalance> {
  return apiGet<CashBalance>("/api/account/balance");
}

export function deposit(amount: number): Promise<CashBalance> {
  return apiPost<CashBalance>("/api/account/deposit", { amount });
}

export function resetAccount(): Promise<{ status: string }> {
  return apiPost<{ status: string }>("/api/account/reset");
}

export function runEndOfDay(): Promise<RunEndOfDayResponse> {
  return apiPost<RunEndOfDayResponse>("/api/account/run-end-of-day");
}

// --- Candidates ------------------------------------------------------------------

export function getCandidates(numLongs?: number, numShorts?: number): Promise<CandidatesResponse> {
  const query = new URLSearchParams();
  if (numLongs) query.set("num_longs", String(numLongs));
  if (numShorts) query.set("num_shorts", String(numShorts));
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return apiGet<CandidatesResponse>(`/api/candidates${suffix}`);
}

export function getCandidateAnalysis(ticker: string): Promise<CandidateAnalysisResponse> {
  return apiGet<CandidateAnalysisResponse>(`/api/candidates/${encodeURIComponent(ticker)}/analysis`);
}

// --- Positions ---------------------------------------------------------------------

export function getPositions(): Promise<PositionsResponse> {
  return apiGet<PositionsResponse>("/api/positions");
}

export function executePosition(params: {
  ticker: string;
  side: Side;
  cashAmount: number;
  compositeScore?: number | null;
}): Promise<Position> {
  return apiPost<Position>("/api/positions/execute", {
    ticker: params.ticker,
    side: params.side,
    cash_amount: params.cashAmount,
    composite_score: params.compositeScore ?? null,
  });
}

export function closePosition(positionId: string): Promise<Position> {
  return apiPost<Position>(`/api/positions/${encodeURIComponent(positionId)}/close`);
}
