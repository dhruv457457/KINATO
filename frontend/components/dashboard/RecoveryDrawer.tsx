"use client";

/**
 * The recovery audit drawer - the screen the whole product is judged on.
 *
 * It used to show every real tool call the agent made and none of the
 * conversation those calls were a response to, which made the most
 * interesting thing in the product invisible: "check_offer returned MODIFY,
 * approved 10%" means very little on its own, and means everything two
 * lines under the customer saying "that's more than I wanted to spend".
 *
 * So the timeline is now one merged time axis - what was said and what was
 * done, interleaved, in the order it happened. That single view is where
 * the architecture becomes legible: you can watch the model ask for 40%
 * and watch deterministic code hand back 10%.
 *
 * Two real bugs were fixed when this was extracted out of the table page:
 * Escape didn't close it (a modal overlay dismissable only by clicking
 * exactly the backdrop), and the page behind it kept scrolling.
 */

import { useEffect, useState } from "react";
import {
  getRecoveryDetail,
  explainRecovery,
  formatInr,
  AuditRow,
  TranscriptTurn,
  ExplainResult,
  TimingPlan,
} from "@/lib/api";
import {
  StatusPill,
  Tone,
  toneForRecoveryState,
  RULE,
  RULE_SOFT,
  INK_MUTED,
  WASH,
  WARNING,
  NEGATIVE,
  POSITIVE,
} from "./primitives";

export const STATE_LABELS: Record<string, string> = {
  RECOVERED: "Recovered",
  PAYMENT_LINK_SENT: "Offer sent",
  CALLING: "Calling",
  OUTREACH_APPROVED: "Approved",
  CREATED: "Created",
  CONSENT_REVOKED: "Opted out",
  CALL_FAILED: "Call failed",
  PROMISED: "Promised to pay",
  CALLBACK_REQUESTED: "Callback requested",
  BLOCKED: "Stopped by a rule",
};

/** Plain English for each structured refusal a tool can return. A code a
 *  merchant has to look up is not much better than no code at all. */
