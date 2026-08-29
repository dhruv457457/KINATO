"use client";

/**
 * Charts, in the same visual language as everything else.
 *
 * Deliberately hand-rolled SVG rather than Chart.js or D3. Both would have
 * imported a second visual language into a system that is radius 0, single
 * hairline rules, no shadow, Playfair for titles only - their defaults
 * bring gridlines, rounded bars, tooltip chrome and their own typography,
 * and fighting those is more work than drawing fourteen rectangles.
 *
 * The data is also tiny: a fortnight of daily totals. A charting library is
 * for when the drawing is hard, and here it is not.
 *
 * Everything below follows the data-ink rule the rest of this dashboard
 * already follows: one baseline hairline, no gridlines, no axis furniture,
 * value labels only where they carry information. Colour is semantic
 * (POSITIVE for money recovered), never decorative.
 */

import { RULE, RULE_SOFT, INK, INK_MUTED, POSITIVE, WASH } from "./primitives";

export interface DayPoint {
  day: string;
  recovered_count: number;
  recovered_paise: number;
}

/** Compact date for an axis label: "29 Aug" -> "29". Full date lives in the
 *  <title>, so hovering still tells you exactly which day. */
function dayNumber(iso: string): string {
  const d = new Date(`${iso}T00:00:00`);
  return Number.isNaN(d.getTime()) ? "" : String(d.getDate());
}

function monthLabel(iso: string): string {
  const d = new Date(`${iso}T00:00:00`);
  return Number.isNaN(d.getTime())
    ? ""
    : d.toLocaleDateString("en-IN", { month: "short" });
}

/**
 * Fourteen days of recovered money as a bar column chart.
 *
 * Renders nothing rather than an empty frame when every day is zero: an
 * axis with no bars on it looks like a broken chart, while the surrounding
 * empty state can say something true about why there is no data yet.
 */
export function RecoveredByDay({
  series,
  formatValue,
}: {
  series: DayPoint[];
  formatValue: (paise: number) => string;
}) {
  const max = Math.max(...series.map((d) => d.recovered_paise), 0);
  if (!series.length || max === 0) return null;

  const total = series.reduce((sum, d) => sum + d.recovered_paise, 0);
  const best = series.reduce((a, b) => (b.recovered_paise > a.recovered_paise ? b : a));

  // Geometry in a fixed viewBox, scaled by CSS. Keeps the hairlines crisp
  // at any width without a ResizeObserver.
  const W = 720;
  const H = 150;
  const gap = 6;
  const barW = (W - gap * (series.length - 1)) / series.length;

  return (
    <figure className="m-0">
      <figcaption className="flex items-baseline justify-between gap-4 mb-4 flex-wrap">
        <div className="text-[10.5px] font-semibold uppercase tracking-widest" style={{ color: INK_MUTED }}>
          Recovered · last 14 days
        </div>
        <div className="text-[12px] tabular-nums" style={{ color: INK_MUTED }}>
          <span style={{ color: POSITIVE }}>{formatValue(total)}</span> total · best day{" "}
          {formatValue(best.recovered_paise)}
        </div>
      </figcaption>

      <svg
        viewBox={`0 0 ${W} ${H + 22}`}
        className="w-full h-auto block"
        role="img"
        aria-label={`Money recovered per day over the last ${series.length} days`}
      >
        {series.map((d, i) => {
          const x = i * (barW + gap);
          const h = max ? Math.round((d.recovered_paise / max) * H) : 0;
          const isEmpty = d.recovered_paise === 0;
          return (
            <g key={d.day}>
              <title>
                {`${d.day} — ${formatValue(d.recovered_paise)} from ${d.recovered_count} ` +
                  `recover${d.recovered_count === 1 ? "y" : "ies"}`}
              </title>
              {/* A day with nothing recovered still gets a mark, so the eye
                  reads "zero" rather than "no data for that day". */}
              <rect
                x={x}
                y={isEmpty ? H - 1 : H - h}
                width={barW}
                height={isEmpty ? 1 : h}
                fill={isEmpty ? RULE : POSITIVE}
              />
            </g>
          );
        })}

        {/* The single baseline. The only rule on the chart, matching the
            hairline vocabulary of every table and panel around it. */}
        <line x1={0} y1={H} x2={W} y2={H} stroke={RULE} strokeWidth={1} />

        {series.map((d, i) => {
          // Label every third day plus the last one - fourteen labels at
          // this size is noise, and the <title> carries the exact date.
          const show = i % 3 === 0 || i === series.length - 1;
          if (!show) return null;
          return (
            <text
              key={`l-${d.day}`}
              x={i * (barW + gap) + barW / 2}
              y={H + 15}
              textAnchor="middle"
              fontSize="10"
              fill={INK_MUTED}
              style={{ fontVariantNumeric: "tabular-nums" }}
            >
              {dayNumber(d.day)}
            </text>
          );
        })}
      </svg>

      <div className="text-[10px] mt-1 uppercase tracking-widest" style={{ color: INK_MUTED }}>
        {monthLabel(series[0].day)}
        {monthLabel(series[0].day) !== monthLabel(series[series.length - 1].day)
          ? ` — ${monthLabel(series[series.length - 1].day)}`
          : ""}
      </div>
    </figure>
  );
}

/**
 * A proportion bar: how the same total splits across categories.
 *
 * One row, hairline-separated segments, labels underneath. Used for "which
 * channel did the reaching" and "how attempts ended" - both of which are a
 * single whole divided up, which is the one thing a stacked bar does better
 * than a table.
 */
export function ProportionBar({
  label,
  segments,
  emptyNote,
}: {
  label: string;
  segments: { key: string; value: number; tone?: string }[];
  emptyNote: string;
}) {
  const present = segments.filter((s) => s.value > 0);
  const total = present.reduce((sum, s) => sum + s.value, 0);

  return (
    <div>
      <div className="text-[10.5px] font-semibold uppercase tracking-widest mb-3" style={{ color: INK_MUTED }}>
        {label}
      </div>

      {total === 0 ? (
        <p className="text-[12px] leading-relaxed" style={{ color: INK_MUTED }}>
          {emptyNote}
        </p>
      ) : (
        <>
          <div className="flex h-[10px] w-full border" style={{ borderColor: RULE }}>
            {present.map((s, i) => (
              <div
                key={s.key}
                title={`${s.key}: ${s.value}`}
                style={{
                  width: `${(s.value / total) * 100}%`,
                  background: s.tone || INK,
                  borderLeft: i === 0 ? "none" : `1px solid ${RULE}`,
                }}
              />
            ))}
          </div>
          <dl className="mt-3 grid gap-y-1.5 text-[12px]" style={{ gridTemplateColumns: "1fr auto" }}>
            {present.map((s) => (
              <div key={s.key} className="contents">
                <dt className="flex items-center gap-2" style={{ color: INK_MUTED }}>
                  <span
                    aria-hidden
                    className="inline-block w-[8px] h-[8px] shrink-0"
                    style={{ background: s.tone || INK }}
                  />
                  {s.key}
                </dt>
                <dd className="tabular-nums text-right">{s.value}</dd>
              </div>
            ))}
          </dl>
        </>
      )}
    </div>
  );
}
