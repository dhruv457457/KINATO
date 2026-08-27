"use client";

import { useEffect, useState } from "react";
import { getCustomers, revokeCustomerConsent, CustomerRow } from "@/lib/api";

export default function CustomersPage() {
  const [rows, setRows] = useState<CustomerRow[] | null>(null);
  const [error, setError] = useState("");
  const [revokingId, setRevokingId] = useState<string | null>(null);

  useEffect(() => {
    load();
  }, []);

  function load() {
    getCustomers()
      .then((d) => setRows(d.customers))
      .catch(() => setError("Could not load customers right now."));
  }

  async function handleRevoke(customerId: string) {
    setRevokingId(customerId);
    try {
      await revokeCustomerConsent(customerId);
      setRows((prev) => prev?.map((r) => (r.customer_id === customerId ? { ...r, voice_consent_status: "revoked" } : r)) || null);
    } catch {
      setError("Could not update consent for that customer.");
    } finally {
      setRevokingId(null);
    }
  }

  return (
    <>
      <header className="border-b px-14 py-10" style={{ borderColor: "#D5D0BC" }}>
        <div className="text-[11px] font-semibold uppercase tracking-widest text-dark-200 mb-2">Customers</div>
        <h1 className="font-serif font-semibold text-[28px]">Who Kinato can contact</h1>
      </header>

      <div className="flex-1 px-14 py-10 w-full">
        {error && <p className="text-sm text-red-700 mb-4">{error}</p>}
        {!rows && !error && <p className="text-sm text-dark-200">Loading…</p>}

        {rows && rows.length === 0 && (
          <div className="border p-6 text-sm text-dark-200 max-w-lg" style={{ borderColor: "#D5D0BC" }}>
            No customers on file yet — one appears here the moment a checkout or a failed payment identifies one.
          </div>
        )}

        {rows && rows.length > 0 && (
          <div className="overflow-x-auto border" style={{ borderColor: "#D5D0BC" }}>
            <table className="w-full text-[13px]" style={{ borderCollapse: "collapse" }}>
              <thead>
                <tr className="border-b" style={{ borderColor: "#D5D0BC", background: "#F5F3E9" }}>
                  {["Name", "Phone", "Email", "Voice consent", "On file since", ""].map((h) => (
                    <th key={h} className="text-left px-4 py-3 text-[10.5px] font-semibold uppercase tracking-wider text-dark-200">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.customer_id} className="border-b" style={{ borderColor: "#EAE6D5" }}>
                    <td className="px-4 py-3">{r.name || "—"}</td>
                    <td className="px-4 py-3 tabular-nums">{r.phone || "—"}</td>
                    <td className="px-4 py-3">{r.email || "—"}</td>
                    <td className="px-4 py-3">
                      <span
                        className="text-[11px] font-semibold uppercase"
                        style={{ color: r.voice_consent_status === "granted" ? "#1F6B3C" : "#A3372A" }}
                      >
                        {r.voice_consent_status === "granted" ? "Granted" : "Revoked / none"}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-dark-200 tabular-nums">{new Date(r.created_at).toLocaleDateString("en-IN")}</td>
                    <td className="px-4 py-3">
                      {r.voice_consent_status === "granted" && (
                        <button
                          onClick={() => handleRevoke(r.customer_id)}
                          disabled={revokingId === r.customer_id}
                          className="onb-btn-ghost !p-0 !text-[11px]"
                        >
                          {revokingId === r.customer_id ? "Revoking…" : "Revoke"}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}
