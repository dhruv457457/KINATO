"use client";

import { useEffect, useState } from "react";
import { getOverview, formatInr, DashboardOverview } from "@/lib/api";

function KpiCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="border p-6" style={{ borderColor: "#D5D0BC" }}>
      <div className="text-[11px] font-semibold uppercase tracking-wider text-dark-200 mb-3">{label}</div>
      <div className="font-serif text-3xl font-semibold tabular-nums">{value}</div>
      {sub && <div className="text-xs text-dark-200 mt-2">{sub}</div>}
    </div>
  );
}

export default function OverviewPage() {
  const [data, setData] = useState<DashboardOverview | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    getOverview()
      .then(setData)
      .catch(() => setError("Could not load your overview right now."));
  }, []);

  return (
    <>
      <header className="border-b px-14 py-10" style={{ borderColor: "#D5D0BC" }}>
        <div className="text-[11px] font-semibold uppercase tracking-widest text-dark-200 mb-2">Overview</div>
        <h1 className="font-serif font-semibold text-[28px]">How recovery is going</h1>
      </header>

      <div className="flex-1 px-14 py-12 max-w-[1000px] w-full">
        {error && <p className="text-sm text-red-700 mb-6">{error}</p>}

        {!data && !error && <p className="text-sm text-dark-200">Loading…</p>}

        {data && (
          <>
            <div className="grid grid-cols-4 gap-px mb-10" style={{ background: "#D5D0BC" }}>
              <div style={{ background: "#FDFCF7" }}>
                <KpiCard label="Revenue recovered" value={formatInr(data.revenue_recovered_paise)} />
              </div>
              <div style={{ background: "#FDFCF7" }}>
                <KpiCard label="Revenue at risk" value={formatInr(data.revenue_at_risk_paise)} sub={`${data.abandoned_count} abandoned checkouts`} />
              </div>
              <div style={{ background: "#FDFCF7" }}>
                <KpiCard label="Active recoveries" value={String(data.active_recoveries)} />
              </div>
              <div style={{ background: "#FDFCF7" }}>
                <KpiCard
                  label="Recovery rate"
                  value={data.recovery_rate_pct !== null ? `${data.recovery_rate_pct}%` : "—"}
                  sub={`${data.recovered_count} of ${data.total_attempts} attempts`}
                />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-8">
              <div className="border p-6" style={{ borderColor: "#D5D0BC" }}>
                <div className="text-[11px] font-semibold uppercase tracking-wider text-dark-200 mb-3">
                  Stopped, by request
                </div>
                <div className="font-serif text-2xl font-semibold tabular-nums">{data.opted_out_count}</div>
                <div className="text-xs text-dark-200 mt-2">
                  Customers who asked not to be contacted again — honored immediately, every time.
                </div>
              </div>
              <div className="border p-6" style={{ borderColor: "#D5D0BC" }}>
                <div className="text-[11px] font-semibold uppercase tracking-wider text-dark-200 mb-3">
                  Calls that couldn&apos;t connect
                </div>
                <div className="font-serif text-2xl font-semibold tabular-nums">{data.call_failed_count}</div>
                <div className="text-xs text-dark-200 mt-2">
                  Real dial failures (no phone on file, carrier issue) — never silently dropped.
                </div>
              </div>
            </div>

            {data.total_attempts === 0 && (
              <div className="mt-10 border p-6 text-sm text-dark-200" style={{ borderColor: "#D5D0BC" }}>
                No recovery attempts yet. Once a real payment fails on your store, Kinato picks it up automatically —
                nothing to trigger here.
              </div>
            )}
          </>
        )}
      </div>
    </>
  );
}
