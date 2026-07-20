import type { CandidateAnalysisResponse } from "@/lib/types";

import { AIIcon } from "./ui/icons";

// Read-only explanation of why a candidate was surfaced -- factor drivers
// are computed (no LLM call), the three SEC-backed sections are cached
// Gemini verdicts reused verbatim from ai_analysis/. Any section can be
// legitimately unavailable (no filing on record, no prior filing to diff,
// SEC EDGAR unreachable) -- render that plainly rather than hiding it.
export function AIAnalysisPanel({ analysis }: { analysis: CandidateAnalysisResponse }) {
  return (
    <div className="flex flex-col gap-3 border-t border-ai-accent/20 pt-3">
      <div className="flex items-center gap-2">
        <AIIcon className="h-3.5 w-3.5 text-ai-accent" />
        <span className="text-[11px] font-semibold uppercase tracking-wider text-ai-accent">
          AI Analysis — Generated
        </span>
      </div>

      {analysis.factor_drivers.length > 0 && (
        <div>
          <SectionLabel>Top factor drivers</SectionLabel>
          <div className="flex flex-wrap gap-2">
            {analysis.factor_drivers.map((driver) => (
              <span
                key={driver.factor}
                className={`rounded border px-1.5 py-0.5 font-mono text-[11px] ${
                  driver.value >= 0 ? "border-long/30 text-long" : "border-short/30 text-short"
                }`}
              >
                {driver.factor.replace("_", " ")} {driver.value.toFixed(2)}
              </span>
            ))}
          </div>
        </div>
      )}

      <Section label="Filing structure">
        {analysis.filing_structure.available ? (
          <>
            <Badge2 tone={analysis.filing_structure.severity === "none" ? "ok" : "warn"}>
              {analysis.filing_structure.flagged ? `flagged — ${analysis.filing_structure.severity}` : "no anomalies"}
            </Badge2>
            {analysis.filing_structure.anomalies.length > 0 && (
              <ul className="mt-1 list-inside list-disc text-muted-foreground">
                {analysis.filing_structure.anomalies.map((a, i) => (
                  <li key={i}>{a}</li>
                ))}
              </ul>
            )}
            <p className="mt-1 text-muted-foreground">{analysis.filing_structure.rationale}</p>
          </>
        ) : (
          <Unavailable reason={analysis.filing_structure.unavailable_reason} />
        )}
      </Section>

      <Section label="Risk factor changes">
        {analysis.risk_factor_change.available ? (
          <>
            <Badge2 tone={analysis.risk_factor_change.has_material_changes ? "warn" : "ok"}>
              {analysis.risk_factor_change.has_material_changes ? "material changes" : "no material changes"}
            </Badge2>
            <p className="mt-1 text-muted-foreground">{analysis.risk_factor_change.summary}</p>
          </>
        ) : (
          <Unavailable reason={analysis.risk_factor_change.unavailable_reason} />
        )}
      </Section>

      <Section label="Insider activity (90d)">
        {analysis.insider_cluster.available ? (
          <>
            <Badge2 tone={analysis.insider_cluster.verdict === "meaningful_cluster_buy" ? "ok" : "neutral"}>
              {analysis.insider_cluster.verdict?.replace(/_/g, " ")}
            </Badge2>
            <p className="mt-1 text-muted-foreground">{analysis.insider_cluster.rationale}</p>
          </>
        ) : (
          <Unavailable reason={analysis.insider_cluster.unavailable_reason} />
        )}
      </Section>

      <Section label="Transcript sentiment">
        <Unavailable reason={analysis.transcript_sentiment_note} />
      </Section>
    </div>
  );
}

function SectionLabel({ children }: { children: string }) {
  return <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">{children}</div>;
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="text-xs">
      <SectionLabel>{label}</SectionLabel>
      {children}
    </div>
  );
}

function Unavailable({ reason }: { reason: string | null }) {
  return <p className="text-muted-foreground">Unavailable — {reason ?? "no data"}</p>;
}

function Badge2({ tone, children }: { tone: "ok" | "warn" | "neutral"; children: React.ReactNode }) {
  const classes =
    tone === "ok"
      ? "border-long/30 text-long"
      : tone === "warn"
        ? "border-warning/30 text-warning"
        : "border-border text-muted-foreground";
  return <span className={`inline-block rounded border px-1.5 py-0.5 text-[11px] ${classes}`}>{children}</span>;
}

export function AIAnalysisLoading() {
  return <p className="border-t border-ai-accent/20 pt-3 text-xs text-muted-foreground">Fetching SEC filings and running analysis — first look at a ticker can take up to a minute…</p>;
}

export function AIAnalysisError({ message }: { message: string }) {
  return <p className="border-t border-short/20 pt-3 text-xs text-short">Analysis failed: {message}</p>;
}
