export function formatUsd(value: number, options?: { compact?: boolean }): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    notation: options?.compact ? "compact" : "standard",
    maximumFractionDigits: options?.compact ? 1 : 0,
  }).format(value);
}

export function formatPct(value: number, digits = 2): string {
  return `${(value * 100).toFixed(digits)}%`;
}

export function formatSignedPct(value: number, digits = 2): string {
  const sign = value > 0 ? "+" : "";
  return `${sign}${formatPct(value, digits)}`;
}

export function formatSignedUsd(value: number, options?: { compact?: boolean }): string {
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  return `${sign}${formatUsd(Math.abs(value), options)}`;
}

export function formatNumber(value: number, digits = 2): string {
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: digits }).format(value);
}

// Gemini's structured text output sometimes contains the literal two-character
// sequence "\n" (backslash + n) instead of a real newline inside a JSON string
// field -- observed on both /api/ai/commentary and /api/ai/lp-letter. Normalize
// either form before rendering so paragraph breaks always show up correctly.
export function normalizeLlmText(text: string): string {
  return text.replace(/\\n/g, "\n");
}
