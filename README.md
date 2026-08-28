# KINATO

> **Razorpay AI Buildathon 2026 — Track 03: AI Revenue Recovery**
> *"Don't just identify the problem. Show measured money recovered across a batch, with compliant escalation, stopping rules, and an audit trail."*

**Kinato is a recovery layer that sits on top of the Razorpay account a merchant already has.**

When a payment fails or a checkout is abandoned, an AI agent calls or emails the customer, finds out what actually stopped them, and — if and only if a deterministic policy engine approves — sends a real Razorpay payment link. Every money decision it makes is written to an audit trail the merchant can read.

Setup is one webhook URL. No code on the storefront.

---

## The one idea everything hangs on

**A tool call from the model is a request, never an effect.**

1. The model calls `check_offer(discount_percent=40)`. This **applies nothing**. It loads the real cart and the real policy, runs a deterministic engine, and returns an opaque `offer_token` — plus what was *actually* approved, which may be 10%.
2. The only tool that can act is `issue_offer(offer_token=…)`.
3. `issue_offer` reads the money terms **from the server-side token row** — never from any argument the model passed.

A hallucinating or prompt-injected model can only hand back an opaque string whose amounts were computed by code it never touched. A unit test asserts no tool schema accepts `merchant_id`, `amount`, `phone`, or `email` from the model.

"Bounded" is structural here, not a promise in a prompt.

---

## Architecture

AI reasoning is separated from deterministic services. The agent decides *what to say*; code decides *what money moves*.

```text
  payment.failed / checkout.abandoned          ← Razorpay webhook, or DB sweeper
                 │
                 ▼
       RECOVERY ELIGIBILITY                    ← stopping rules live here
       ├─ already paid?                          (consent, rail health,
       ├─ consent granted?                        contactable, in-flight)
       ├─ Razorpay rail degraded?
       └─ already being recovered?
                 │
                 ▼
       RECOVERY STRATEGIST                     ← LLM: opening line only
                 │
                 ▼
       CALL ORCHESTRATOR ──────────► TWILIO (outbound voice)
                 │                          │
                 ▼                          ▼
          AGENT RUNTIME  ◄─────── customer speech, per turn
          (LangGraph, bounded)
                 │
        ┌────────┴─────────┐
        ▼                  ▼
   check_offer         record_opt_out         ← the only mutating tools
        │                  │
        ▼                  ▼
  POLICY ENGINE      CONSENT LEDGER           ← deterministic, append-only
  (ceiling, margin    (revocation is a
   floor, exclusions)  new row, never
        │              an UPDATE)
        ▼
   issue_offer ──────► RAZORPAY PAYMENT LINK ──► email
                              │
                        webhook (HMAC verified)
                              │
                              ▼
                    ATTRIBUTION + AUDIT LOG
```

Every agent tool call — its arguments, decision, latency, and whether it ran degraded — is written to `audit_log` and visible in the dashboard's recovery drawer.

---

## What's real, and what isn't

| Capability | Status |
|---|---|
| Merchant signup, session auth, per-merchant Razorpay keys (validated live, Fernet-encrypted) | **Real** |
| `payment.failed` webhooks, HMAC-verified per merchant | **Real** |
| Abandoned-checkout sweeper (DB-backed, survives restarts) | **Real** |
| Agent runtime (LangGraph), bounded iterations + deadline, never raises | **Real** |
| Two-phase money gate (`check_offer` → `issue_offer`) | **Real** |
| Deterministic policy engine: discount ceiling, margin floor, product exclusions | **Real** |
| Real Razorpay Payment Links, with `offer_token` in `notes` | **Real** |
| Outbound voice over Twilio (Gather + Play, turn-based) | **Real** |
| Append-only consent ledger; "don't call me again" honoured immediately | **Real** |
| Merchant Q&A grounded strictly in that merchant's own rows | **Real** |
| Batch recovery report | **Real pipeline, simulated customer speech** — see `docs/JUDGE-DEMO.md` |
| AI-buyer commerce (MCP) | **Not multi-tenant.** `create_purchase_intent` returns `REJECTED: catalog_not_yet_multi_tenant` rather than guessing a merchant. The dashboard marks it "soon" instead of faking it. |
| WhatsApp | **Not built.** Offers go by email. |

Speech-to-text is Twilio's built-in `Gather` transcription and is the weakest link in the live demo — accents and line noise degrade it noticeably. `docs/JUDGE-DEMO.md` lists the known weaknesses in full.

---

## Quick start

```bash
cd backend && pip install -r requirements.txt
cp .env.example .env      # fill in what you have; the app boots without Twilio/ElevenLabs
python run_backend.py
```

```bash
cd frontend && npm install && npm run dev
```

Open `http://localhost:3000`.

```bash
cd backend && python -m pytest tests/ -q
```

Tests run against a real database — no mocked repositories. Test doubles are used only where a test must not place a real phone call or spend real money.

---

## Integration

**Zero code (recommended).** Paste one webhook URL into the Razorpay dashboard:

```
https://<your-kinato-host>/webhooks/razorpay/<merchant_id>
```

Enable `payment.failed` and `payment.captured`. `payment.failed` is the primary trigger — it carries the customer's real email and phone, so recovery starts in seconds with no timer and nothing on the storefront.

**With the SDK** (adds abandoned-cart coverage):

```html
<script src="https://<your-kinato-host>/sdk/kinato.js" data-key="pk_test_..."></script>
```

Exposes exactly three methods: `Kinato.init`, `Kinato.identify`, `Kinato.track`.

---

## Stopping rules

- **Consent revoked** → recorded as a new append-only row; checked before every outreach.
- **Razorpay rail down** → `payment.downtime.started` sets a durable flag; no customer is called about a failure that may be Razorpay's outage rather than their card.
- **No contact details** → recovery blocked, and the reason surfaced on the dashboard rather than silently dropped.
- **Degraded agent** → an agent running without its LLM cannot call any mutating tool at all.
- **Duplicate webhooks** → deduplicated against a database UNIQUE constraint, so a redeploy landing between Razorpay's retries cannot double-count revenue.
- **The sale comes first** → a customer ready to buy is sent a full-price link. The agent never volunteers a discount, and a declined card is treated as a broken checkout, not a price objection.

---

## Documentation

- **`docs/JUDGE-DEMO.md`** — verification guide, real-vs-simulated table, known weaknesses.
