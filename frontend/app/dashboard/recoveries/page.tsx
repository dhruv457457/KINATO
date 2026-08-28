"use client";

import { useEffect, useState } from "react";
import { getRecoveries, formatInr, RecoveryRow } from "@/lib/api";
import {
  PageHeader,
  PageBody,
  DataTable,
  Row,
  Cell,
  EmptyState,
  AsyncSection,
} from "@/components/dashboard/primitives";
import { RecoveryDrawer, RecoveryState } from "@/components/dashboard/RecoveryDrawer";

export default function RecoveriesPage() {
  const [rows, setRows] = useState<RecoveryRow[] | null>(null);
  const [error, setError] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  useEffect(() => {
    getRecoveries()
      .then((d) => setRows(d.recoveries))
      .catch(() => setError("Could not load recoveries right now."));
  }, []);

  return (
    <>
      <PageHeader eyebrow="Recoveries" title="Every attempt, real and auditable" />

      <PageBody>
        <AsyncSection
          data={rows}
          error={error}
          empty={
            <EmptyState>
              No recovery attempts yet. Once a real payment fails on your store, one appears here automatically.
            </EmptyState>
          }
        >
          {(list) => (
            <DataTable columns={["Customer", "Cart", "Status", "Discount", "Recovered", "When"]}>
              {list.map((r, i) => (
                <Row key={r.recovery_attempt_id} index={i} onClick={() => setSelectedId(r.recovery_attempt_id)}>
                  <Cell>{r.customer_name || r.customer_phone || "—"}</Cell>
                  <Cell numeric>{formatInr(r.cart_amount_paise)}</Cell>
                  <Cell>
                    <RecoveryState state={r.state} />
                  </Cell>
                  <Cell numeric>
                    {r.approved_discount_percent !== null ? `${r.approved_discount_percent}%` : "—"}
                  </Cell>
                  <Cell numeric>{formatInr(r.attributed_revenue_paise)}</Cell>
                  <Cell muted numeric>
                    {new Date(r.created_at).toLocaleString("en-IN")}
                  </Cell>
                </Row>
              ))}
            </DataTable>
          )}
        </AsyncSection>
      </PageBody>

      {selectedId && <RecoveryDrawer recoveryAttemptId={selectedId} onClose={() => setSelectedId(null)} />}
    </>
  );
}
