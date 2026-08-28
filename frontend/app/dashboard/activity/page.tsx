"use client";

import { useEffect, useMemo, useState } from "react";
import { getActivity, AuditRow } from "@/lib/api";
import {
  PageHeader,
  PageBody,
  DataTable,
  Row,
  Cell,
  StatusPill,
  EmptyState,
  AsyncSection,
  Tone,
  RULE,
  INK_MUTED,
  WARNING,
} from "@/components/dashboard/primitives";

function toneForDecision(decision: string): Tone {
  if (decision === "ISSUED" || decision === "ALLOW") return "positive";
  if (decision === "DENY" || decision === "REJECTED") return "negative";
  if (decision === "MODIFY") return "warning";
  return "neutral";
}

function Filter({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: string[];
  onChange: (v: string) => void;
}) {
  return (
    <label className="flex items-center gap-2">
      <span className="uppercase tracking-wider text-[10.5px] font-semibold" style={{ color: INK_MUTED }}>
        {label}
      </span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="border px-2 py-1 text-[12px]"
        style={{ borderColor: RULE, borderRadius: 0, background: "transparent" }}
      >
        <option value="all">All</option>
        {options.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    </label>
  );
}

export default function ActivityPage() {
  const [rows, setRows] = useState<AuditRow[] | null>(null);
  const [error, setError] = useState("");
  const [actorFilter, setActorFilter] = useState("all");
  const [decisionFilter, setDecisionFilter] = useState("all");

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

  const filtered = useMemo(
    () =>
      (rows || []).filter(
        (r) =>
          (actorFilter === "all" || r.actor === actorFilter) &&
          (decisionFilter === "all" || r.decision === decisionFilter)
      ),
    [rows, actorFilter, decisionFilter]
  );

  return (
    <>
      <PageHeader eyebrow="Activity" title="Every money decision, queryable" />

      <PageBody>
        {rows && rows.length > 0 && (
          <div className="flex gap-6 mb-6 anim-fade">
            <Filter label="Actor" value={actorFilter} options={actors} onChange={setActorFilter} />
            <Filter label="Decision" value={decisionFilter} options={decisions} onChange={setDecisionFilter} />
          </div>
        )}

        <AsyncSection
          data={rows === null ? null : filtered}
          error={error}
          empty={
            <EmptyState>
              {rows && rows.length > 0
                ? "No activity matches this filter."
                : "No agent activity yet — every tool call an agent makes will appear here with its real decision, latency, and whether it ran degraded."}
            </EmptyState>
          }
        >
          {(list) => (
            <DataTable columns={["Action", "Actor", "Decision", "Latency", "When"]}>
              {list.map((r, i) => (
                <Row key={r.audit_id} index={i}>
                  <Cell>
                    <span className="font-medium">{r.action}</span>
                  </Cell>
                  <Cell muted>
                    {r.actor}
                    {r.degraded && (
                      <span
                        className="ml-2 text-[10px] uppercase font-semibold tracking-wide"
                        style={{ color: WARNING }}
                      >
                        degraded
                      </span>
                    )}
                  </Cell>
                  <Cell>
                    {r.decision && <StatusPill tone={toneForDecision(r.decision)}>{r.decision}</StatusPill>}
                  </Cell>
                  <Cell muted numeric>
                    {r.latency_ms !== null ? `${r.latency_ms}ms` : "—"}
                  </Cell>
                  <Cell muted numeric>
                    {new Date(r.created_at).toLocaleString("en-IN")}
                  </Cell>
                </Row>
              ))}
            </DataTable>
          )}
        </AsyncSection>
      </PageBody>
    </>
  );
}
