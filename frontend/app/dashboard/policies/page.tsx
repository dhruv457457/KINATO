"use client";

import { useEffect, useState } from "react";
import { apiFetch, proposePolicy, PolicyProposal } from "@/lib/api";
import {
  PageHeader,
  PageBody,
  SectionTitle,
  RULE,
  RULE_SOFT,
  INK,
  INK_MUTED,
  WASH,
  POSITIVE,
  NEGATIVE,
} from "@/components/dashboard/primitives";

interface Policy {
  max_discount_percent: number;
  minimum_margin_percent: number;
  calling_start_hour: number;
  calling_end_hour: number;
  auto_approval_threshold_inr: number;
  emi_available: boolean;
  /** How the agent should SOUND. Deliberately separate from every number
   *  above it: those are limits a policy engine enforces, this is style
   *  that reaches a prompt. Writing "give 90% off" here changes nothing —
   *  the ceiling is computed by code the prompt never touches. */
  voice_persona?: string;
}

/** How each proposable field should read to a human.
 *  A diff that says `auto_approval_threshold_inr: 500 -> 1000` is a diff
 *  about a database column. A merchant is approving a change to their own
 *  money, so it has to read like one. */
const FIELD_LABELS: Record<string, string> = {
  max_discount_percent: "Max discount ceiling",
  minimum_margin_percent: "Minimum margin",
  calling_start_hour: "Calling starts",
  calling_end_hour: "Calling ends",
  auto_approval_threshold_inr: "Auto-approve threshold",
  emi_available: "EMI offered",
};

function formatValue(field: string, value: number | boolean | undefined): string {
  if (value === undefined || value === null) return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (field.endsWith("_percent")) return `${value}%`;
  if (field.endsWith("_inr")) return `₹${value.toLocaleString("en-IN")}`;
  if (field.endsWith("_hour")) return `${String(value).padStart(2, "0")}:00`;
  return String(value);
}

/** A policy slider with its live value read out in Playfair, so the number
 *  a merchant is committing to is the loudest thing on the row. */
function PolicySlider({
  label,
  value,
  max,
  onChange,
  hint,
}: {
  label: string;
  value: number;
  max: number;
  onChange: (v: number) => void;
  hint: string;
}) {
  return (
    <div className="mb-8 anim-rise">
      <div className="flex justify-between items-baseline mb-3">
        <label className="text-[11.5px] font-semibold uppercase tracking-wider" style={{ color: INK_MUTED }}>
          {label}
        </label>
        <span className="font-serif text-xl font-semibold tabular-nums">{value}%</span>
      </div>
      <input
        type="range"
        min={0}
        max={max}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full policy-range"
      />
      <div className="text-xs mt-2" style={{ color: INK_MUTED }}>
        {hint}
      </div>
    </div>
  );
}

/** Describe the policy you want; approve the numbers it proposes.
 *
 *  Deliberately a PROPOSAL, not an action. The agent never writes here - it
 *  reads the instruction, and the merchant approves a before -> after diff
 *  before anything is saved.
 *
 *  That is the same rule the whole product runs on, applied to itself: the
 *  model argues, a deterministic engine decides, and a human sees every
 *  number that constrains money before it binds. A model that could write
 *  this page's values would be setting the ceiling it is later judged
 *  against. */
