"use client";

/**
 * Catalog upload, in two steps: read the file, then apply what the merchant
 * confirmed.
 *
 * It used to be one step against an endpoint that required a header row
 * saying exactly `sku, name, price` in row one. Real exports say "SKU Code"
 * and "Selling Price", carry a title line above the table, and write money
 * as "Rs. 1,299/-" - so the common case was a rejected file and a merchant
 * being told to go and edit their spreadsheet.
 *
 * The confirm step exists for one column in particular. `cogs` is what the
 * merchant PAID for the goods, and it is one of the two inputs to the
 * margin floor: map it to the wrong column and you change which discounts
 * are legal for that merchant on every future call. That is a decision to
 * put in front of a person, not to infer silently - the same reason
 * /policy/propose proposes rather than writes.
 */

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { API_URL, apiFetch, ApiError, formatInr } from "@/lib/api";

const FIELDS = ["sku", "name", "price", "cogs", "inventory", "description"] as const;
type Field = (typeof FIELDS)[number];

const FIELD_LABEL: Record<Field, string> = {
  sku: "Product code",
  name: "Product name",
  price: "Price charged",
  cogs: "Your cost",
  inventory: "Stock",
  description: "Description",
};

interface Proposal {
  columns: string[];
  mapping: Record<string, string | null>;
  model_suggested: string[];
  unmapped_columns: string[];
  notes: string[];
  usable: boolean;
  header_row: number;
  preview: {
    product_id: string;
    name: string;
    price_paise: number;
    cogs_paise: number | null;
    inventory_count: number;
  }[];
  total_rows: number;
  rejected: { row: string; reason: string }[];
  rejected_total: number;
}

interface UploadResult {
  imported: number;
  skipped: string[];
}