const REFUSAL_COPY: Record<string, string> = {
  REJECTED_CEILING: "above your discount ceiling",
  REJECTED_MARGIN_FLOOR: "would break your margin floor",
  REJECTED_SKU_EXCLUDED: "this product is excluded from discounts",
  REJECTED_ALREADY_PAID: "this checkout was already paid",
  REJECTED_FULL_PRICE_FIRST: "their payment broke — they didn't object to the price",
  REJECTED_LOW_CONFIDENCE: "the line was too unclear to act on",
  REJECTED_UNCONFIRMED_BARRIER: "they hadn't confirmed price was the problem yet",
  REJECTED_INVALID_CART_TOTAL: "the cart total was not usable",
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

/** The one panel that shows deterministic code overruling the model. It is
 *  rendered from the tool's own result, so it can never describe a decision
 *  differently from the audit row it came out of. */
function RefusalNote({ result }: { result: any }) {
  const reason: string | undefined = result?.reason;
  if (!reason || !reason.startsWith("REJECTED_")) return null;

  const requested = result.requested_percent;
  const approved = result.approved_percent;
  const ceiling = result.ceiling_percent;
  const floor = result.margin_floor_percent;
  const showNumbers = typeof requested === "number" && requested > 0;

  return (
    <div
      className="mt-2 mb-1.5 border-l-2 pl-3 py-1"
      style={{ borderColor: reason === "REJECTED_CEILING" ? WARNING : NEGATIVE }}
    >
      <div className="text-[12px] font-semibold mb-0.5">{reason}</div>
      <div className="text-[12px] leading-relaxed" style={{ color: INK_MUTED }}>
        {REFUSAL_COPY[reason] || "refused by policy"}
      </div>
      {showNumbers && (
        <div className="text-[12px] mt-1 tabular-nums" style={{ color: INK_MUTED }}>
          asked <strong style={{ color: NEGATIVE }}>{requested}%</strong>
          {typeof approved === "number" && (
            <>
              {" → approved "}
              <strong style={{ color: approved > 0 ? POSITIVE : INK_MUTED }}>{approved}%</strong>
            </>
          )}
          {typeof ceiling === "number" && <> · your ceiling {ceiling}%</>}
          {typeof floor === "number" && <> · margin floor {floor}%</>}
        </div>
      )}
    </div>
  );
}

function Utterance({ turn }: { turn: TranscriptTurn }) {
  const isAgent = turn.speaker === "agent";
  // Below this, the money tools refuse outright (see LOW_CONFIDENCE_FLOOR
  // in the backend). Surfaced because a call that reads oddly in this
  // drawer is usually a call we misheard, and that should be visible
  // rather than something the merchant has to infer.
  const unsure = turn.stt_confidence !== null && turn.stt_confidence < 0.6;

  return (
    <div className="border-t py-3.5" style={{ borderColor: RULE_SOFT }}>
      <div className="flex items-baseline justify-between gap-3 mb-1">
        <span
          className="text-[10.5px] font-semibold uppercase tracking-widest"
          style={{ color: INK_MUTED }}
        >
          {isAgent ? "Agent" : "Customer"}
        </span>
        <span className="text-[11px] tabular-nums shrink-0" style={{ color: INK_MUTED }}>
          {new Date(turn.created_at).toLocaleTimeString("en-IN")}
        </span>
      </div>
      <div className={isAgent ? "text-[13px] leading-relaxed" : "text-[13px] leading-relaxed pl-4"}>
        &ldquo;{turn.text}&rdquo;
      </div>
      <div className="flex items-center gap-3 mt-1">
        {turn.input_mode === "dtmf" && (
          <span className="text-[10px] uppercase font-semibold tracking-wide" style={{ color: INK_MUTED }}>
            keypad
          </span>
        )}
        {unsure && (
          <span className="text-[10px] uppercase font-semibold tracking-wide" style={{ color: WARNING }}>
            heard poorly · {(turn.stt_confidence! * 100).toFixed(0)}%
          </span>
        )}
      </div>
    </div>
  );
}

function ToolCall({ row }: { row: AuditRow }) {
  return (
    <div className="border-t py-4" style={{ borderColor: RULE_SOFT, background: WASH }}>
      <div className="flex items-baseline justify-between mb-1.5 gap-3 px-3">
        <span className="text-sm font-semibold">{row.action}</span>
        <span className="text-[11px] tabular-nums shrink-0" style={{ color: INK_MUTED }}>
          {new Date(row.created_at).toLocaleTimeString("en-IN")}
        </span>
      </div>
      <div className="px-3">
        <div className="flex items-center flex-wrap gap-3 text-[12px] mb-1" style={{ color: INK_MUTED }}>
          <span>{row.actor}</span>
          {row.decision && <StatusPill tone={toneForDecision(row.decision)}>{row.decision}</StatusPill>}
          {row.latency_ms !== null && <span className="tabular-nums">{row.latency_ms}ms</span>}
          {row.degraded && (
            <span className="text-[10px] uppercase font-semibold tracking-wide" style={{ color: WARNING }}>
              degraded
            </span>
          )}
        </div>
        <RefusalNote result={row.result} />
        {row.result && (
          <details className="mt-1">
            <summary className="text-[11px] cursor-pointer" style={{ color: INK_MUTED }}>
              raw result
            </summary>
            <pre
              className="text-[11px] p-2.5 mt-1 overflow-x-auto"
              style={{ background: "#FDFCF7", fontFamily: "var(--font-geist-mono)" }}
            >
              {JSON.stringify(row.result, null, 2)}
            </pre>
          </details>
        )}
      </div>
    </div>
  );
}

/** What the customer committed to, in their own words. `promise_words` has
 *  been stored verbatim since promise-to-pay shipped and was displayed
 *  nowhere, so the merchant saw a state label where they could have seen
 *  the sentence. */
function PromiseNote({ attempt }: { attempt: any }) {
  if (!attempt?.promised_at) return null;
  return (
    <div className="mb-8 border p-4" style={{ borderColor: RULE }}>
      <div className="text-[10.5px] font-semibold uppercase tracking-widest mb-2" style={{ color: INK_MUTED }}>
        Promise to pay
      </div>
      <div className="text-[13px] mb-1">
        Committed to pay by{" "}
        <strong className="tabular-nums">{String(attempt.promised_at).slice(0, 10)}</strong>
        {attempt.promised_amount_paise ? (
          <> · {formatInr(attempt.promised_amount_paise)}</>
        ) : null}
      </div>
      {attempt.promise_words && (
        <div className="text-[13px] italic leading-relaxed" style={{ color: INK_MUTED }}>
          &ldquo;{attempt.promise_words}&rdquo;
        </div>
      )}
      <div className="text-[12px] mt-2 leading-relaxed" style={{ color: INK_MUTED }}>
        {attempt.promise_reminded_at
          ? "The one reminder this promise earns has been sent. They will not be contacted about it again."
          : "Outreach is paused until that date. If it passes unpaid, exactly one reminder is sent."}
      </div>
    </div>
  );
}

/** When this customer could be approached again, and why that moment.
 *
 *  Sits directly above PromiseNote on purpose: they are the same story in
 *  two states. This is what the agent was allowed to OFFER; the promise
 *  below is what the customer actually agreed to. Nothing here is
 *  scheduled, and the card says so rather than letting a merchant assume
 *  otherwise - in Tier 1 there is no dispatcher, so a card implying one
 *  would be the interface telling the same lie the agent is forbidden to.
 */
function TimingPlanNote({ plan }: { plan?: TimingPlan }) {
  if (!plan) return null;

  // "Never" is a real answer and worth showing. A stopping rule the
  // merchant cannot see is a stopping rule they have to take on trust.
  if (plan.no_window_reason) {
    return (
      <div className="mb-8 border p-4" style={{ borderColor: RULE }}>
        <div
          className="text-[10.5px] font-semibold uppercase tracking-widest mb-2"
          style={{ color: INK_MUTED }}
        >
          Follow-up timing
        </div>
        <div className="text-[13px] leading-relaxed" style={{ color: INK_MUTED }}>
          {plan.no_window_reason}
        </div>
      </div>
    );
  }

  if (!plan.windows.length) return null;

  return (
    <div className="mb-8 border p-4" style={{ borderColor: RULE }}>
      <div
        className="text-[10.5px] font-semibold uppercase tracking-widest mb-2"
        style={{ color: INK_MUTED }}
      >
        Follow-up timing
      </div>
      <ul className="space-y-1.5">
        {plan.windows.map((w) => (
          <li key={w.at} className="text-[13px] leading-relaxed">
            <strong className="tabular-nums" style={{ opacity: w.is_past ? 0.5 : 1 }}>
              {new Date(w.at).toLocaleString("en-IN", {
                weekday: "short",
                day: "numeric",
                month: "short",
                hour: "2-digit",
                minute: "2-digit",
              })}
            </strong>
            <span style={{ color: INK_MUTED }}> — {w.say_reason}</span>
            {w.is_past && (
              <span
                className="text-[10px] uppercase font-semibold tracking-wide ml-2"
                style={{ color: INK_MUTED }}
              >
                passed
              </span>
            )}
          </li>
        ))}
      </ul>
      <div className="text-[12px] mt-3 leading-relaxed" style={{ color: INK_MUTED }}>
        Suggested only — nothing is scheduled and no card is retried automatically. A date
        holds only once the customer agrees to it.
        {plan.calling_hours ? ` Kept inside your calling hours (${plan.calling_hours}).` : ""}
      </div>
    </div>
  );
}

type TimelineItem =
  | { kind: "turn"; at: number; turn: TranscriptTurn }
  | { kind: "tool"; at: number; row: AuditRow };

/** The narration, sitting directly above the rows it was written from.
 *
 *  Deliberately not a popup. An explanation on its own is a "trust me";
 *  an explanation with the transcript and the tool calls underneath it is
 *  something a merchant can check line by line - which matters most when
 *  it is describing why their customer was refused a discount.
 */
function Explanation({ recoveryAttemptId }: { recoveryAttemptId: string }) {
  const [state, setState] = useState<ExplainResult | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setState(null);
    setFailed(false);
    explainRecovery(recoveryAttemptId)
      .then((r) => !cancelled && setState(r))
      .catch(() => !cancelled && setFailed(true));
    return () => {
      cancelled = true;
    };
  }, [recoveryAttemptId]);

  const body = failed
    ? "Could not write an explanation just now. The record below is unaffected."
    : state?.degraded
    ? state.detail
    : state?.explanation;

  return (
    <div className="mb-8 border p-5" style={{ borderColor: RULE, background: WASH }}>
      <div className="text-[10.5px] font-semibold uppercase tracking-widest mb-2" style={{ color: INK_MUTED }}>
        What happened here
      </div>
      {!state && !failed ? (
        <div className="space-y-2">
          {[0, 1, 2].map((i) => (
            <div key={i} className="anim-shimmer h-[10px] rounded-sm" style={{ width: `${92 - i * 16}%` }} />
          ))}
        </div>
      ) : (
        <p className="text-[13px] leading-relaxed" style={{ textWrap: "pretty" } as React.CSSProperties}>
          {body}
        </p>
      )}
      <div className="text-[11px] mt-3 pt-3 border-t" style={{ borderColor: RULE_SOFT, color: INK_MUTED }}>
        Written from the rows below and nothing else — if a fact is not in this attempt&rsquo;s
        record, it is not in the summary.
      </div>
    </div>
  );
}

