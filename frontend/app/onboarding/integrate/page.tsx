"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { apiFetch, getCurrentMerchant, API_URL } from "@/lib/api";

type Tab = "zero" | "react" | "html" | "ai";

interface ApiKeyRow {
  key_id: string;
  key_type: "publishable" | "secret";
  key_prefix: string;
  revoked_at: string | null;
}

export default function IntegratePage() {
  const router = useRouter();
  const [tab, setTab] = useState<Tab>("zero");
  const [merchantId, setMerchantId] = useState("");
  const [webhookUrl, setWebhookUrl] = useState("");
  const [pkKey, setPkKey] = useState<string | null>(null); // full raw key - only ever held here, shown once
  const [pkExisting, setPkExisting] = useState<ApiKeyRow | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const [eventState, setEventState] = useState<"waiting" | "received">("waiting");
  const [eventMeta, setEventMeta] = useState("");
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    (async () => {
      const merchant = await getCurrentMerchant();
      if (!merchant) return;
      setMerchantId(merchant.merchant_id);
      setWebhookUrl(`${API_URL}/webhooks/razorpay/${merchant.merchant_id}`);

      const { keys } = await apiFetch<{ keys: ApiKeyRow[] }>("/api/merchant/api-keys");
      const existingPk = keys.find((k) => k.key_type === "publishable" && !k.revoked_at);
      if (existingPk) {
        setPkExisting(existingPk);
      } else {
        const created = await apiFetch<{ key: string }>("/api/merchant/api-keys", {
          method: "POST",
          body: JSON.stringify({ key_type: "publishable" }),
        });
        setPkKey(created.key);
      }
    })();
  }, []);

  useEffect(() => {
    async function poll() {
      try {
        const data = await apiFetch<{ received: boolean; event_type?: string; created_at?: string }>(
          "/api/merchant/onboarding/events-check"
        );
        if (data.received) {
          setEventState("received");
          setEventMeta(data.event_type || "");
          if (pollRef.current) clearInterval(pollRef.current);
        }
      } catch {
        /* keep polling silently - a transient network blip shouldn't scare the merchant */
      }
    }
    poll();
    pollRef.current = setInterval(poll, 4000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  function copy(text: string, label: string) {
    navigator.clipboard?.writeText(text).catch(() => {});
    setCopied(label);
    setTimeout(() => setCopied(null), 1400);
  }

  const pkDisplay = pkKey || (pkExisting ? `${pkExisting.key_prefix}••••••••` : "generating…");

  const aiPrompt = `Integrate Kinato checkout-recovery tracking into this project.

Publishable key: ${pkKey || "(already issued — check your saved key, or revoke and reissue from Settings)"}
Webhook URL:     ${webhookUrl}

1. Detect the stack (Next.js/React, plain HTML, Shopify, etc.) and pick the
   matching integration from https://docs.kinato.app/sdk — the React
   provider, the HTML script tag, or the zero-code webhook.
2. If this is a Next.js/React app: install @kinato/react, wrap the root
   layout in <KinatoProvider publishableKey="...">, and call
   useKinato().trackCheckoutStarted(...) where checkout begins.
3. If there's a Razorpay integration already in the codebase, do NOT
   duplicate payment logic — only add the Kinato event calls around it.
4. Register the webhook URL above in the Razorpay Dashboard under
   Settings -> Webhooks for payment.failed and payment.captured.
5. Show me the diff before committing anything.`;

  return (
    <>
      <header className="border-b px-16 py-11 flex items-end gap-5" style={{ borderColor: "#D5D0BC" }}>
        <div className="font-serif font-bold text-[60px] leading-none tabular-nums">03</div>
        <div className="pb-1">
          <div className="text-[11px] font-semibold uppercase tracking-widest text-dark-200 mb-2">Integrate</div>
          <h1 className="font-serif font-semibold text-[28px] leading-tight">Tell us when a checkout happens</h1>
        </div>
      </header>

      <div className="flex-1 px-16 py-13 max-w-[640px] w-full">
        {pkKey && (
          <div className="onb-status-line err mb-6" style={{ color: "#8A5A1A", borderColor: "#D8B25A" }}>
            <span className="onb-status-dot" style={{ background: "#8A5A1A" }} />
            <span>
              This key (<code>{pkKey}</code>) is shown once — copy it into the snippet below before leaving this
              page.
            </span>
          </div>
        )}

        <div className="flex border-b mb-6" style={{ borderColor: "#D5D0BC" }}>
          {([
            ["zero", "Zero code"],
            ["react", "React / Next.js"],
            ["html", "HTML / other"],
            ["ai", "Ask your AI"],
          ] as [Tab, string][]).map(([key, label]) => (
            <button key={key} className={`onb-tab ${tab === key ? "active" : ""}`} onClick={() => setTab(key)}>
              {label}
            </button>
          ))}
        </div>

        {tab === "zero" && (
          <div>
            <p className="text-sm text-dark-200 leading-relaxed mb-4">
              Recommended — nothing to install on your site. Paste this into Razorpay Dashboard → Settings →
              Webhooks, and enable the <code>payment.failed</code> and <code>payment.captured</code> events.
            </p>
            <div className="onb-code-block">{webhookUrl}</div>
            <button className="onb-copy-btn" onClick={() => copy(webhookUrl, "zero")}>
              {copied === "zero" ? "Copied" : "Copy URL"}
            </button>
          </div>
        )}

        {tab === "react" && (
          <div>
            <p className="text-sm text-dark-200 leading-relaxed mb-4">
              Wrap your app once, then call <code>useKinato()</code> anywhere a checkout starts.
            </p>
            <div className="onb-code-block">{`npm install @kinato/react

import { KinatoProvider } from "@kinato/react";

<KinatoProvider publishableKey="${pkDisplay}">
  <App />
</KinatoProvider>`}</div>
            <button className="onb-copy-btn" onClick={() => copy("npm install @kinato/react", "react")}>
              {copied === "react" ? "Copied" : "Copy snippet"}
            </button>
          </div>
        )}

        {tab === "html" && (
          <div>
            <p className="text-sm text-dark-200 leading-relaxed mb-4">
              One script tag. It auto-detects your store from the tag's own URL.
            </p>
            <div className="onb-code-block">{`<script src="https://cdn.kinato.app/kinato.js"
        data-key="${pkDisplay}"></script>`}</div>
            <button
              className="onb-copy-btn"
              onClick={() => copy(`<script src="https://cdn.kinato.app/kinato.js" data-key="${pkDisplay}"></script>`, "html")}
            >
              {copied === "html" ? "Copied" : "Copy snippet"}
            </button>
          </div>
        )}

        {tab === "ai" && (
          <div>
            <p className="text-sm text-dark-200 leading-relaxed mb-4">
              Paste this into Claude Code, Cursor, or any AI coding assistant with access to your codebase. It
              reads your project, picks the right integration path, and wires it up itself.
            </p>
            <div className="onb-code-block">{aiPrompt}</div>
            <button className="onb-copy-btn" onClick={() => copy(aiPrompt, "ai")}>
              {copied === "ai" ? "Copied" : "Copy prompt"}
            </button>
          </div>
        )}

        <div className={`onb-status-line mt-8 ${eventState === "waiting" ? "pending" : "ok"}`}>
          <span className="onb-status-dot" />
          <span>
            {eventState === "waiting"
              ? "Waiting for your first event…"
              : `Received ${eventMeta} from your store`}
          </span>
        </div>

        <div className="flex gap-3.5 mt-9 pt-5 border-t items-center" style={{ borderColor: "#EAE6D5" }}>
          <button className="onb-btn-secondary" onClick={() => router.push("/onboarding/connect")}>
            Back
          </button>
          <button
            className="onb-btn-primary"
            disabled={eventState !== "received"}
            onClick={() => router.push("/onboarding/catalog")}
          >
            Continue
          </button>
        </div>
      </div>
    </>
  );
}