export default function CatalogPage() {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [status, setStatus] = useState<"idle" | "reading" | "applying" | "err">("idle");
  const [error, setError] = useState("");
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [mapping, setMapping] = useState<Record<string, string | null>>({});
  const [result, setResult] = useState<UploadResult | null>(null);

  async function post(path: string, f: File, extra?: Record<string, string>) {
    const form = new FormData();
    form.append("file", f);
    Object.entries(extra || {}).forEach(([k, v]) => form.append(k, v));
    const res = await fetch(`${API_URL}/api/merchant/onboarding/${path}`, {
      method: "POST",
      credentials: "include",
      body: form,
    });
    const data = await res.json();
    if (!res.ok) throw new ApiError(data.detail || "Upload failed", res.status);
    return data;
  }

  async function read(f: File) {
    setFile(f);
    setStatus("reading");
    setError("");
    setResult(null);
    setProposal(null);
    try {
      const data: Proposal = await post("catalog/propose", f);
      setProposal(data);
      setMapping(data.mapping);
      setStatus("idle");
    } catch (err) {
      setStatus("err");
      setError(err instanceof ApiError ? err.message : "Could not reach the server.");
    }
  }

  async function apply() {
    if (!file) return;
    setStatus("applying");
    setError("");
    try {
      setResult(await post("catalog", file, { mapping: JSON.stringify(mapping) }));
      setStatus("idle");
    } catch (err) {
      setStatus("err");
      setError(err instanceof ApiError ? err.message : "Could not reach the server.");
    }
  }

  async function skip() {
    try {
      await apiFetch("/api/merchant/onboarding/catalog/skip", { method: "POST" });
    } finally {
      router.push("/onboarding/policy");
    }
  }

  const requiredMissing = ["sku", "name", "price"].filter((f) => !mapping[f]);

  return (
    <>
      <header className="border-b px-16 py-11 flex items-end gap-5" style={{ borderColor: "#D5D0BC" }}>
        <div className="font-serif font-bold text-[60px] leading-none tabular-nums">04</div>
        <div className="pb-1">
          <div className="text-[11px] font-semibold uppercase tracking-widest text-dark-200 mb-2">Catalog</div>
          <h1 className="font-serif font-semibold text-[28px] leading-tight">Upload your product costs</h1>
        </div>
      </header>

      <div className="flex-1 px-16 py-13 max-w-[720px] w-full">
        <p className="text-sm text-dark-200 leading-relaxed mb-7">
          Your cost price is what makes the margin floor real when the agent negotiates, instead of a
          number nobody&apos;s checking. Upload the file you already have — we&apos;ll work out the
          columns and show you what we read before anything is saved.
        </p>

        {!result && (
          <>
            <div
              className={`onb-dropzone ${dragging ? "drag" : ""}`}
              onClick={() => inputRef.current?.click()}
              onDragOver={(e) => {
                e.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDragging(false);
                if (e.dataTransfer.files[0]) read(e.dataTransfer.files[0]);
              }}
            >
              <div className="text-xl mb-2.5">↑</div>
              <div>
                <strong className="text-dark">Click to upload</strong> or drag a CSV file here
              </div>
              <div className="text-[11px] mt-1.5">
                Any column names. Extra rows above the table are fine.
              </div>
            </div>
            <input
              ref={inputRef}
              type="file"
              accept=".csv"
              className="hidden"
              onChange={(e) => e.target.files?.[0] && read(e.target.files[0])}
            />
          </>
        )}

        {status === "reading" && (
          <div className="onb-status-line pending mt-4">
            <span className="onb-status-dot" />
            <span>Reading {file?.name}…</span>
          </div>
        )}

        {status === "err" && (
          <div className="onb-status-line err mt-4">
            <span className="onb-status-dot" />
            <span>{error}</span>
          </div>
        )}

        {proposal && !result && (
          <div className="mt-7">
            <div className="text-[11px] font-semibold uppercase tracking-widest text-dark-200 mb-3">
              What we read — change anything that&apos;s wrong
            </div>

            <div className="grid grid-cols-2 gap-x-6 gap-y-3 mb-5">
              {FIELDS.map((f) => (
                <label key={f} className="text-sm">
                  <span className="block text-dark-200 text-[12px] mb-1">
                    {FIELD_LABEL[f]}
                    {["sku", "name", "price"].includes(f) && " *"}
                    {proposal.model_suggested.includes(f) && (
                      <span className="ml-1.5 text-[10px] uppercase tracking-wide font-semibold">
                        suggested — check this
                      </span>
                    )}
                  </span>
                  <select
                    className="w-full border px-2 py-1.5 text-[13px] bg-transparent"
                    style={{ borderColor: "#D5D0BC" }}
                    value={mapping[f] ?? ""}
                    onChange={(e) =>
                      setMapping({ ...mapping, [f]: e.target.value || null })
                    }
                  >
                    <option value="">— not in this file —</option>
                    {proposal.columns.filter(Boolean).map((c) => (
                      <option key={c} value={c}>
                        {c}
                      </option>
                    ))}
                  </select>
                </label>
              ))}
            </div>

            {proposal.notes.map((n, i) => (
              <div key={i} className="text-[12px] text-dark-200 leading-relaxed mb-1.5">
                · {n}
              </div>
            ))}

            {proposal.preview.length > 0 && (
              <div className="mt-5 border" style={{ borderColor: "#EAE6D5" }}>
                <div className="text-[11px] font-semibold uppercase tracking-widest text-dark-200 px-3 py-2 border-b" style={{ borderColor: "#EAE6D5" }}>
                  First {proposal.preview.length} of {proposal.total_rows}
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-[12.5px]">
                    <tbody>
                      {proposal.preview.map((p) => (
                        <tr key={p.product_id} className="border-b last:border-0" style={{ borderColor: "#EAE6D5" }}>
                          <td className="px-3 py-1.5 font-medium">{p.product_id}</td>
                          <td className="px-3 py-1.5">{p.name}</td>
                          <td className="px-3 py-1.5 tabular-nums">{formatInr(p.price_paise)}</td>
                          <td className="px-3 py-1.5 tabular-nums text-dark-200">
                            {p.cogs_paise === null ? "no cost" : `cost ${formatInr(p.cogs_paise)}`}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {proposal.rejected_total > 0 && (
              <div className="mt-4">
                <div className="text-[12px] text-dark-200 mb-1.5">
                  {proposal.rejected_total} row{proposal.rejected_total === 1 ? "" : "s"} won&apos;t be
                  imported:
                </div>
                {proposal.rejected.map((r, i) => (
                  <div key={i} className="text-[12px] text-dark-200 leading-relaxed">
                    · <strong>{r.row}</strong> — {r.reason}
                  </div>
                ))}
              </div>
            )}

            <button
              className="onb-btn-primary mt-6"
              disabled={requiredMissing.length > 0 || status === "applying"}
              onClick={apply}
            >
              {status === "applying"
                ? "Saving…"
                : requiredMissing.length > 0
                ? `Tell us which column holds the ${requiredMissing.join(", ")}`
                : `Import ${proposal.total_rows} product${proposal.total_rows === 1 ? "" : "s"}`}
            </button>
          </div>
        )}

        {result && (
          <div className="onb-status-line ok mt-4">
            <span className="onb-status-dot" />
            <span>
              Imported {result.imported} product{result.imported === 1 ? "" : "s"}
              {result.skipped.length > 0 && ` — ${result.skipped.length} row(s) skipped`}
            </span>
          </div>
        )}

        <div className="flex gap-3.5 mt-9 pt-5 border-t items-center" style={{ borderColor: "#EAE6D5" }}>
          <button className="onb-btn-secondary" onClick={() => router.push("/onboarding/integrate")}>
            Back
          </button>
          <button className="onb-btn-ghost" onClick={skip}>
            Skip for now
          </button>
          <button
            className="onb-btn-primary"
            disabled={!result}
            onClick={() => router.push("/onboarding/policy")}
          >
            Continue
          </button>
        </div>
      </div>
    </>
  );
}
