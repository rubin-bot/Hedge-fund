"use client";

import { useState } from "react";

import { getCommentary, getLPLetter } from "@/lib/api";
import { normalizeLlmText } from "@/lib/format";
import type { AICommentaryResponse, LPLetterResponse } from "@/lib/types";

import { AIIcon } from "./ui/icons";

interface AIInsightCardProps {
  kind: "commentary" | "lp-letter";
  title: string;
  initialData: AICommentaryResponse | LPLetterResponse;
}

function bodyText(data: AICommentaryResponse | LPLetterResponse): string {
  return "commentary" in data ? data.commentary : data.letter;
}

// AI-generated content must be clearly labeled as such (per the
// ui-ux-pro-max ux-guidelines "AI Interaction / Disclaimer" rule) and give
// the user a way to act on it rather than being static output-only (the
// same domain's "Feedback Loop" rule) -- the accent border/label and the
// regenerate button below are both here for that reason, not just style.
export function AIInsightCard({ kind, title, initialData }: AIInsightCardProps) {
  const [data, setData] = useState(initialData);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function regenerate() {
    setLoading(true);
    setError(null);
    try {
      const fresh = kind === "commentary" ? await getCommentary(true) : await getLPLetter(true);
      setData(fresh);
    } catch {
      setError("Regeneration failed -- the Gemini call may have hit a rate limit. Try again shortly.");
    } finally {
      setLoading(false);
    }
  }

  const paragraphs = normalizeLlmText(bodyText(data)).split(/\n{2,}/).filter(Boolean);

  return (
    <div className="rounded-lg border border-ai-accent/30 bg-ai-accent/5 p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <AIIcon className="h-4 w-4 text-ai-accent" />
          <span className="text-xs font-semibold uppercase tracking-wider text-ai-accent">{title} — AI Generated</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[11px] text-muted-foreground">
            {data.model} · {data.period_start} to {data.period_end} · {data.cached ? "cached" : "freshly generated"}
          </span>
          <button
            onClick={regenerate}
            disabled={loading}
            className="rounded border border-ai-accent/40 px-2.5 py-1 text-[11px] font-medium text-ai-accent hover:bg-ai-accent/10 disabled:opacity-50"
          >
            {loading ? "Regenerating…" : "Regenerate"}
          </button>
        </div>
      </div>

      {error && <p className="mb-2 text-xs text-short">{error}</p>}

      <div className="flex flex-col gap-2.5 text-sm leading-relaxed text-foreground/90">
        {paragraphs.map((paragraph, index) => (
          <p key={index}>{paragraph}</p>
        ))}
      </div>

      {"key_takeaways" in data && data.key_takeaways.length > 0 && (
        <ul className="mt-3 flex flex-col gap-1 border-t border-ai-accent/20 pt-3 text-xs text-muted-foreground">
          {data.key_takeaways.map((takeaway, index) => (
            <li key={index} className="flex gap-2">
              <span className="text-ai-accent">&bull;</span>
              {takeaway}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
