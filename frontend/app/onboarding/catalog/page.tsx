"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { API_URL, apiFetch, ApiError } from "@/lib/api";

interface UploadResult {
  imported: number;
  skipped: string[];
  product_ids: string[];
}

export default function CatalogPage() {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [status, setStatus] = useState<"idle" | "uploading" | "err">("idle");
  const [error, setError] = useState("");
  const [result, setResult] = useState<UploadResult | null>(null);

  async function upload(f: File) {
    setFile(f);
    setStatus("uploading");
    setError("");
    setResult(null);
    const form = new FormData();
    form.append("file", f);
    try {
      const res = await fetch(`${API_URL}/api/merchant/onboarding/catalog`, {
        method: "POST",
        credentials: "include",
        body: form,
      });
      const data = await res.json();
      if (!res.ok) throw new ApiError(data.detail || "Upload failed", res.status);
      setResult(data);
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

  return (
    <>
      <header className="border-b px-16 py-11 flex items-end gap-5" style={{ borderColor: "#D5D0BC" }}>
        <div className="font-serif font-bold text-[60px] leading-none tabular-nums">04</div>
        <div className="pb-1">
          <div className="text-[11px] font-semibold uppercase tracking-widest text-dark-200 mb-2">Catalog</div>
          <h1 className="font-serif font-semibold text-[28px] leading-tight">Upload your product costs</h1>
        </div>
      </header>

      <div className="flex-1 px-16 py-13 max-w-[600px] w-full">
        <p className="text-sm text-dark-200 leading-relaxed mb-7">
          COGS (cost of goods sold) is required — it&apos;s what makes the margin floor real when the AI
          negotiates a discount, instead of a number nobody&apos;s checking.
        </p>

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
            if (e.dataTransfer.files[0]) upload(e.dataTransfer.files[0]);
          }}
        >
          <div className="text-xl mb-2.5">↑</div>
          <div>
            <strong className="text-dark">Click to upload</strong> or drag a CSV file here
          </div>
          <div className="text-[11px] mt-1.5">sku, name, price, cogs, inventory</div>
        </div>
        <input
          ref={inputRef}
          type="file"
          accept=".csv"
          className="hidden"
          onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])}
        />

        {status === "uploading" && (
          <div className="onb-status-line pending mt-4">
            <span className="onb-status-dot" />
            <span>Parsing {file?.name}…</span>
          </div>
        )}

        {status === "err" && (
          <div className="onb-status-line err mt-4">
            <span className="onb-status-dot" />
            <span>{error}</span>
          </div>
        )}

        {result && (
          <>
            <div className="onb-status-line ok mt-4">
              <span className="onb-status-dot" />
              <span>
                Imported {result.imported} product{result.imported === 1 ? "" : "s"}
                {result.skipped.length > 0 && ` — skipped ${result.skipped.length} row(s) with missing data`}
              </span>
            </div>
          </>
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
