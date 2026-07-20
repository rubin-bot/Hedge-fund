import { AIInsightCard } from "@/components/ai-insight-card";
import { getCommentary, getLPLetter } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function AIPage() {
  const [commentary, lpLetter] = await Promise.all([getCommentary(), getLPLetter()]);

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="font-mono text-lg font-semibold">AI Commentary</h1>
        <p className="text-xs text-muted-foreground">
          Gemini-generated analysis over the week&rsquo;s positions opened/closed, risk report, and performance
          attribution.
        </p>
      </div>

      <AIInsightCard kind="commentary" title="Weekly Commentary" initialData={commentary} />
      <AIInsightCard kind="lp-letter" title="LP-Style Letter" initialData={lpLetter} />
    </div>
  );
}
