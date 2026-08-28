"use client";

import { useEffect, useRef, useState } from "react";
import { askMerchantIntel, IntelAnswer } from "@/lib/api";
import {
  PageHeader,
  PageBody,
  RULE,
  RULE_SOFT,
  INK_MUTED,
  WASH,
  WARNING,
  NEGATIVE,
} from "@/components/dashboard/primitives";

/** Questions that are answerable strictly from what this merchant's own DB
 *  actually contains — deliberately not aspirational prompts the backend
 *  can't ground, which would invite the model to invent an answer. */
const SUGGESTIONS = [
  "How much revenue is at risk right now?",
  "What's my recovery rate, and is that good?",
  "What are customers objecting to most?",
  "Why haven't more recoveries converted?",
];

interface Turn {
  question: string;
  answer?: string;
  source?: string;
  degraded?: boolean;
  error?: boolean;
}

/** Minimal markdown: bold, bullets, headings. The backend is instructed to
 *  emit clean short markdown, and pulling in a full parser for that would be
 *  more dependency than the surface needs. */
function renderMarkdown(md: string) {
  return md.split("\n").map((line, i) => {
    const t = line.trim();
    if (!t) return <div key={i} className="h-2" />;
    const bold = (s: string) =>
      s.split(/(\*\*[^*]+\*\*)/g).map((part, j) =>
        part.startsWith("**") && part.endsWith("**") ? (
          <strong key={j}>{part.slice(2, -2)}</strong>
        ) : (
          <span key={j}>{part}</span>
        )
      );
    if (t.startsWith("###")) {
      return (
        <div key={i} className="text-[11px] font-semibold uppercase tracking-widest mt-3 mb-1.5" style={{ color: INK_MUTED }}>
          {t.replace(/^#+\s*/, "")}
        </div>
      );
    }
    if (t.startsWith("- ") || t.startsWith("* ")) {
      return (
        <div key={i} className="flex gap-2.5 mb-1">
          <span style={{ color: INK_MUTED }}>—</span>
          <span>{bold(t.slice(2))}</span>
        </div>
      );
    }
    return (
      <p key={i} className="mb-1.5">
        {bold(t)}
      </p>
    );
  });
}

export default function AskPage() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns, busy]);

  async function ask(question: string) {
    const q = question.trim();
    if (!q || busy) return;
    setInput("");
    setBusy(true);
    setTurns((prev) => [...prev, { question: q }]);
    try {
      const res: IntelAnswer = await askMerchantIntel(q);
      setTurns((prev) =>
        prev.map((t, i) =>
          i === prev.length - 1 ? { ...t, answer: res.answer, source: res.source, degraded: res.degraded } : t
        )
      );
    } catch {
      setTurns((prev) =>
        prev.map((t, i) =>
          i === prev.length - 1
            ? { ...t, answer: "Couldn't reach the intelligence service just now. Your data is unaffected.", error: true }
            : t
        )
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader eyebrow="Ask" title="Ask anything about your store" />

      <PageBody>
        <div className="max-w-[720px]">
          {turns.length === 0 && (
            <div className="anim-rise">
              <p className="text-sm mb-6" style={{ color: INK_MUTED }}>
                Answers come strictly from your own recovery data — never a generic benchmark, never an invented
                example. If there&apos;s no data behind a question yet, it will say so.
              </p>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-px" style={{ background: RULE }}>
                {SUGGESTIONS.map((s, i) => (
                  <button
                    key={s}
                    onClick={() => ask(s)}
                    className="text-left p-4 text-[13px] transition-colors hover:bg-[#F5F3E9] anim-rise"
                    style={{ background: "#FDFCF7", animationDelay: `${i * 60}ms` }}
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}

          {turns.map((t, i) => (
            <div key={i} className="mb-8 anim-rise">
              <div className="flex items-baseline gap-3 mb-3">
                <span
                  className="text-[10px] font-semibold uppercase tracking-widest shrink-0 pt-0.5"
                  style={{ color: INK_MUTED }}
                >
                  You
                </span>
                <span className="text-[15px] font-medium">{t.question}</span>
              </div>

              {t.answer === undefined ? (
                <div className="pl-[42px] space-y-2">
                  {[0, 1, 2].map((k) => (
                    <div
                      key={k}
                      className="anim-shimmer h-[11px] rounded-sm"
                      style={{ width: `${78 - k * 16}%`, animationDelay: `${k * 90}ms` }}
                    />
                  ))}
                </div>
              ) : (
                <div
                  className="pl-[42px] text-[14px] leading-relaxed border-l"
                  style={{ borderColor: RULE_SOFT, marginLeft: "3px", paddingLeft: "39px", color: t.error ? NEGATIVE : undefined }}
                >
                  {renderMarkdown(t.answer)}
                  {t.degraded && (
                    <div className="mt-3 text-[11px] uppercase tracking-wide font-semibold" style={{ color: WARNING }}>
                      Answered without the language model — figures are real, phrasing is templated.
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}

          <div ref={endRef} />

          <div className="sticky bottom-0 pt-4 pb-8" style={{ background: "#FDFCF7" }}>
            <div className="flex gap-3 items-end border-t pt-4" style={{ borderColor: RULE }}>
              <input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && (e.preventDefault(), ask(input))}
                placeholder="Ask about recoveries, objections, revenue at risk…"
                className="flex-1 bg-transparent outline-none text-[15px] py-2"
                style={{ borderBottom: `1px solid ${RULE}` }}
                disabled={busy}
              />
              <button className="onb-btn-primary shrink-0" onClick={() => ask(input)} disabled={busy || !input.trim()}>
                {busy ? "Thinking…" : "Ask"}
              </button>
            </div>
            <div className="text-[11px] mt-2.5" style={{ color: INK_MUTED }}>
              Grounded in your own data only. Figures are read live from your recovery records.
            </div>
          </div>
        </div>
      </PageBody>
    </>
  );
}
