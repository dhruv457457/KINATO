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
  // Onboarding must never dead-end. This screen used to hard-disable
  // Continue until a real webhook arrived, which meant a merchant setting
  // up before they had live traffic - or whose test account simply never
  // fires an event - could not finish onboarding at all. After a genuine
  // wait we offer an honest way forward instead of trapping them.
  const [waitedLongEnough, setWaitedLongEnough] = useState(false);
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
    const escapeHatch = setTimeout(() => setWaitedLongEnough(true), 20000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
      clearTimeout(escapeHatch);
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

The ONLY integration artifact is this script tag. There is no npm package to
install and no external SDK docs to fetch — do not look for either:

    <script src="${API_URL}/sdk/kinato.js" data-key="PUBLISHABLE_KEY"></script>

The tag auto-initialises from data-key. The full API it exposes on
window.Kinato is exactly these three methods — do not invent others:

    Kinato.init({ apiKey })                      // automatic via data-key
    Kinato.identify({ externalId, name, phone, email,
                      consent: { voice: true, email: true } })
    Kinato.track("checkout.started", { checkoutId, amount, currency, productIds })

1. If this project has no checkout or cart flow at all, STOP and say so —
   this integration only makes sense in a store. Do not invent one.
2. Otherwise add the script tag to the root HTML/layout, call
   Kinato.identify(...) once you know who the customer is, and
   Kinato.track("checkout.started", ...) at the point checkout begins.
3. If there's a Razorpay integration already in the codebase, do NOT
   duplicate payment logic — only add the Kinato event calls around it.
4. Do NOT try to change my Razorpay Dashboard settings yourself. Tell me to
   register this webhook URL manually under Settings -> Webhooks for
   payment.failed and payment.captured:
       ${webhookUrl}
   (If that URL is localhost, tell me it must be a public URL first.)
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
            <div className="onb-status-line err mb-4" style={{ color: "#8A5A1A", borderColor: "#D8B25A" }}>
              <span className="onb-status-dot" style={{ background: "#8A5A1A" }} />
              <span>
                <code>@kinato/react</code> is not published to npm yet — the source lives in this repo under{" "}
                <code>packages/kinato-react</code>. Use the script tag below today; it works on React and
                Next.js apps too.
              </span>
            </div>
            <p className="text-sm text-dark-200 leading-relaxed mb-4">
              Add this to your root layout (Next.js <code>app/layout.tsx</code>, or <code>index.html</code> for
              Vite/CRA). It derives its endpoint from its own <code>src</code>, so no extra config.
            </p>
            <div className="onb-code-block">{`<script src="${API_URL}/sdk/kinato.js"
        data-key="${pkDisplay}"></script>`}</div>
            <button
              className="onb-copy-btn"
              onClick={() => copy(`<script src="${API_URL}/sdk/kinato.js" data-key="${pkDisplay}"></script>`, "react")}
            >
              {copied === "react" ? "Copied" : "Copy snippet"}
            </button>
          </div>
        )}

        {tab === "html" && (
          <div>
            <p className="text-sm text-dark-200 leading-relaxed mb-4">
              One script tag. It derives its endpoint from the tag's own src, so it always talks back to the Kinato instance that served it.
            </p>
            <div className="onb-code-block">{`<script src="${API_URL}/sdk/kinato.js"
        data-key="${pkDisplay}"></script>`}</div>
            <button
              className="onb-copy-btn"
              onClick={() => copy(`<script src="${API_URL}/sdk/kinato.js" data-key="${pkDisplay}"></script>`, "html")}
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

        {eventState === "waiting" && waitedLongEnough && (
          <p className="text-[12.5px] mt-3.5 leading-relaxed anim-fade" style={{ color: "#8A8678", maxWidth: "34rem" }}>
            No event yet — that&apos;s normal if your store hasn&apos;t taken a payment since you added the webhook.
            You can finish setting up and carry on; Kinato starts recovering the moment your first real event
            arrives, and this step will show as verified then.
          </p>
        )}

        <div className="flex gap-3.5 mt-9 pt-5 border-t items-center" style={{ borderColor: "#EAE6D5" }}>
          <button className="onb-btn-secondary" onClick={() => router.push("/onboarding/connect")}>
            Back
          </button>
          <button
            className="onb-btn-primary"
            disabled={eventState !== "received" && !waitedLongEnough}
            onClick={async () => {
              // If no event was verified, the server is still parked on
              // "integrate" - and the funnel guard will bounce us straight
              // back here unless we advance the step first. (events-check
              // does this itself once a real event lands.)
              if (eventState !== "received") {
                try {
                  await apiFetch("/api/merchant/onboarding/integrate/skip", { method: "POST" });
                } catch {
                  /* non-fatal: worst case the guard returns us here and the
                     merchant can retry, rather than silently losing the click */
                }
              }
              router.push("/onboarding/catalog");
            }}
          >
            Continue
          </button>
          {eventState === "waiting" && waitedLongEnough && (
            <span className="text-[11px] uppercase tracking-wider anim-fade" style={{ color: "#8A8678" }}>
              Continuing without a verified event
            </span>
          )}
        </div>
      </div>
    </>
  );
}
