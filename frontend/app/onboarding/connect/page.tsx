"use client";

import { useState, FormEvent } from "react";
import { useRouter } from "next/navigation";
import { apiFetch, ApiError } from "@/lib/api";

export default function ConnectRazorpayPage() {
  const router = useRouter();
  const [keyId, setKeyId] = useState("");
  const [keySecret, setKeySecret] = useState("");
  const [status, setStatus] = useState<"idle" | "verifying" | "ok" | "err">("idle");
  const [message, setMessage] = useState("");

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setStatus("verifying");
    setMessage("Verifying against Razorpay…");
    try {
      const data = await apiFetch<{ status: string; message: string }>("/api/merchant/razorpay/connect", {
        method: "POST",
        body: JSON.stringify({ key_id: keyId.trim(), key_secret: keySecret.trim() }),
      });
      setStatus("ok");
      setMessage(data.message || "Verified — connected to Razorpay test account");
      setTimeout(() => router.push("/onboarding/integrate"), 500);
    } catch (err) {
      setStatus("err");
      setMessage(err instanceof ApiError ? err.message : "Could not reach the server.");
    }
  }

  return (
    <>
      <header className="border-b px-16 py-11 flex items-end gap-5" style={{ borderColor: "#D5D0BC" }}>
        <div className="font-serif font-bold text-[60px] leading-none tabular-nums">02</div>
        <div className="pb-1">
          <div className="text-[11px] font-semibold uppercase tracking-widest text-dark-200 mb-2">
            Connect Razorpay
          </div>
          <h1 className="font-serif font-semibold text-[28px] leading-tight">Paste your test-mode API keys</h1>
        </div>
      </header>

      <div className="flex-1 px-16 py-13 max-w-[600px] w-full">
        <p className="text-sm text-dark-200 leading-relaxed mb-7">
          Kept encrypted at rest. We validate the key live against Razorpay before accepting it — a typo fails
          here, not three screens later.
        </p>

        <form onSubmit={handleSubmit}>
          <div className="onb-field mb-6">
            <label>Key ID</label>
            <input
              type="text"
              required
              placeholder="rzp_test_••••••••••"
              value={keyId}
              onChange={(e) => setKeyId(e.target.value)}
            />
            <div className="hint">Settings → API Keys in your Razorpay Dashboard.</div>
          </div>
          <div className="onb-field mb-6">
            <label>Key secret</label>
            <input
              type="password"
              required
              placeholder="••••••••••••••••"
              value={keySecret}
              onChange={(e) => setKeySecret(e.target.value)}
            />
          </div>

          {status !== "idle" && (
            <div className={`onb-status-line ${status === "verifying" ? "pending" : status === "ok" ? "ok" : "err"}`}>
              <span className="onb-status-dot" />
              <span>{message}</span>
            </div>
          )}

          <div className="flex gap-3.5 mt-9 pt-5 border-t" style={{ borderColor: "#EAE6D5" }}>
            <button type="submit" className="onb-btn-primary" disabled={status === "verifying"}>
              {status === "verifying" ? "Verifying…" : "Verify & continue"}
            </button>
          </div>
        </form>
      </div>
    </>
  );
}