export function RecoveryDrawer({
  recoveryAttemptId,
  explainOnOpen = false,
  onClose,
}: {
  recoveryAttemptId: string;
  explainOnOpen?: boolean;
  onClose: () => void;
}) {
  const [detail, setDetail] = useState<{
    recovery_attempt: any;
    audit_trail: AuditRow[];
    transcript: TranscriptTurn[];
    timing_plan?: TimingPlan;
  } | null>(null);
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

  // One time axis. Said and done, interleaved, in the order it happened -
  // which is the only ordering in which either half explains the other.
  const timeline: TimelineItem[] = detail
    ? [
        ...(detail.transcript || []).map(
          (turn): TimelineItem => ({ kind: "turn", at: new Date(turn.created_at).getTime(), turn })
        ),
        ...(detail.audit_trail || []).map(
          (row): TimelineItem => ({ kind: "tool", at: new Date(row.created_at).getTime(), row })
        ),
      ].sort((a, b) => a.at - b.at)
    : [];

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
                {detail.recovery_attempt.failure_class && (
                  <Field label="Why it failed">{detail.recovery_attempt.failure_class}</Field>
                )}
                {detail.recovery_attempt.plan?.opening_line && (
                  <Field label="Opening line used">
                    <span className="italic">&ldquo;{detail.recovery_attempt.plan.opening_line}&rdquo;</span>
                  </Field>
                )}
              </div>

              {explainOnOpen && <Explanation recoveryAttemptId={recoveryAttemptId} />}

              <TimingPlanNote plan={detail.timing_plan} />

              <PromiseNote attempt={detail.recovery_attempt} />

              <div
                className="text-[11px] font-semibold uppercase tracking-widest mb-4"
                style={{ color: INK_MUTED }}
              >
                Timeline — what was said, and what was done
              </div>

              {timeline.length === 0 ? (
                <p className="text-sm" style={{ color: INK_MUTED }}>
                  Nothing recorded for this attempt yet.
                </p>
              ) : (
                timeline.map((item, i) => (
                  <div
                    key={item.kind === "turn" ? item.turn.turn_id : item.row.audit_id}
                    className="anim-row"
                    style={{ animationDelay: `${Math.min(i, 10) * 45}ms` }}
                  >
                    {item.kind === "turn" ? <Utterance turn={item.turn} /> : <ToolCall row={item.row} />}
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
