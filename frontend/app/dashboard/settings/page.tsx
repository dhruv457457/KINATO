"use client";

import { useEffect, useState } from "react";
import {
  getApiKeys,
  createApiKey,
  revokeApiKey,
  getRazorpayStatus,
  getWebhookUrl,
  saveWebhookSecret,
  getAllowedOrigins,
  setAllowedOrigins,
  connectRazorpay,
  ApiError,
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
  POSITIVE,
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
  const [webhookUrl, setWebhookUrl] = useState<string>("");
  const [publicBaseOk, setPublicBaseOk] = useState<boolean | null>(null);
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
  const [keyIdDraft, setKeyIdDraft] = useState("");
  const [keySecretDraft, setKeySecretDraft] = useState("");
  const [showKeySecret, setShowKeySecret] = useState(false);
  const [rzpKeysSaved, setRzpKeysSaved] = useState(false);
  const [rzpKeyError, setRzpKeyError] = useState("");

  async function handleConnectRazorpay() {
    setBusy(true);
    setRzpKeyError("");
    setRzpKeysSaved(false);
    try {
      await connectRazorpay(keyIdDraft.trim(), keySecretDraft.trim());
      setRzpConnected(true);
      setRzpKeysSaved(true);
      // Clear both fields. A secret left sitting in an input is a secret on
      // screen, and the field is a write-only slot by design - we never
      // read these back.
      setKeyIdDraft("");
      setKeySecretDraft("");
      setShowKeySecret(false);
    } catch (err) {
      // Show what Razorpay actually said. "Could not connect" tells a
      // merchant nothing about whether they pasted a live key into a test
      // field or simply mistyped one character.
      setRzpKeyError(
        err instanceof ApiError ? err.message : "Could not reach the server to verify those keys."
      );
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    getWebhookUrl()
      .then((d) => {
        setWebhookUrl(d.url);
        setPublicBaseOk(d.public_base_configured);
      })
      .catch(() => setPublicBaseOk(false));
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

          {/* Key rotation.
              These were previously settable only during onboarding, as if
              connecting an account were a decision you make once. It isn't:
              keys get rotated, and a test account that hits Razorpay's
              30-payment-link ceiling has to be swapped for a different one
              or nothing can be recovered at all. The only way to do that
              was to create a whole new merchant account. */}
          <div className="border border-t-0 p-5" style={{ borderColor: RULE }}>
            <div className="flex items-center justify-between mb-2">
              <span className="text-[12px] font-semibold uppercase tracking-wider" style={{ color: INK_MUTED }}>
                API key pair
              </span>
              {rzpKeysSaved && (
                <span className="text-[12px] anim-fade" style={{ color: POSITIVE }}>
                  Connected — new keys are live
                </span>
              )}
            </div>
            <p className="text-[12px] mb-3 leading-relaxed" style={{ color: INK_MUTED }}>
              Settings → API Keys in your Razorpay dashboard. Validated against Razorpay before we store them, so a
              typo fails here rather than on your next call. Stored encrypted; replacing them takes effect within
              five minutes.
            </p>

            <div className="flex flex-col gap-3">
              <input
                type="text"
                value={keyIdDraft}
                onChange={(e) => setKeyIdDraft(e.target.value)}
                placeholder="rzp_test_..."
                autoComplete="off"
                spellCheck={false}
                className="border p-3 text-[13px]"
                style={{ borderColor: RULE, borderRadius: 0, background: "transparent", fontFamily: "var(--font-geist-mono)" }}
              />
              <div className="flex gap-3 items-center">
                <input
                  type={showKeySecret ? "text" : "password"}
                  value={keySecretDraft}
                  onChange={(e) => setKeySecretDraft(e.target.value)}
                  placeholder="Key secret"
                  autoComplete="off"
                  spellCheck={false}
                  className="flex-1 border p-3 text-[13px] min-w-0"
                  style={{ borderColor: RULE, borderRadius: 0, background: "transparent", fontFamily: "var(--font-geist-mono)" }}
                />
                <button
                  type="button"
                  onClick={() => setShowKeySecret((v) => !v)}
                  className="text-[11px] font-semibold uppercase tracking-wider shrink-0 px-2"
                  style={{ color: INK_MUTED }}
                >
                  {showKeySecret ? "Hide" : "Show"}
                </button>
                <button
                  className="onb-btn-primary shrink-0"
                  disabled={busy || !keyIdDraft.trim() || !keySecretDraft.trim()}
                  onClick={handleConnectRazorpay}
                >
                  {busy ? "Verifying…" : rzpConnected ? "Replace" : "Connect"}
                </button>
              </div>
            </div>

            {rzpKeyError && (
              <p className="text-[12px] mt-2.5 leading-relaxed" style={{ color: NEGATIVE }}>
                {rzpKeyError}
              </p>
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
          {publicBaseOk === false && (
            <p className="text-[12px] mt-2 leading-relaxed" style={{ color: NEGATIVE }}>
              This deployment has no public base URL configured, so there is no address Razorpay could reach.
              Set <code>NGROK_URL</code> to this server&apos;s public URL (your Railway URL, or a tunnel) and
              reload.
            </p>
          )}

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
