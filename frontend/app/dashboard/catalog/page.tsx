"use client";

import { useEffect, useState } from "react";
import { getCatalog, setProductVisibility, uploadCatalogCsv, formatInr, ProductRow } from "@/lib/api";
import {
  PageHeader,
  PageBody,
  DataTable,
  Row,
  Cell,
  StatusPill,
  EmptyState,
  AsyncSection,
  INK_MUTED,
  NEGATIVE,
} from "@/components/dashboard/primitives";

function marginPct(pricePaise: number, cogsPaise: number | null): string {
  if (!cogsPaise || pricePaise <= 0) return "—";
  return `${(((pricePaise - cogsPaise) / pricePaise) * 100).toFixed(1)}%`;
}

export default function CatalogPage() {
  const [rows, setRows] = useState<ProductRow[] | null>(null);
  const [error, setError] = useState("");
  const [togglingId, setTogglingId] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadResult, setUploadResult] = useState<string>("");

  useEffect(() => {
    load();
  }, []);

  function load() {
    getCatalog()
      .then((d) => setRows(d.products))
      .catch(() => setError("Could not load your catalog right now."));
  }

  async function handleUpload(file: File) {
    setUploading(true);
    setError("");
    setUploadResult("");
    try {
      const res = await uploadCatalogCsv(file);
      // Re-uploading is an upsert by sku, so this doubles as "update my prices".
      setUploadResult(
        `Imported ${res.imported} product${res.imported === 1 ? "" : "s"}` +
          (res.skipped?.length ? ` · skipped ${res.skipped.length} (missing sku, name, or price)` : "")
      );
      load();
    } catch (e: any) {
      setError(e?.message || "Could not import that CSV.");
    } finally {
      setUploading(false);
    }
  }

  async function handleToggle(productId: string, next: boolean) {
    setTogglingId(productId);
    try {
      await setProductVisibility(productId, next);
      setRows(
        (prev) => prev?.map((r) => (r.product_id === productId ? { ...r, visible_to_ai_buyers: next } : r)) || null
      );
    } catch {
      setError("Could not update visibility for that product.");
    } finally {
      setTogglingId(null);
    }
  }

  return (
    <>
      <PageHeader
        eyebrow="Catalog"
        title="What Kinato knows you sell"
        actions={
          <label className="onb-btn-secondary shrink-0" style={{ cursor: uploading ? "wait" : "pointer" }}>
            {uploading ? "Importing…" : "Upload CSV"}
            <input
              type="file"
              accept=".csv,text/csv"
              className="hidden"
              disabled={uploading}
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) handleUpload(f);
                e.target.value = ""; // allow re-uploading the same filename
              }}
            />
          </label>
        }
      />

      <PageBody>
        {uploadResult && (
          <p className="text-sm mb-4 anim-fade" style={{ color: "#1F6B3C" }}>
            {uploadResult}
          </p>
        )}
        {error && <p className="text-sm mb-4 anim-fade" style={{ color: NEGATIVE }}>{error}</p>}

        <AsyncSection
          data={rows}
          error={rows === null ? error : ""}
          empty={
            <EmptyState>
              No products on file yet. Use <strong>Upload CSV</strong> above — columns
              <code> sku, name, price</code> (required) plus <code>cogs, inventory</code>. COGS is what makes the
              margin floor real when the AI negotiates a discount.
            </EmptyState>
          }
        >
          {(list) => (
            <DataTable columns={["Product", "Price", "COGS", "Margin", "Inventory", "Visible to AI buyers"]}>
              {list.map((r, i) => (
                <Row key={r.product_id} index={i}>
                  <Cell>
                    <div className="font-medium">{r.name}</div>
                    <div className="text-[11px]" style={{ color: INK_MUTED }}>
                      {r.product_id}
                    </div>
                  </Cell>
                  <Cell numeric>{formatInr(r.price_paise)}</Cell>
                  <Cell muted numeric>
                    {r.cogs_paise ? formatInr(r.cogs_paise) : "—"}
                  </Cell>
                  <Cell numeric>{marginPct(r.price_paise, r.cogs_paise)}</Cell>
                  <Cell numeric>{r.inventory_count}</Cell>
                  <Cell>
                    <button
                      onClick={() => handleToggle(r.product_id, !r.visible_to_ai_buyers)}
                      disabled={togglingId === r.product_id}
                      className="onb-btn-ghost !p-0 !text-[11px]"
                    >
                      {togglingId === r.product_id ? (
                        "…"
                      ) : (
                        <StatusPill tone={r.visible_to_ai_buyers ? "positive" : "neutral"}>
                          {r.visible_to_ai_buyers ? "Visible" : "Hidden"}
                        </StatusPill>
                      )}
                    </button>
                  </Cell>
                </Row>
              ))}
            </DataTable>
          )}
        </AsyncSection>

        <div className="mt-6 text-[11px] max-w-lg" style={{ color: INK_MUTED }}>
          &ldquo;Visible to AI buyers&rdquo; only controls the AI Commerce surface (external AI agents purchasing
          via MCP), which is still being built — this toggle is stored for real today, but nothing reads it yet.
        </div>
      </PageBody>
    </>
  );
}
