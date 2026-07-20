"use client";

import { useState } from "react";

import { executePosition, getCandidateAnalysis } from "@/lib/api";
import { formatPct } from "@/lib/format";
import type { Candidate, CandidateAnalysisResponse } from "@/lib/types";

import { AIAnalysisError, AIAnalysisLoading, AIAnalysisPanel } from "./ai-analysis-panel";
import { Badge } from "./ui/badge";
import { AIIcon } from "./ui/icons";

interface CandidateCardProps {
  candidate: Candidate;
  freeCash: number;
  onExecuted: () => void;
}

export function CandidateCard({ candidate, freeCash, onExecuted }: CandidateCardProps) {
  const [expanded, setExpanded] = useState(false);
  const [analysis, setAnalysis] = useState<CandidateAnalysisResponse | null>(null);
  const [analysisLoading, setAnalysisLoading] = useState(false);
  const [analysisError, setAnalysisError] = useState<string | null>(null);

  const [cashAmount, setCashAmount] = useState("");
  const [executing, setExecuting] = useState(false);
  const [executeError, setExecuteError] = useState<string | null>(null);

  const alreadyHeld = candidate.held_position_id !== null;
  const isFlip = alreadyHeld && candidate.held_side !== candidate.side;
  const sideColor = candidate.side === "long" ? "text-long" : "text-short";

  async function toggleExpand() {
    const next = !expanded;
    setExpanded(next);
    if (next && !analysis && !analysisLoading) {
      setAnalysisLoading(true);
      setAnalysisError(null);
      try {
        setAnalysis(await getCandidateAnalysis(candidate.ticker));
      } catch (err) {
        setAnalysisError(err instanceof Error ? err.message : "Unknown error");
      } finally {
        setAnalysisLoading(false);
      }
    }
  }

  async function handleExecute() {
    const amount = Number(cashAmount);
    if (!amount || amount <= 0) {
      setExecuteError("Enter a positive cash amount.");
      return;
    }
    if (amount > freeCash) {
      setExecuteError(`Exceeds free cash (${freeCash.toFixed(2)}).`);
      return;
    }
    setExecuting(true);
    setExecuteError(null);
    try {
      await executePosition({
        ticker: candidate.ticker,
        side: candidate.side,
        cashAmount: amount,
        compositeScore: candidate.composite_score,
      });
      setCashAmount("");
      onExecuted();
    } catch (err) {
      setExecuteError(err instanceof Error ? err.message : "Execute failed");
    } finally {
      setExecuting(false);
    }
  }

  return (
    <div className="rounded-lg border border-border bg-card p-3">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="font-mono text-sm font-semibold">{candidate.ticker}</span>
            {candidate.sector && <span className="truncate text-xs text-muted-foreground">{candidate.sector}</span>}
          </div>
          <div className="mt-0.5 flex items-center gap-3 font-mono text-xs text-muted-foreground">
            <span className={sideColor}>{candidate.side}</span>
            {candidate.composite_score !== null && <span>score {candidate.composite_score.toFixed(2)}</span>}
            {candidate.rank !== null && <span>rank {formatPct(candidate.rank, 0)}</span>}
          </div>
        </div>
        <button
          onClick={toggleExpand}
          className="flex shrink-0 items-center gap-1 rounded border border-ai-accent/40 px-2 py-1 text-[11px] font-medium text-ai-accent hover:bg-ai-accent/10"
        >
          <AIIcon className="h-3.5 w-3.5" />
          {expanded ? "Hide analysis" : "AI Analysis"}
        </button>
      </div>

      {alreadyHeld && (
        <div className="mt-2">
          <Badge tone={isFlip ? "warning" : "neutral"}>
            {isFlip ? `held ${candidate.held_side} — candidate is ${candidate.side}` : `already open (${candidate.held_side})`}
          </Badge>
        </div>
      )}

      {!alreadyHeld && (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <input
            type="number"
            min={0}
            step={100}
            placeholder="cash amount"
            value={cashAmount}
            onChange={(e) => setCashAmount(e.target.value)}
            className="w-32 rounded border border-border bg-secondary px-2 py-1 font-mono text-xs text-foreground"
          />
          <button
            onClick={handleExecute}
            disabled={executing}
            className={`rounded border px-3 py-1 text-xs font-medium disabled:opacity-50 ${
              candidate.side === "long"
                ? "border-long/40 text-long hover:bg-long/10"
                : "border-short/40 text-short hover:bg-short/10"
            }`}
          >
            {executing ? "Executing…" : `Execute ${candidate.side}`}
          </button>
          {executeError && <span className="text-[11px] text-short">{executeError}</span>}
        </div>
      )}

      {expanded && (
        <div className="mt-3">
          {analysisLoading && <AIAnalysisLoading />}
          {analysisError && <AIAnalysisError message={analysisError} />}
          {analysis && !analysisLoading && <AIAnalysisPanel analysis={analysis} />}
        </div>
      )}
    </div>
  );
}
