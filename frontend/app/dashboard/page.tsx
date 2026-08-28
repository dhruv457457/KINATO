"use client";

import { useEffect, useState } from "react";
import { getOverview, formatInr, DashboardOverview } from "@/lib/api";
import {
  PageHeader,
  PageBody,
  StatCard,
  EmptyState,
  TableSkeleton,
  RULE,
  RULE_SOFT,
  INK_MUTED,
  WASH,
  NEGATIVE,
} from "@/components/dashboard/primitives";

/** Human copy for each real `recovery.blocked` reason the backend emits.
 *  Each one says what happened AND what the merchant can do about it —
 *  a count with no next action is just a number. */
const BLOCKED_COPY: Record<string, { title: string; action: string }> = {
  no_contact: {
    title: "no contact details on file",
    action:
      "Razorpay sent us the failed payment, but it carried no email or phone — so there was nobody to reach. Collecting contact details at checkout turns these into recoverable carts.",
  },
  rail_degraded: {
    title: "Razorpay reported a payment outage",
    action:
      "These failures happened during a reported Razorpay outage, so Kinato deliberately stayed quiet rather than chasing customers over a problem that wasn't theirs. No action needed.",
  },
};

function BlockedPanel({ reasons }: { reasons: Record<string, number> }) {
  const entries = Object.entries(reasons || {}).filter(([, n]) => n > 0);
  if (entries.length === 0) return null;
  const total = entries.reduce((sum, [, n]) => sum + n, 0);

  return (
    <div className="mt-10 border anim-rise" style={{ borderColor: RULE }}>
      <div className="px-6 py-4 border-b" style={{ borderColor: RULE, background: WASH }}>
        <span className="font-serif font-semibold text-[17px] tabular-nums">{total}</span>
        <span className="text-[13px] ml-2">
          {total === 1 ? "recovery never started" : "recoveries never started"}
        </span>
      </div>
      {entries.map(([reason, count]) => {
        const copy = BLOCKED_COPY[reason];
        return (
          <div key={reason} className="px-6 py-4 border-b last:border-b-0" style={{ borderColor: RULE_SOFT }}>
            <div className="text-[13px] font-medium mb-1">
              <span className="tabular-nums">{count}</span> — {copy?.title || reason.replace(/_/g, " ")}
            </div>
            <div className="text-[12px] leading-relaxed" style={{ color: INK_MUTED }}>
              {copy?.action || "Kinato held back outreach for this reason."}
            </div>
          </div>
        );
      })}
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
      <PageHeader eyebrow="Overview" title="How recovery is going" />

      <PageBody>
        {error && <p className="text-sm mb-6 anim-fade" style={{ color: NEGATIVE }}>{error}</p>}
        {!data && !error && <TableSkeleton rows={4} />}

        {data && (
          <div className="max-w-[1000px]">
            {/* Hairline grid: the 1px gap IS the rule, so KPIs read as one
                instrument rather than four floating cards. */}
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-px mb-10" style={{ background: RULE }}>
              {[
                {
                  label: "Revenue recovered",
                  value: formatInr(data.revenue_recovered_paise),
                  sub: `${data.recovered_count} recovered`,
                  tone: data.revenue_recovered_paise > 0 ? ("positive" as const) : undefined,
                },
                {
                  label: "Revenue at risk",
                  value: formatInr(data.revenue_at_risk_paise),
                  sub: `${data.abandoned_count} abandoned checkouts`,
                },
                {
                  label: "Active recoveries",
                  value: String(data.active_recoveries),
                  sub: "in flight right now",
                },
                {
                  label: "Recovery rate",
                  value: data.recovery_rate_pct !== null ? `${data.recovery_rate_pct}%` : "—",
                  sub: `${data.recovered_count} of ${data.total_attempts} attempts`,
                },
              ].map((k, i) => (
                <div key={k.label} style={{ background: "#FDFCF7" }}>
                  <StatCard label={k.label} value={k.value} sub={k.sub} tone={k.tone} index={i} />
                </div>
              ))}
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-px" style={{ background: RULE }}>
              <div style={{ background: "#FDFCF7" }}>
                <StatCard
                  label="Stopped, by request"
                  value={String(data.opted_out_count)}
                  sub="Customers who asked not to be contacted again — honored immediately, every time."
                  index={4}
                />
              </div>
              <div style={{ background: "#FDFCF7" }}>
                <StatCard
                  label="Calls that couldn't connect"
                  value={String(data.call_failed_count)}
                  sub="Real dial failures (no phone on file, carrier issue) — never silently dropped."
                  index={5}
                />
              </div>
            </div>

            <BlockedPanel reasons={data.blocked_reasons} />

            {data.total_attempts === 0 && (
              <div className="mt-10">
                <EmptyState>
                  No recovery attempts yet. Once a real payment fails on your store, Kinato picks it up
                  automatically — nothing to trigger here.
                </EmptyState>
              </div>
            )}
          </div>
        )}
      </PageBody>
    </>
  );
}
