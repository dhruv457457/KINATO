"use client";

/**
 * The recovery audit drawer - the screen the whole product is judged on:
 * for one recovery attempt, every real tool call the agent made, in order,
 * with its real decision, latency, and whether it ran degraded.
 *
 * Extracted out of app/dashboard/recoveries/page.tsx so the table page stays
 * about the table. Two real bugs fixed in the move: Escape didn't close it
 * (a modal overlay you can only dismiss by clicking exactly the backdrop),
 * and the page behind it kept scrolling under the overlay.
 */

import { useEffect, useState } from "react";
import { getRecoveryDetail, formatInr, AuditRow } from "@/lib/api";
import { StatusPill, Tone, toneForRecoveryState, RULE, RULE_SOFT, INK_MUTED, WASH, WARNING } from "./primitives";

export const STATE_LABELS: Record<string, string> = {
  RECOVERED: "Recovered",
  PAYMENT_LINK_SENT: "Offer sent",
  CALLING: "Calling",
  OUTREACH_APPROVED: "Approved",
  CREATED: "Created",
  CONSENT_REVOKED: "Opted out",
  CALL_FAILED: "Call failed",
};

export function RecoveryState({ state }: { state: string }) {
  return <StatusPill tone={toneForRecoveryState(state)}>{STATE_LABELS[state] || state}</StatusPill>;
}

function toneForDecision(decision: string): Tone {
  if (decision === "DENY" || decision === "REJECTED") return "negative";
  if (decision === "MODIFY") return "warning";
  return "positive";
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <>
      <div style={{ color: INK_MUTED }}>{label}</div>
      <div>{children}</div>
    </>
  );
}

export function RecoveryDrawer({
  recoveryAttemptId,
  onClose,
}: {
  recoveryAttemptId: string;
  onClose: () => void;
}) {
  const [detail, setDetail] = useState<{ recovery_attempt: any; audit_trail: AuditRow[] } | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setDetail(null);
    setFailed(false);
    getRecoveryDetail(recoveryAttemptId)
      .then((d) => !cancelled && setDetail(d))
      .catch(() => !cancelled && setFailed(true));
    return () => {
      cancelled = true;
    };
  }, [recoveryAttemptId]);

  // Escape to close, and freeze the page behind the overlay.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex justify-end anim-fade"
      style={{ background: "rgba(27,26,23,0.28)" }}
      onClick={onClose}
      role="presentation"
    >
      <div
        className="w-full max-w-[540px] h-full overflow-y-auto border-l anim-drawer"
        style={{ borderColor: RULE, background: "#FDFCF7" }}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={`Audit trail for ${recoveryAttemptId}`}
      >
        <div
          className="flex items-start justify-between border-b px-8 py-6 sticky top-0 z-10"
          style={{ borderColor: RULE, background: "#FDFCF7" }}
        >
          <div className="min-w-0">
            <div className="text-[11px] font-semibold uppercase tracking-widest mb-1" style={{ color: INK_MUTED }}>
              Audit trail
            </div>
            <h2 className="font-serif font-semibold text-xl truncate">{recoveryAttemptId}</h2>
          </div>
          <button
            onClick={onClose}
            aria-label="Close audit trail"
            className="text-2xl leading-none shrink-0 ml-4 transition-opacity hover:opacity-60"
            style={{ color: INK_MUTED }}
          >
            ×
          </button>
        </div>

        <div className="px-8 py-6">
          {failed && <p className="text-sm" style={{ color: INK_MUTED }}>Could not load this recovery attempt.</p>}
          {!detail && !failed && (
            <div className="space-y-3">
              {[0, 1, 2, 3].map((i) => (
                <div key={i} className="anim-shimmer h-[13px] rounded-sm" style={{ width: `${70 - i * 12}%` }} />
              ))}
            </div>
          )}

          {detail && (
            <>
              <div className="mb-8 text-sm grid grid-cols-2 gap-y-2.5">
                <Field label="Status">
                  <RecoveryState state={detail.recovery_attempt.state} />
                </Field>
                <Field label="Channel">{detail.recovery_attempt.channel || "—"}</Field>
                <Field label="Approved discount">
                  {detail.recovery_attempt.approved_discount_percent !== null
                    ? `${detail.recovery_attempt.approved_discount_percent}%`
                    : "—"}
                </Field>
                <Field label="Amount recovered">
                  <span className="tabular-nums">{formatInr(detail.recovery_attempt.attributed_revenue_paise)}</span>
                </Field>
                {detail.recovery_attempt.plan?.opening_line && (
                  <Field label="Opening line used">
                    <span className="italic">&ldquo;{detail.recovery_attempt.plan.opening_line}&rdquo;</span>
                  </Field>
                )}
              </div>

              <div
                className="text-[11px] font-semibold uppercase tracking-widest mb-4"
                style={{ color: INK_MUTED }}
              >
                Timeline — every real tool call, in order
              </div>

              {detail.audit_trail.length === 0 ? (
                <p className="text-sm" style={{ color: INK_MUTED }}>
                  No agent tool calls recorded for this attempt yet.
                </p>
              ) : (
                detail.audit_trail.map((row, i) => (
                  <div
                    key={row.audit_id}
                    className="border-t py-4 anim-row"
                    style={{ borderColor: RULE_SOFT, animationDelay: `${Math.min(i, 10) * 45}ms` }}
                  >
                    <div className="flex items-baseline justify-between mb-1.5 gap-3">
                      <span className="text-sm font-semibold">{row.action}</span>
                      <span className="text-[11px] tabular-nums shrink-0" style={{ color: INK_MUTED }}>
                        {new Date(row.created_at).toLocaleTimeString("en-IN")}
                      </span>
                    </div>
                    <div className="flex items-center flex-wrap gap-3 text-[12px] mb-1.5" style={{ color: INK_MUTED }}>
                      <span>{row.actor}</span>
                      {row.decision && (
                        <StatusPill tone={toneForDecision(row.decision)}>{row.decision}</StatusPill>
                      )}
                      {row.latency_ms !== null && <span className="tabular-nums">{row.latency_ms}ms</span>}
                      {row.degraded && (
                        <span className="text-[10px] uppercase font-semibold tracking-wide" style={{ color: WARNING }}>
                          degraded
                        </span>
                      )}
                    </div>
                    {row.result && (
                      <pre
                        className="text-[11px] p-2.5 overflow-x-auto"
                        style={{ background: WASH, fontFamily: "var(--font-geist-mono)" }}
                      >
                        {JSON.stringify(row.result, null, 2)}
                      </pre>
                    )}
                  </div>
                ))
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
