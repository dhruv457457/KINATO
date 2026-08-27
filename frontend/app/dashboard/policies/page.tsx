"use client";

import { useEffect, useState } from "react";
import { apiFetch } from "@/lib/api";

interface Policy {
  max_discount_percent: number;
  minimum_margin_percent: number;
  calling_start_hour: number;
  calling_end_hour: number;
  auto_approval_threshold_inr: number;
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
      <header className="border-b px-14 py-10" style={{ borderColor: "#D5D0BC" }}>
        <div className="text-[11px] font-semibold uppercase tracking-widest text-dark-200 mb-2">Policies</div>
        <h1 className="font-serif font-semibold text-[28px]">The rules your AI must never break</h1>
      </header>

      <div className="flex-1 px-14 py-10 max-w-[560px] w-full">
        {error && <p className="text-sm text-red-700 mb-6">{error}</p>}
        {!policy && !error && <p className="text-sm text-dark-200">Loading…</p>}

        {policy && (
          <>
            <div className="mb-8">
              <div className="flex justify-between items-baseline mb-3">
                <label className="text-[11.5px] font-semibold uppercase tracking-wider text-dark-200">
                  Max discount ceiling
                </label>
                <span className="font-serif text-xl font-semibold tabular-nums">{policy.max_discount_percent}%</span>
              </div>
              <input
                type="range"
                min={0}
                max={40}
                value={policy.max_discount_percent}
                onChange={(e) => setPolicy({ ...policy, max_discount_percent: Number(e.target.value) })}
                className="w-full"
              />
              <div className="text-xs text-dark-200 mt-2">
                The AI will never propose more than this, no matter how the negotiation goes.
              </div>
            </div>

            <div className="mb-8">
              <div className="flex justify-between items-baseline mb-3">
                <label className="text-[11.5px] font-semibold uppercase tracking-wider text-dark-200">
                  Minimum margin
                </label>
                <span className="font-serif text-xl font-semibold tabular-nums">{policy.minimum_margin_percent}%</span>
              </div>
              <input
                type="range"
                min={0}
                max={80}
                value={policy.minimum_margin_percent}
                onChange={(e) => setPolicy({ ...policy, minimum_margin_percent: Number(e.target.value) })}
                className="w-full"
              />
              <div className="text-xs text-dark-200 mt-2">
                A discount is capped further if it would push margin below this, even if the ceiling above allows more.
              </div>
            </div>

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

            <div className="grid grid-cols-2 gap-6 mb-8">
              <div className="onb-field">
                <label>Calling hours start</label>
                <input
                  type="number"
                  min={0}
                  max={23}
                  value={policy.calling_start_hour}
                  onChange={(e) => setPolicy({ ...policy, calling_start_hour: Number(e.target.value) })}
                />
              </div>
              <div className="onb-field">
                <label>Calling hours end</label>
                <input
                  type="number"
                  min={0}
                  max={23}
                  value={policy.calling_end_hour}
                  onChange={(e) => setPolicy({ ...policy, calling_end_hour: Number(e.target.value) })}
                />
              </div>
            </div>

            <div className="flex items-center gap-4 pt-5 border-t" style={{ borderColor: "#EAE6D5" }}>
              <button className="onb-btn-primary" disabled={saving} onClick={save}>
                {saving ? "Saving…" : "Save changes"}
              </button>
              {saved && <span className="text-sm" style={{ color: "#1F6B3C" }}>Saved</span>}
            </div>
          </>
        )}
      </div>
    </>
  );
}
