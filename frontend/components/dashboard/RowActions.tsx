"use client";

/**
 * Per-row actions in the Recoveries table.
 *
 * Two buttons, deliberately plain text on a hairline: this system has no
 * filled buttons anywhere and adding one here would make a row action look
 * more important than the money in the column beside it.
 *
 * Retry is only offered where retrying is a real option. Offering it on a
 * recovered cart would invite a merchant to phone someone who has already
 * paid, which is the single worst thing this product can do - and the
 * backend would refuse it anyway, so the button would exist purely to be
 * declined.
 *
 * The retry result is shown inline, in words, because the honest answer is
 * often "no, and here is why": outside your calling hours, at the contact
 * cap, they opted out. A toast that says "failed" would turn a rule working
 * correctly into something that looks broken.
 */

import { useState } from "react";
import { retryRecovery, RetryResult } from "@/lib/api";
import { INK_MUTED, POSITIVE, RULE, WARNING } from "./primitives";

/** States where trying again is a real option. A recovered cart is done; a
 *  customer who opted out is permanently done. */
const RETRYABLE = new Set(["CALL_FAILED", "BLOCKED", "CREATED", "OUTREACH_APPROVED"]);

/** Plain English for the reasons a retry can be refused. Every one of these
 *  is the system working, not failing. */
const REFUSAL_COPY: Record<string, string> = {
  already_paid: "already paid",
  no_consent: "they opted out",
  quiet_hours: "outside calling hours",
  max_calls_reached: "at the contact cap",
  channel_cap_today: "already contacted today",
  promise_to_pay: "they promised a date",
};

export function RowActions({
  recoveryAttemptId,
  state,
  onExplain,
  onRetried,
}: {
  recoveryAttemptId: string;
  state: string;
  onExplain: () => void;
  /** Refetch the table. A retry does not change THIS row - that attempt is
   *  finished and its state is history - it opens a NEW one, which is
   *  invisible until the list reloads. Saying "restarted" while nothing
   *  on screen moved was the confusing part, not the behaviour. */
  onRetried: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<RetryResult | null>(null);

  const canRetry = RETRYABLE.has(state);

  async function handleRetry(e: React.MouseEvent) {
    e.stopPropagation(); // the row itself opens the drawer
    if (busy) return;
    setBusy(true);
    try {
      const r = await retryRecovery(recoveryAttemptId);
      setResult(r);
      if (r.started) onRetried();
    } catch {
      setResult({ started: false, reason: "error", detail: "Could not reach the server." });
    } finally {
      setBusy(false);
    }
  }

  if (result) {
    const good = result.started;
    return (
      <span
        className="text-[11.5px] leading-snug"
        style={{ color: good ? POSITIVE : INK_MUTED }}
        title={result.detail}
      >
        {good ? "new attempt above" : REFUSAL_COPY[result.reason] || result.reason || "not retried"}
      </span>
    );
  }

  return (
    <span className="flex items-center gap-3 whitespace-nowrap">
      {canRetry && (
        <button
          onClick={handleRetry}
          disabled={busy}
          className="text-[11.5px] underline underline-offset-2 transition-opacity hover:opacity-60 disabled:opacity-40"
          style={{ color: busy ? INK_MUTED : WARNING }}
        >
          {busy ? "trying…" : "Retry"}
        </button>
      )}
      <button
        onClick={(e) => {
          e.stopPropagation();
          onExplain();
        }}
        className="text-[11.5px] underline underline-offset-2 transition-opacity hover:opacity-60"
        style={{ color: INK_MUTED }}
      >
        Explain
      </button>
    </span>
  );
}

export { RULE };
