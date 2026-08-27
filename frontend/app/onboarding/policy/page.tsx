"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { apiFetch } from "@/lib/api";

interface Policy {
  max_discount_percent: number;
  minimum_margin_percent: number;
  calling_start_hour: number;
  calling_end_hour: number;
  auto_approval_threshold_inr: number;
}

export default function PolicyPage() {
  const router = useRouter();
  const [policy, setPolicy] = useState<Policy | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    apiFetch<Policy>("/api/merchant/policy").then(setPolicy).catch(() => {
      setError("Could not load your current policy.");
    });
  }, []);

  async function finish() {
    if (!policy) return;
    setSaving(true);
    setError("");
    try {
      await apiFetch("/api/merchant/policy", { method: "PUT", body: JSON.stringify(policy) });
      await apiFetch("/api/merchant/onboarding/complete", { method: "POST" });
      router.push("/dashboard");
    } catch {
      setError("Could not save your policy. Please try again.");
      setSaving(false);
    }
  }

  if (!policy) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <p className="text-sm text-dark-200">{error || "Loading your policy…"}</p>
      </div>
    );
  }

  return (
    <>
      <header className="border-b px-16 py-11 flex items-end gap-5" style={{ borderColor: "#D5D0BC" }}>
        <div className="font-serif font-bold text-[60px] leading-none tabular-nums">05</div>
        <div className="pb-1">
          <div className="text-[11px] font-semibold uppercase tracking-widest text-dark-200 mb-2">Policy</div>
          <h1 className="font-serif font-semibold text-[28px] leading-tight">
            Set the rules your AI must never break
          </h1>
        </div>
      </header>

      <div className="flex-1 px-16 py-13 max-w-[600px] w-full">
        <div className="mb-7">
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
        </div>

        <div className="mb-7">
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
          <div className="text-xs text-dark-200 mt-2 leading-relaxed">
            The AI will never approve a discount that pushes margin below this, regardless of what it&apos;s
            negotiating.
          </div>
        </div>

        <div className="onb-field mb-7">
          <label>Auto-approve threshold (₹)</label>
          <input
            type="number"
            min={0}
            value={policy.auto_approval_threshold_inr}
            onChange={(e) => setPolicy({ ...policy, auto_approval_threshold_inr: Number(e.target.value) })}
          />
          <div className="hint">Discounts that cost less than this need no human review.</div>
        </div>

        <div className="grid grid-cols-2 gap-6 mb-7">
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

        {error && (
          <div className="onb-status-line err mb-4">
            <span className="onb-status-dot" />
            <span>{error}</span>
          </div>
        )}

        <div className="flex gap-3.5 mt-9 pt-5 border-t items-center" style={{ borderColor: "#EAE6D5" }}>
          <button className="onb-btn-secondary" onClick={() => router.push("/onboarding/catalog")}>
            Back
          </button>
          <button className="onb-btn-primary" disabled={saving} onClick={finish}>
            {saving ? "Saving…" : "Finish setup"}
          </button>
        </div>
      </div>
    </>
  );
}
