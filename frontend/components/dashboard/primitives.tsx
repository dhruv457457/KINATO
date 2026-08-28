"use client";

/**
 * Shared dashboard primitives.
 *
 * Every dashboard page previously hand-rolled its own <header>, its own
 * <table> chrome, its own status colouring and its own empty/loading state -
 * the same ~40 lines of markup copied six times, which is how the hairline
 * colours had already started to drift between pages. These are the single
 * definitions. Design language is the Vignelli-adapted one already
 * established by the onboarding funnel (see app/globals.css `.onb-`):
 * cream ground, charcoal ink, single hairline rules, radius 0, no shadow,
 * Playfair for titles only, tabular numerals in every figure column.
 */

import { ReactNode } from "react";

export const RULE = "#D5D0BC";
export const RULE_SOFT = "#EAE6D5";
export const INK = "#1B1A17";
export const INK_MUTED = "#8A8678";
export const WASH = "#F5F3E9";
export const POSITIVE = "#1F6B3C";
export const NEGATIVE = "#A3372A";
export const WARNING = "#8A6A1F";

/** Page masthead: eyebrow label + serif title + optional right-hand slot. */
export function PageHeader({
  eyebrow,
  title,
  actions,
}: {
  eyebrow: string;
  title: string;
  actions?: ReactNode;
}) {
  return (
    <header
      className="border-b px-14 py-10 flex items-end justify-between gap-6 anim-rise"
      style={{ borderColor: RULE }}
    >
      <div>
        <div className="text-[11px] font-semibold uppercase tracking-widest mb-2" style={{ color: INK_MUTED }}>
          {eyebrow}
        </div>
        <h1 className="font-serif font-semibold text-[28px] leading-none">{title}</h1>
      </div>
      {actions}
    </header>
  );
}

/** Standard page body wrapper - consistent gutters on every dashboard page. */
export function PageBody({ children, narrow = false }: { children: ReactNode; narrow?: boolean }) {
  return <div className={`flex-1 px-14 py-10 w-full${narrow ? " max-w-2xl" : ""}`}>{children}</div>;
}

/**
 * The one place a dashboard page decides what to render for
 * loading / error / nothing-yet / data. Prevents each page inventing its
 * own (previously inconsistent) version of all four.
 */
export function AsyncSection<T>({
  data,
  error,
  empty,
  children,
}: {
  data: T[] | null;
  error?: string;
  empty: ReactNode;
  children: (rows: T[]) => ReactNode;
}) {
  if (error) return <p className="text-sm anim-fade" style={{ color: NEGATIVE }}>{error}</p>;
  if (!data) return <TableSkeleton />;
  if (data.length === 0) return <>{empty}</>;
  return <>{children(data)}</>;
}

/** Teaching empty state - says what will make this page fill up. */
export function EmptyState({ children }: { children: ReactNode }) {
  return (
    <div
      className="border p-6 text-sm max-w-lg anim-rise"
      style={{ borderColor: RULE, color: INK_MUTED }}
    >
      {children}
    </div>
  );
}

/** Shimmer placeholder sized like a real table, so layout doesn't jump. */
export function TableSkeleton({ rows = 5 }: { rows?: number }) {
  return (
    <div className="border anim-fade" style={{ borderColor: RULE }}>
      <div className="h-[42px] border-b" style={{ borderColor: RULE, background: WASH }} />
      {Array.from({ length: rows }).map((_, i) => (
        <div
          key={i}
          className="h-[46px] border-b flex items-center px-4"
          style={{ borderColor: RULE_SOFT, animationDelay: `${i * 70}ms` }}
        >
          <div className="anim-shimmer h-[9px] rounded-sm" style={{ width: `${34 + ((i * 13) % 38)}%` }} />
        </div>
      ))}
    </div>
  );
}

/** Table shell + header row. Scrolls horizontally on its own, never the page. */
export function DataTable({ columns, children }: { columns: string[]; children: ReactNode }) {
  return (
    <div className="overflow-x-auto border anim-rise" style={{ borderColor: RULE }}>
      <table className="w-full text-[13px]" style={{ borderCollapse: "collapse" }}>
        <thead>
          <tr className="border-b" style={{ borderColor: RULE, background: WASH }}>
            {columns.map((c, i) => (
              <th
                key={`${c}-${i}`}
                className="text-left px-4 py-3 text-[10.5px] font-semibold uppercase tracking-wider"
                style={{ color: INK_MUTED }}
              >
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

export function Row({
  children,
  onClick,
  index = 0,
}: {
  children: ReactNode;
  onClick?: () => void;
  index?: number;
}) {
  return (
    <tr
      onClick={onClick}
      className={`border-b anim-row${onClick ? " cursor-pointer hover:bg-[#FAF8F0]" : ""}`}
      style={{ borderColor: RULE_SOFT, animationDelay: `${Math.min(index, 12) * 35}ms` }}
    >
      {children}
    </tr>
  );
}

export function Cell({
  children,
  muted = false,
  numeric = false,
}: {
  children: ReactNode;
  muted?: boolean;
  numeric?: boolean;
}) {
  return (
    <td className={`px-4 py-3${numeric ? " tabular-nums" : ""}`} style={muted ? { color: INK_MUTED } : undefined}>
      {children}
    </td>
  );
}

export type Tone = "positive" | "negative" | "warning" | "neutral";

const TONE_COLOR: Record<Tone, string> = {
  positive: POSITIVE,
  negative: NEGATIVE,
  warning: WARNING,
  neutral: INK_MUTED,
};

/**
 * One definition of how a state reads. Recovery states, consent states and
 * key states all previously picked their own greens/reds inline.
 */
export function StatusPill({ tone, children }: { tone: Tone; children: ReactNode }) {
  return (
    <span className="text-[11px] font-semibold uppercase tracking-wide" style={{ color: TONE_COLOR[tone] }}>
      {children}
    </span>
  );
}

/** Maps a backend recovery_attempts.state to how it should read. */
export function toneForRecoveryState(state: string): Tone {
  if (state === "RECOVERED") return "positive";
  if (state === "CALL_FAILED" || state === "CONSENT_REVOKED") return "negative";
  if (state === "CALLING" || state === "PAYMENT_LINK_SENT" || state === "OUTREACH_APPROVED") return "warning";
  return "neutral";
}

/** KPI figure. Playfair numerals, honest about missing data (null -> em dash). */
export function StatCard({
  label,
  value,
  sub,
  tone,
  index = 0,
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: Tone;
  index?: number;
}) {
  return (
    <div
      className="border p-6 anim-rise"
      style={{ borderColor: RULE, animationDelay: `${index * 60}ms` }}
    >
      <div className="text-[10.5px] font-semibold uppercase tracking-widest mb-3" style={{ color: INK_MUTED }}>
        {label}
      </div>
      <div
        className="font-serif font-semibold text-[32px] leading-none tabular-nums"
        style={{ color: tone ? TONE_COLOR[tone] : INK }}
      >
        {value}
      </div>
      {sub && (
        <div className="text-[11.5px] mt-2.5" style={{ color: INK_MUTED }}>
          {sub}
        </div>
      )}
    </div>
  );
}

/** Section heading inside a page body. */
export function SectionTitle({ children }: { children: ReactNode }) {
  return (
    <h2 className="text-[13px] font-semibold uppercase tracking-wider mb-3" style={{ color: INK_MUTED }}>
      {children}
    </h2>
  );
}
