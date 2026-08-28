"use client";

import { useEffect, useState } from "react";
import {
  API_URL,
  getCurrentMerchant,
  getApiKeys,
  createApiKey,
  revokeApiKey,
  getRazorpayStatus,
  saveWebhookSecret,
  getAllowedOrigins,
  setAllowedOrigins,
  ApiKeyRow,
} from "@/lib/api";
import {
  PageHeader,
  PageBody,
  SectionTitle,
  DataTable,
  Row,
  Cell,
  StatusPill,
  RULE,
  INK,
  INK_MUTED,
  WASH,
  NEGATIVE,
} from "@/components/dashboard/primitives";

/** Copy-to-clipboard field. Used for both the webhook URL and a freshly
 *  minted key — the two things a merchant must move somewhere else. */
function CopyField({ value, mono = true }: { value: string; mono?: boolean }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      /* clipboard blocked (insecure context) — the value is selectable anyway */
    }
  }

  return (
    <div className="flex items-stretch gap-0 border" style={{ borderColor: RULE }}>
      <div
        className="flex-1 p-3 text-[12px] break-all"
        style={{ background: WASH, fontFamily: mono ? "var(--font-geist-mono)" : undefined }}
      >
        {value || "…"}
      </div>
      <button
        onClick={copy}
        disabled={!value}
        className="px-4 text-[11px] font-semibold uppercase tracking-wider border-l transition-colors shrink-0"
        style={{ borderColor: RULE, color: copied ? "#1F6B3C" : INK }}
      >
        {copied ? "Copied" : "Copy"}
      </button>
    </div>
  );
}

