"use client";

import { useEffect, useState } from "react";
import { getRecoveries, getRecoveryDetail, formatInr, RecoveryRow, AuditRow } from "@/lib/api";

const STATE_LABELS: Record<string, { label: string; color: string }> = {
  RECOVERED: { label: "Recovered", color: "#1F6B3C" },
  PAYMENT_LINK_SENT: { label: "Offer sent", color: "#8A5A1A" },
  CALLING: { label: "Calling", color: "#44433C" },
  OUTREACH_APPROVED: { label: "Approved", color: "#44433C" },
  CREATED: { label: "Created", color: "#8A8678" },
  CONSENT_REVOKED: { label: "Opted out", color: "#A3372A" },
  CALL_FAILED: { label: "Call failed", color: "#A3372A" },
};

function stateBadge(state: string) {
  const meta = STATE_LABELS[state] || { label: state, color: "#8A8678" };
  return (
    <span className="text-[11px] font-semibold uppercase tracking-wide" style={{ color: meta.color }}>
      {meta.label}
    </span>
  );
}

export default function RecoveriesPage() {
  const [rows, setRows] = useState<RecoveryRow[] | null>(null);
  const [error, setError] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  useEffect(() => {
    getRecoveries()
      .then((d) => setRows(d.recoveries))
      .catch(() => setError("Could not load recoveries right now."));
  }, []);

  return (
    <>
      <header className="border-b px-14 py-10" style={{ borderColor: "#D5D0BC" }}>
        <div className="text-[11px] font-semibold uppercase tracking-widest text-dark-200 mb-2">Recoveries</div>
        <h1 className="font-serif font-semibold text-[28px]">Every attempt, real and auditable</h1>
      </header>

      <div className="flex-1 px-14 py-10 w-full">
        {error && <p className="text-sm text-red-700">{error}</p>}
        {!rows && !error && <p className="text-sm text-dark-200">Loading…</p>}

        {rows && rows.length === 0 && (
          <div className="border p-6 text-sm text-dark-200 max-w-lg" style={{ borderColor: "#D5D0BC" }}>
            No recovery attempts yet. Once a real payment fails on your store, one appears here automatically.
          </div>
        )}

        {rows && rows.length > 0 && (
          <div className="overflow-x-auto border" style={{ borderColor: "#D5D0BC" }}>
            <table className="w-full text-[13px]" style={{ borderCollapse: "collapse" }}>
              <thead>
                <tr className="border-b" style={{ borderColor: "#D5D0BC", background: "#F5F3E9" }}>
                  {["Customer", "Cart", "Status", "Discount", "Recovered", "When"].map((h) => (
                    <th
                      key={h}
                      className="text-left px-4 py-3 text-[10.5px] font-semibold uppercase tracking-wider text-dark-200"
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr
                    key={r.recovery_attempt_id}
                    onClick={() => setSelectedId(r.recovery_attempt_id)}
                    className="border-b cursor-pointer hover:bg-[#F5F3E9] transition-colors"
                    style={{ borderColor: "#EAE6D5" }}
                  >
                    <td className="px-4 py-3">{r.customer_name || r.customer_phone || "—"}</td>
                    <td className="px-4 py-3 tabular-nums">{formatInr(r.cart_amount_paise)}</td>
                    <td className="px-4 py-3">{stateBadge(r.state)}</td>
                    <td className="px-4 py-3 tabular-nums">
                      {r.approved_discount_percent !== null ? `${r.approved_discount_percent}%` : "—"}
                    </td>
                    <td className="px-4 py-3 tabular-nums">{formatInr(r.attributed_revenue_paise)}</td>
                    <td className="px-4 py-3 text-dark-200">{new Date(r.created_at).toLocaleString("en-IN")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {selectedId && <RecoveryDrawer recoveryAttemptId={selectedId} onClose={() => setSelectedId(null)} />}
    </>
  );
}

function RecoveryDrawer({ recoveryAttemptId, onClose }: { recoveryAttemptId: string; onClose: () => void }) {
  const [detail, setDetail] = useState<{ recovery_attempt: any; audit_trail: AuditRow[] } | null>(null);

  useEffect(() => {
    getRecoveryDetail(recoveryAttemptId).then(setDetail).catch(() => setDetail(null));
  }, [recoveryAttemptId]);

  return (
    <div className="fixed inset-0 z-50 flex justify-end" style={{ background: "rgba(27,26,23,0.25)" }} onClick={onClose}>
      <div
        className="w-full max-w-[520px] h-full bg-background overflow-y-auto border-l"
        style={{ borderColor: "#D5D0BC" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b px-8 py-6" style={{ borderColor: "#D5D0BC" }}>
          <div>
            <div className="text-[11px] font-semibold uppercase tracking-widest text-dark-200 mb-1">Audit trail</div>
            <h2 className="font-serif font-semibold text-xl">{recoveryAttemptId}</h2>
          </div>
          <button onClick={onClose} className="text-dark-200 hover:text-dark text-2xl leading-none">
            ×
          </button>
        </div>

        <div className="px-8 py-6">
          {!detail && <p className="text-sm text-dark-200">Loading…</p>}
          {detail && (
            <>
              <div className="mb-8 text-sm">
                <div className="grid grid-cols-2 gap-y-2">
                  <div className="text-dark-200">Status</div>
                  <div>{stateBadge(detail.recovery_attempt.state)}</div>
                  <div className="text-dark-200">Channel</div>
                  <div>{detail.recovery_attempt.channel || "—"}</div>
                  <div className="text-dark-200">Approved discount</div>
                  <div>
                    {detail.recovery_attempt.approved_discount_percent !== null
                      ? `${detail.recovery_attempt.approved_discount_percent}%`
                      : "—"}
                  </div>
                  <div className="text-dark-200">Amount recovered</div>
                  <div>{formatInr(detail.recovery_attempt.attributed_revenue_paise)}</div>
                  {detail.recovery_attempt.plan?.opening_line && (
                    <>
                      <div className="text-dark-200">Opening line used</div>
                      <div className="italic">&ldquo;{detail.recovery_attempt.plan.opening_line}&rdquo;</div>
                    </>
                  )}
                </div>
              </div>

              <div className="text-[11px] font-semibold uppercase tracking-widest text-dark-200 mb-4">
                Timeline — every real tool call, in order
              </div>
              {detail.audit_trail.length === 0 && (
                <p className="text-sm text-dark-200">No agent tool calls recorded for this attempt yet.</p>
              )}
              <div>
                {detail.audit_trail.map((row) => (
                  <div key={row.audit_id} className="border-t py-4" style={{ borderColor: "#EAE6D5" }}>
                    <div className="flex items-baseline justify-between mb-1.5">
                      <span className="text-sm font-semibold">{row.action}</span>
                      <span className="text-[11px] text-dark-200 tabular-nums">
                        {new Date(row.created_at).toLocaleTimeString("en-IN")}
                      </span>
                    </div>
                    <div className="flex items-center gap-3 text-[12px] text-dark-200 mb-1.5">
                      <span>{row.actor}</span>
                      {row.decision && (
                        <span
                          className="font-semibold"
                          style={{ color: row.decision === "DENY" || row.decision === "REJECTED" ? "#A3372A" : "#1F6B3C" }}
                        >
                          {row.decision}
                        </span>
                      )}
                      {row.latency_ms !== null && <span>{row.latency_ms}ms</span>}
                      {row.degraded && (
                        <span className="text-[10px] uppercase font-semibold" style={{ color: "#8A5A1A" }}>
                          degraded
                        </span>
                      )}
                    </div>
                    {row.result && (
                      <pre
                        className="text-[11px] p-2.5 overflow-x-auto"
                        style={{ background: "#F5F3E9", fontFamily: "var(--font-geist-mono)" }}
                      >
                        {JSON.stringify(row.result, null, 2)}
                      </pre>
                    )}
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
