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
  POSITIVE,
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
  no_consent: {
    title: "no consent on file to contact them",
    action:
      "These customers have either asked not to be contacted, or arrived without an email or phone we could record consent against. Opt-outs are permanent by design and need no action; the rest usually means contact details were missing at checkout.",
  },
  quiet_hours: {
    title: "outside your calling hours",
    action:
      "These came in when your configured calling window was closed, so nobody was phoned. Times are judged in IST, not server time. Widen the window on the Policies page if you want these reached.",
  },
  max_calls_reached: {
    title: "already contacted the maximum number of times",
    action:
      "These customers have been called as often as the cap allows. Calling again is the point at which recovery becomes a nuisance, so the cap holds rather than bending for a large cart.",
  },
  already_paid: {
    title: "the customer had already paid",
    action:
      "Payment landed before outreach started, so nothing was sent. No action needed — this is the system checking twice and finding the money already in.",
  },
  promise_to_pay: {
    title: "the customer promised to pay by a date",
    action:
      "They committed to a date and outreach is paused until it passes. If it lapses unpaid, exactly one reminder goes out and then we stop for good.",
  },
  rail_degraded: {
    title: "Razorpay reported a payment outage",
    action:
      "These failures happened during a reported Razorpay outage, so Kinato deliberately stayed quiet rather than chasing customers over a problem that wasn't theirs. No action needed.",
  },
};

/** The one number that must read zero. A rule break is outreach that
 *  happened despite a hard stop being true — calling someone who had already
 *  paid, who never consented, or outside their configured hours; approving a
 *  discount above the ceiling. It is not a metric to improve. */
function RuleBreaks({ count }: { count: number }) {
  const clean = !count;
  return (
    <div
      className="mt-10 border p-6 flex items-baseline gap-4 anim-rise"
      style={{ borderColor: clean ? RULE : NEGATIVE }}
    >
      <span
        className="font-serif font-semibold text-[32px] leading-none tabular-nums"
        style={{ color: clean ? POSITIVE : NEGATIVE }}
      >
        {count}
      </span>
      <div>
        <div className="text-[10.5px] font-semibold uppercase tracking-widest mb-1" style={{ color: INK_MUTED }}>
          Rule breaks
        </div>
        <div className="text-[12px] leading-relaxed" style={{ color: INK_MUTED, maxWidth: "34rem" }}>
          {clean
            ? "No customer was contacted after paying, without consent, or outside your calling hours, and no discount exceeded your ceiling."
            : "A hard stop was breached. This is not a number to improve — it means a guarantee you rely on did not hold. Check Activity for the offending case."}
        </div>
      </div>
    </div>
  );
}

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

            <div className="grid grid-cols-1 md:grid-cols-3 gap-px" style={{ background: RULE }}>
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
                  label="Promised to pay"
                  value={String(data.promised_count)}
                  sub={
                    data.promised_count
                      ? `${formatInr(data.promised_paise)} committed — we've stopped calling them until the date they gave.`
                      : "Customers who name a date are left alone until it passes."
                  }
                  index={6}
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

            <RuleBreaks count={data.rule_breaks} />

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
