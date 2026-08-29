"use client";

import { useEffect, useState } from "react";
import { apiFetch } from "@/lib/api";
import {
  PageHeader,
  PageBody,
  SectionTitle,
  RULE_SOFT,
  INK_MUTED,
  POSITIVE,
  NEGATIVE,
} from "@/components/dashboard/primitives";

interface Policy {
  max_discount_percent: number;
  minimum_margin_percent: number;
  calling_start_hour: number;
  calling_end_hour: number;
  auto_approval_threshold_inr: number;
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
              <div className="hint">Discounts that cost less than this need no human review.</div>
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
