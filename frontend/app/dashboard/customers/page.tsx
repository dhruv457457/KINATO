"use client";

import { useEffect, useState } from "react";
import { getCustomers, revokeCustomerConsent, CustomerRow } from "@/lib/api";
import {
  PageHeader,
  PageBody,
  DataTable,
  Row,
  Cell,
  StatusPill,
  EmptyState,
  AsyncSection,
  NEGATIVE,
} from "@/components/dashboard/primitives";

export default function CustomersPage() {
  const [rows, setRows] = useState<CustomerRow[] | null>(null);
  const [error, setError] = useState("");
  const [revokingId, setRevokingId] = useState<string | null>(null);

  useEffect(() => {
    getCustomers()
      .then((d) => setRows(d.customers))
      .catch(() => setError("Could not load customers right now."));
  }, []);

  async function handleRevoke(customerId: string) {
    setRevokingId(customerId);
    try {
      await revokeCustomerConsent(customerId);
      setRows(
        (prev) =>
          prev?.map((r) => (r.customer_id === customerId ? { ...r, voice_consent_status: "revoked" } : r)) || null
      );
    } catch {
      setError("Could not update consent for that customer.");
    } finally {
      setRevokingId(null);
    }
  }

  return (
    <>
      <PageHeader eyebrow="Customers" title="Who Kinato can contact" />

      <PageBody>
        {error && <p className="text-sm mb-4 anim-fade" style={{ color: NEGATIVE }}>{error}</p>}

        <AsyncSection
          data={rows}
          error={rows === null ? error : ""}
          empty={
            <EmptyState>
              No customers on file yet — one appears here the moment a checkout or a failed payment identifies one.
            </EmptyState>
          }
        >
          {(list) => (
            <DataTable columns={["Name", "Phone", "Email", "Voice consent", "On file since", ""]}>
              {list.map((r, i) => (
                <Row key={r.customer_id} index={i}>
                  <Cell>{r.name || "—"}</Cell>
                  <Cell numeric>{r.phone || "—"}</Cell>
                  <Cell>{r.email || "—"}</Cell>
                  <Cell>
                    <StatusPill tone={r.voice_consent_status === "granted" ? "positive" : "negative"}>
                      {r.voice_consent_status === "granted" ? "Granted" : "Revoked / none"}
                    </StatusPill>
                  </Cell>
                  <Cell muted numeric>
                    {new Date(r.created_at).toLocaleDateString("en-IN")}
                  </Cell>
                  <Cell>
                    {r.voice_consent_status === "granted" && (
                      <button
                        onClick={() => handleRevoke(r.customer_id)}
                        disabled={revokingId === r.customer_id}
                        className="onb-btn-ghost !p-0 !text-[11px]"
                      >
                        {revokingId === r.customer_id ? "Revoking…" : "Revoke"}
                      </button>
                    )}
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
