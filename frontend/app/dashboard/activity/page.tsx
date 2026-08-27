"use client";

import { useEffect, useMemo, useState } from "react";
import { getActivity, AuditRow } from "@/lib/api";

const DECISION_COLOR: Record<string, string> = {
  ISSUED: "#1F6B3C",
  ALLOW: "#1F6B3C",
  MODIFY: "#8A5A1A",
  DENY: "#A3372A",
  REJECTED: "#A3372A",
  RECORDED: "#44433C",
};

export default function ActivityPage() {
  const [rows, setRows] = useState<AuditRow[] | null>(null);
  const [error, setError] = useState("");
  const [actorFilter, setActorFilter] = useState<string>("all");
  const [decisionFilter, setDecisionFilter] = useState<string>("all");

  useEffect(() => {
    getActivity()
      .then((d) => setRows(d.activity))
      .catch(() => setError("Could not load activity right now."));
  }, []);

  const actors = useMemo(() => Array.from(new Set((rows || []).map((r) => r.actor))), [rows]);
  const decisions = useMemo(
    () => Array.from(new Set((rows || []).map((r) => r.decision).filter(Boolean))) as string[],
    [rows]
  );

  const filtered = (rows || []).filter(
    (r) => (actorFilter === "all" || r.actor === actorFilter) && (decisionFilter === "all" || r.decision === decisionFilter)
  );

  return (
    <>
      <header className="border-b px-14 py-10" style={{ borderColor: "#D5D0BC" }}>
        <div className="text-[11px] font-semibold uppercase tracking-widest text-dark-200 mb-2">Activity</div>
        <h1 className="font-serif font-semibold text-[28px]">Every money decision, queryable</h1>
      </header>

      <div className="flex-1 px-14 py-10 w-full">
        {error && <p className="text-sm text-red-700">{error}</p>}
        {!rows && !error && <p className="text-sm text-dark-200">Loading…</p>}

        {rows && (
          <>
            <div className="flex gap-6 mb-6 text-[12px]">
              <label className="flex items-center gap-2">
                <span className="text-dark-200 uppercase tracking-wider text-[10.5px] font-semibold">Actor</span>
                <select
                  value={actorFilter}
                  onChange={(e) => setActorFilter(e.target.value)}
                  className="border px-2 py-1"
                  style={{ borderColor: "#D5D0BC", borderRadius: 0, background: "transparent" }}
                >
                  <option value="all">All</option>
                  {actors.map((a) => (
                    <option key={a} value={a}>
                      {a}
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex items-center gap-2">
                <span className="text-dark-200 uppercase tracking-wider text-[10.5px] font-semibold">Decision</span>
                <select
                  value={decisionFilter}
                  onChange={(e) => setDecisionFilter(e.target.value)}
                  className="border px-2 py-1"
                  style={{ borderColor: "#D5D0BC", borderRadius: 0, background: "transparent" }}
                >
                  <option value="all">All</option>
                  {decisions.map((d) => (
                    <option key={d} value={d}>
                      {d}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            {filtered.length === 0 && (
              <div className="border p-6 text-sm text-dark-200 max-w-lg" style={{ borderColor: "#D5D0BC" }}>
                No activity matches this filter yet.
              </div>
            )}

            {filtered.length > 0 && (
              <div className="overflow-x-auto border" style={{ borderColor: "#D5D0BC" }}>
                <table className="w-full text-[13px]" style={{ borderCollapse: "collapse" }}>
                  <thead>
                    <tr className="border-b" style={{ borderColor: "#D5D0BC", background: "#F5F3E9" }}>
                      {["Action", "Actor", "Decision", "Latency", "When"].map((h) => (
                        <th key={h} className="text-left px-4 py-3 text-[10.5px] font-semibold uppercase tracking-wider text-dark-200">
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {filtered.map((r) => (
                      <tr key={r.audit_id} className="border-b" style={{ borderColor: "#EAE6D5" }}>
                        <td className="px-4 py-3 font-medium">{r.action}</td>
                        <td className="px-4 py-3 text-dark-200">
                          {r.actor}
                          {r.degraded && (
                            <span className="ml-2 text-[10px] uppercase font-semibold" style={{ color: "#8A5A1A" }}>
                              degraded
                            </span>
                          )}
                        </td>
                        <td className="px-4 py-3">
                          {r.decision && (
                            <span className="text-[11px] font-semibold" style={{ color: DECISION_COLOR[r.decision] || "#8A8678" }}>
                              {r.decision}
                            </span>
                          )}
                        </td>
                        <td className="px-4 py-3 tabular-nums text-dark-200">{r.latency_ms !== null ? `${r.latency_ms}ms` : "—"}</td>
                        <td className="px-4 py-3 text-dark-200 tabular-nums">{new Date(r.created_at).toLocaleString("en-IN")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </div>
    </>
  );
}