function PolicyAssistant({
  onApply,
  disabled,
}: {
  onApply: (changes: Record<string, number | boolean>) => Promise<void>;
  disabled: boolean;
}) {
  const [instruction, setInstruction] = useState("");
  const [proposal, setProposal] = useState<PolicyProposal | null>(null);
  const [thinking, setThinking] = useState(false);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState("");

  async function ask() {
    if (!instruction.trim()) return;
    setThinking(true);
    setError("");
    setProposal(null);
    try {
      setProposal(await proposePolicy(instruction.trim()));
    } catch {
      setError("Could not reach the assistant. The controls below still work.");
    } finally {
      setThinking(false);
    }
  }

  async function apply() {
    if (!proposal) return;
    setApplying(true);
    try {
      await onApply(proposal.changes);
      setProposal(null);
      setInstruction("");
    } catch {
      setError("Could not save those changes.");
    } finally {
      setApplying(false);
    }
  }

  const changeKeys = proposal ? Object.keys(proposal.changes) : [];

  return (
    <div className="border mb-10" style={{ borderColor: RULE }}>
      <div className="p-5">
        <div className="text-[11.5px] font-semibold uppercase tracking-wider mb-2" style={{ color: INK_MUTED }}>
          Set it in your own words
        </div>
        <p className="text-[12px] mb-3 leading-relaxed" style={{ color: INK_MUTED }}>
          Describe what you want and it will propose the numbers.{" "}
          <strong>Nothing is saved until you approve it</strong> — these are the limits your AI is held
          to, so you see every figure before it binds.
        </p>

        <div className="flex gap-3 items-stretch">
          <input
            type="text"
            value={instruction}
            onChange={(e) => setInstruction(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") ask();
            }}
            placeholder="Never discount more than 10%, and don't call before 9am"
            className="flex-1 min-w-0 border p-3 text-[13px]"
            style={{ borderColor: RULE, borderRadius: 0, background: "transparent" }}
          />
          <button
            className="onb-btn-primary shrink-0"
            disabled={thinking || disabled || !instruction.trim()}
            onClick={ask}
          >
            {thinking ? "Reading…" : "Propose"}
          </button>
        </div>

        {error && (
          <p className="text-[12px] mt-2.5" style={{ color: NEGATIVE }}>
            {error}
          </p>
        )}
      </div>

      {proposal && (
        <div className="border-t p-5 anim-rise" style={{ borderColor: RULE, background: WASH }}>
          <p className="text-[13px] mb-4 leading-relaxed">{proposal.summary}</p>

          {changeKeys.length === 0 ? (
            <p className="text-[12px]" style={{ color: INK_MUTED }}>
              Nothing to change. Try naming a specific number.
            </p>
          ) : (
            <>
              {/* Before -> after, per field. The number a merchant is
                  agreeing to is the loudest thing here; the sentence above
                  is context, not the record. */}
              <div className="mb-4">
                {changeKeys.map((key) => (
                  <div
                    key={key}
                    className="flex items-baseline justify-between py-2 border-b text-[13px]"
                    style={{ borderColor: RULE_SOFT }}
                  >
                    <span style={{ color: INK_MUTED }}>{FIELD_LABELS[key] || key}</span>
                    <span className="tabular-nums">
                      <span style={{ color: INK_MUTED }}>
                        {formatValue(key, proposal.current?.[key])}
                      </span>
                      <span style={{ color: INK_MUTED }}> → </span>
                      <span className="font-semibold" style={{ color: INK }}>
                        {formatValue(key, proposal.changes[key])}
                      </span>
                    </span>
                  </div>
                ))}
              </div>

              <div className="flex items-center gap-3">
                <button className="onb-btn-primary" disabled={applying} onClick={apply}>
                  {applying ? "Saving…" : "Apply these changes"}
                </button>
                <button
                  className="text-[11px] font-semibold uppercase tracking-wider"
                  style={{ color: INK_MUTED }}
                  onClick={() => setProposal(null)}
                >
                  Discard
                </button>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

export default function PoliciesPage() {
  const [policy, setPolicy] = useState<Policy | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    apiFetch<Policy>("/api/merchant/policy")
      .then(setPolicy)
      .catch(() => setError("Could not load your current policy."));
  }, []);

  async function save() {
    if (!policy) return;
    setSaving(true);
    setSaved(false);
    setError("");
    try {
      await apiFetch("/api/merchant/policy", { method: "PUT", body: JSON.stringify(policy) });
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    } catch {
      setError("Could not save your policy. Please try again.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <PageHeader eyebrow="Policies" title="The rules your AI must never break" />

      <PageBody narrow>
        {error && <p className="text-sm mb-6 anim-fade" style={{ color: NEGATIVE }}>{error}</p>}
        {!policy && !error && (
          <div className="space-y-6">
            {[0, 1, 2].map((i) => (
              <div key={i} className="anim-shimmer h-[46px]" style={{ animationDelay: `${i * 80}ms` }} />
            ))}
          </div>
        )}

        {policy && (
          <>
            <PolicyAssistant
              disabled={saving}
              onApply={async (changes) => {
                const next = { ...policy, ...changes } as Policy;
                // Saved through the SAME endpoint the sliders use, so the
                // proposal gets no privileged path into the policy - it
                // ends up as an ordinary merchant edit, validated by the
                // same bounds and recorded the same way.
                await apiFetch("/api/merchant/policy", {
                  method: "PUT",
                  body: JSON.stringify(next),
                });
                setPolicy(next);
                setSaved(true);
                setTimeout(() => setSaved(false), 2500);
              }}
            />

            <PolicySlider
              label="Max discount ceiling"
              value={policy.max_discount_percent}
              max={40}
              onChange={(v) => setPolicy({ ...policy, max_discount_percent: v })}
              hint="The AI will never propose more than this, no matter how the negotiation goes."
            />

            <PolicySlider
              label="Minimum margin"
              value={policy.minimum_margin_percent}
              max={80}
              onChange={(v) => setPolicy({ ...policy, minimum_margin_percent: v })}
              hint="A discount is capped further if it would push margin below this, even if the ceiling above allows more."
            />

            <div className="onb-field mb-8">
              <label>Auto-approve threshold (₹)</label>
              <input
                type="number"
                min={0}
                value={policy.auto_approval_threshold_inr}
                onChange={(e) => setPolicy({ ...policy, auto_approval_threshold_inr: Number(e.target.value) })}
              />
              <div className="hint">The most the AI may give away on one cart without you. Leave at 0 for no
                limit; the ceiling and margin floor still apply either way.</div>
            </div>

            {/* EMI. Off by default and deliberately worded around what the
                checkout can actually do: the agent offers instalments
                BEFORE a discount when this is on, which keeps the full sale
                instead of giving away margin - but only if Razorpay will
                really take the payment that way. Promising instalments a
                checkout cannot provide is the same class of untruth as
                claiming a link was sent. */}
            <div className="mb-8 pb-8 border-b" style={{ borderColor: RULE_SOFT }}>
              <div className="flex items-start justify-between gap-6">
                <div>
                  <div
                    className="text-[11.5px] font-semibold uppercase tracking-wider mb-1.5"
                    style={{ color: INK_MUTED }}
                  >
                    Offer EMI before a discount
                  </div>
                  <div className="text-xs leading-relaxed" style={{ color: INK_MUTED }}>
                    When someone says the price is too much right now, instalments keep the whole sale —
                    a discount gives away margin to solve the same problem. Only turn this on if EMI is
                    genuinely enabled on your Razorpay account; the agent will offer it out loud.
                  </div>
                </div>
                <button
                  type="button"
                  role="switch"
                  aria-checked={policy.emi_available}
                  onClick={() => setPolicy({ ...policy, emi_available: !policy.emi_available })}
                  className="shrink-0 px-4 py-2 text-[11px] font-semibold uppercase tracking-wider border transition-colors"
                  style={{
                    borderColor: policy.emi_available ? POSITIVE : RULE,
                    color: policy.emi_available ? POSITIVE : INK_MUTED,
                    borderRadius: 0,
                  }}
                >
                  {policy.emi_available ? "On" : "Off"}
                </button>
              </div>
            </div>

            {/* Style, deliberately below every limit above it.
                The numbers above are enforced by a policy engine. This is a
                note about manner that reaches the agent's prompt — and the
                caption says so plainly, because a merchant who believes this
                box can authorise a discount will one day write one in it. */}
            <div className="mb-8 pb-8 border-b" style={{ borderColor: RULE_SOFT }}>
              <div
                className="text-[11.5px] font-semibold uppercase tracking-wider mb-1.5"
                style={{ color: INK_MUTED }}
              >
                How your agent should sound
              </div>
              <div className="text-xs leading-relaxed mb-3" style={{ color: INK_MUTED }}>
                Your words, your customers. Tone, what to call yourselves, a line to open with.
                Leave it empty for the default.
              </div>
              <textarea
                rows={3}
                maxLength={400}
                value={policy.voice_persona || ""}
                placeholder="Warm and unhurried. Thank them for shopping with us. Never rush the close."
                onChange={(e) => setPolicy({ ...policy, voice_persona: e.target.value })}
                className="w-full border px-3 py-2 text-[13px] leading-relaxed bg-transparent"
                style={{ borderColor: RULE, borderRadius: 0, resize: "vertical" }}
              />
              <div className="flex items-baseline justify-between mt-1.5">
                <div className="text-[11px] leading-relaxed" style={{ color: INK_MUTED }}>
                  This changes how it speaks, never what it may offer. Asking here for a discount
                  above your ceiling does nothing — the limits above are enforced in code the agent
                  cannot reach.
                </div>
                <div className="text-[11px] tabular-nums shrink-0 ml-4" style={{ color: INK_MUTED }}>
                  {(policy.voice_persona || "").length}/400
                </div>
              </div>
            </div>

            <SectionTitle>Calling hours</SectionTitle>
            <div className="grid grid-cols-2 gap-6 mb-8">
              <div className="onb-field">
                <label>Start</label>
                <input
                  type="number"
                  min={0}
                  max={23}
                  value={policy.calling_start_hour}
                  onChange={(e) => setPolicy({ ...policy, calling_start_hour: Number(e.target.value) })}
                />
              </div>
              <div className="onb-field">
                <label>End</label>
                <input
                  type="number"
                  min={0}
                  max={24}
                  value={policy.calling_end_hour}
                  onChange={(e) => setPolicy({ ...policy, calling_end_hour: Number(e.target.value) })}
                />
              </div>
            </div>

            {/* Round-the-clock has to be reachable and obvious. Before this
                it was neither: 0-23 silently skipped the 23:00 hour, and
                0-0 - which reads as "midnight to midnight, always" - meant
                never, so a merchant could switch calling off while
                believing they had switched it fully on. */}
            <div className="hint" style={{ marginTop: "-1.5rem", marginBottom: "2rem" }}>
              {policy.calling_start_hour === policy.calling_end_hour ||
              (policy.calling_start_hour === 0 && policy.calling_end_hour >= 24) ? (
                <span>
                  Calling <strong>around the clock</strong>. Times are judged in IST, wherever this is
                  running.{" "}
                  <button
                    type="button"
                    className="underline underline-offset-2"
                    onClick={() => setPolicy({ ...policy, calling_start_hour: 10, calling_end_hour: 20 })}
                  >
                    Back to 10:00–20:00
                  </button>
                </span>
              ) : (
                <span>
                  Judged in IST, not server time. Overnight windows work — 22 to 6 means ten at night
                  until six in the morning.{" "}
                  <button
                    type="button"
                    className="underline underline-offset-2"
                    onClick={() => setPolicy({ ...policy, calling_start_hour: 0, calling_end_hour: 24 })}
                  >
                    Call at any hour
                  </button>
                </span>
              )}
            </div>

            <div className="flex items-center gap-4 pt-5 border-t" style={{ borderColor: RULE_SOFT }}>
              <button className="onb-btn-primary" disabled={saving} onClick={save}>
                {saving ? "Saving…" : "Save changes"}
              </button>
              {saved && (
                <span className="text-sm anim-fade" style={{ color: POSITIVE }}>
                  Saved
                </span>
              )}
            </div>
          </>
        )}
      </PageBody>
    </>
  );
}
