"use client";

import { useState } from "react";

import { deposit } from "@/lib/api";

export function DepositForm({ onDeposited }: { onDeposited: () => void }) {
  const [amount, setAmount] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit() {
    const value = Number(amount);
    if (!value || value <= 0) {
      setError("Enter a positive amount.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await deposit(value);
      setAmount("");
      onDeposited();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Deposit failed");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex items-center gap-2">
      <input
        type="number"
        min={0}
        step={1000}
        placeholder="deposit amount"
        value={amount}
        onChange={(e) => setAmount(e.target.value)}
        className="w-36 rounded border border-border bg-secondary px-2 py-1.5 font-mono text-xs text-foreground"
      />
      <button
        onClick={handleSubmit}
        disabled={submitting}
        className="rounded border border-long/40 px-3 py-1.5 text-xs font-medium text-long hover:bg-long/10 disabled:opacity-50"
      >
        {submitting ? "Depositing…" : "Deposit Funds"}
      </button>
      {error && <span className="text-[11px] text-short">{error}</span>}
    </div>
  );
}