export default function SettingsPage() {
  const [merchantId, setMerchantId] = useState<string | null>(null);
  const [keys, setKeys] = useState<ApiKeyRow[] | null>(null);
  const [rzpConnected, setRzpConnected] = useState<boolean | null>(null);
  const [webhookSecretSet, setWebhookSecretSet] = useState<boolean | null>(null);
  const [secretDraft, setSecretDraft] = useState("");
  const [secretSaved, setSecretSaved] = useState(false);
  const [origins, setOrigins] = useState<string[] | null>(null);
  const [originsDraft, setOriginsDraft] = useState("");
  const [originsSaved, setOriginsSaved] = useState(false);
  const [newKey, setNewKey] = useState<{ key: string; key_type: string } | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getCurrentMerchant().then((m) => setMerchantId(m?.merchant_id || null));
    getApiKeys().then((d) => setKeys(d.keys)).catch(() => setError("Could not load API keys."));
    getRazorpayStatus()
      .then((d) => {
        setRzpConnected(d.connected);
        setWebhookSecretSet(d.webhook_secret_set);
      })
      .catch(() => {});
    getAllowedOrigins()
      .then((d) => {
        setOrigins(d.origins);
        setOriginsDraft(d.origins.join("\n"));
      })
      .catch(() => {});
  }, []);

  async function handleCreateKey(keyType: "publishable" | "secret") {
    setBusy(true);
    setError("");
    try {
      const res = await createApiKey(keyType);
      setNewKey({ key: res.key, key_type: keyType });
      setKeys((await getApiKeys()).keys);
    } catch {
      setError("Could not create a new key.");
    } finally {
      setBusy(false);
    }
  }

  async function handleRevoke(keyId: string) {
    setBusy(true);
    try {
      await revokeApiKey(keyId);
      setKeys((prev) => prev?.map((k) => (k.key_id === keyId ? { ...k, revoked_at: new Date().toISOString() } : k)) || null);
    } catch {
      setError("Could not revoke that key.");
    } finally {
      setBusy(false);
    }
  }

  async function handleSaveWebhookSecret() {
    setBusy(true);
    setError("");
    try {
      await saveWebhookSecret(secretDraft.trim());
      setWebhookSecretSet(true);
      setSecretDraft("");
      setSecretSaved(true);
      setTimeout(() => setSecretSaved(false), 2500);
    } catch (e: any) {
      setError(e?.message || "Could not save the webhook secret.");
    } finally {
      setBusy(false);
    }
  }

  async function handleSaveOrigins() {
    setBusy(true);
    setError("");
    try {
      const list = originsDraft.split("\n").map((s) => s.trim()).filter(Boolean);
      setOrigins((await setAllowedOrigins(list)).origins);
      setOriginsSaved(true);
      setTimeout(() => setOriginsSaved(false), 2000);
    } catch {
      setError("Could not save allowed origins.");
    } finally {
      setBusy(false);
    }
  }

  const webhookUrl = merchantId ? `${API_URL}/webhooks/razorpay/${merchantId}` : "";

  return (
    <>
      <PageHeader eyebrow="Settings" title="Connections & access" />

      <PageBody narrow>
        {error && <p className="text-sm mb-6 anim-fade" style={{ color: NEGATIVE }}>{error}</p>}

        <section className="mb-12 anim-rise">
          <SectionTitle>Razorpay</SectionTitle>
          <div className="border p-5 flex items-center justify-between" style={{ borderColor: RULE }}>
            <span className="text-[13px]">Test-mode account</span>
            {rzpConnected === null ? (
              <span className="anim-shimmer h-[11px] w-[90px] rounded-sm" />
            ) : (
              <StatusPill tone={rzpConnected ? "positive" : "negative"}>
                {rzpConnected ? "Connected" : "Not connected"}
              </StatusPill>
            )}
          </div>
        </section>

        <section className="mb-12 anim-rise">
          <SectionTitle>Webhook URL</SectionTitle>
          <p className="text-[12px] mb-2.5" style={{ color: INK_MUTED }}>
            Paste this into your Razorpay dashboard&apos;s webhook settings. This one URL is the whole zero-code
            integration — a failed payment reaches Kinato without you touching your storefront.
          </p>
          <CopyField value={webhookUrl} />

          <div className="mt-6">
            <div className="flex items-center justify-between mb-2">
              <span className="text-[12px] font-semibold uppercase tracking-wider" style={{ color: INK_MUTED }}>
                Webhook signing secret
              </span>
              {webhookSecretSet !== null && (
                <StatusPill tone={webhookSecretSet ? "positive" : "negative"}>
                  {webhookSecretSet ? "Set" : "Not set"}
                </StatusPill>
              )}
            </div>

            {!webhookSecretSet && (
              <p className="text-[12px] mb-2.5 leading-relaxed" style={{ color: NEGATIVE }}>
                Until this is set, every event Razorpay sends is <strong>rejected</strong> — we refuse webhooks we
                can&apos;t verify, so recovery never starts. This is the secret you typed into the
                &ldquo;Secret&rdquo; field when creating the webhook in Razorpay; it isn&apos;t one of your API keys.
              </p>
            )}

            <div className="flex gap-3 items-center">
              <input
                type="password"
                value={secretDraft}
                onChange={(e) => setSecretDraft(e.target.value)}
                placeholder={webhookSecretSet ? "Enter a new secret to replace it" : "Paste the secret from Razorpay"}
                className="flex-1 border p-3 text-[13px]"
                style={{ borderColor: RULE, borderRadius: 0, background: "transparent", fontFamily: "var(--font-geist-mono)" }}
              />
              <button
                className="onb-btn-primary shrink-0"
                disabled={busy || secretDraft.trim().length < 8}
                onClick={handleSaveWebhookSecret}
              >
                Save
              </button>
              {secretSaved && (
                <span className="text-sm anim-fade shrink-0" style={{ color: "#1F6B3C" }}>
                  Saved
                </span>
              )}
            </div>
            <p className="text-[11px] mt-2" style={{ color: INK_MUTED }}>
              Stored encrypted. Never shown again after saving — replace it here if you rotate it in Razorpay.
            </p>
          </div>
        </section>

        <section className="mb-12 anim-rise">
          <SectionTitle>API keys</SectionTitle>
          {newKey && (
            <div className="border p-4 mb-4 anim-rise" style={{ borderColor: INK }}>
              <div className="text-[12px] font-semibold mb-2">
                New {newKey.key_type} key — shown once. Save it now.
              </div>
              <CopyField value={newKey.key} />
            </div>
          )}

          {!keys ? (
            <div className="anim-shimmer h-[46px] mb-4" />
          ) : keys.length > 0 ? (
            <div className="mb-4">
              <DataTable columns={["Type", "Prefix", "Created", "Status", ""]}>
                {keys.map((k, i) => (
                  <Row key={k.key_id} index={i}>
                    <Cell>
                      <span className="capitalize">{k.key_type}</span>
                    </Cell>
                    <Cell>
                      <span style={{ fontFamily: "var(--font-geist-mono)" }}>{k.key_prefix}…</span>
                    </Cell>
                    <Cell muted numeric>
                      {new Date(k.created_at).toLocaleDateString("en-IN")}
                    </Cell>
                    <Cell>
                      <StatusPill tone={k.revoked_at ? "negative" : "positive"}>
                        {k.revoked_at ? "Revoked" : "Active"}
                      </StatusPill>
                    </Cell>
                    <Cell>
                      {!k.revoked_at && (
                        <button onClick={() => handleRevoke(k.key_id)} disabled={busy} className="onb-btn-ghost !p-0 !text-[11px]">
                          Revoke
                        </button>
                      )}
                    </Cell>
                  </Row>
                ))}
              </DataTable>
            </div>
          ) : (
            <p className="text-[12px] mb-4" style={{ color: INK_MUTED }}>
              No keys yet. You only need one if you want to send events from your own code — the webhook URL above
              covers the zero-code path.
            </p>
          )}

          <div className="flex gap-3">
            <button onClick={() => handleCreateKey("publishable")} disabled={busy} className="onb-btn-secondary !text-[11.5px] !py-2.5">
              + Publishable key
            </button>
            <button onClick={() => handleCreateKey("secret")} disabled={busy} className="onb-btn-secondary !text-[11.5px] !py-2.5">
              + Secret key
            </button>
          </div>
        </section>

        <section className="anim-rise">
          <SectionTitle>Allowed origins</SectionTitle>
          <p className="text-[12px] mb-2.5" style={{ color: INK_MUTED }}>
            Domains permitted to send events with a publishable key. One per line.
          </p>
          <textarea
            value={originsDraft}
            onChange={(e) => setOriginsDraft(e.target.value)}
            rows={4}
            placeholder="https://yourstore.com"
            className="w-full border p-3 text-[13px]"
            style={{ borderColor: RULE, fontFamily: "var(--font-geist-mono)", borderRadius: 0, background: "transparent" }}
          />
          <div className="flex items-center gap-4 mt-3">
            <button onClick={handleSaveOrigins} disabled={busy} className="onb-btn-primary">
              Save
            </button>
            {originsSaved && (
              <span className="text-sm anim-fade" style={{ color: "#1F6B3C" }}>
                Saved
              </span>
            )}
          </div>
          {origins && (
            <p className="text-[11px] mt-2.5" style={{ color: INK_MUTED }}>
              Currently saved: {origins.length === 0 ? "none" : origins.join(", ")}
            </p>
          )}
        </section>
      </PageBody>
    </>
  );
}
