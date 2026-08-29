# Kinato — Judge Demo & Verification Guide

> **Razorpay AI Buildathon 2026 — Track 03: AI Revenue Recovery**
> *"Don't just identify the problem. Show measured money recovered across a batch, with compliant escalation, stopping rules, and an audit trail."*

Kinato is a recovery layer that sits on top of the Razorpay account a merchant
already has. When a payment fails or a checkout is abandoned, an AI agent calls
or emails the customer, negotiates **within limits the merchant sets**, and — if
and only if a deterministic policy engine approves — sends a real Razorpay
payment link. Every money decision it makes is written to an audit trail.

---

## What is real, and what is not

This table is the first thing to read. Everything in this repo is either real or
labelled; nothing is staged to look better than it is.

| Capability | Status |
|---|---|
| Merchant signup, login, session auth (JWT in httpOnly cookie) | **Real** |
| Per-merchant Razorpay test credentials, validated live against Razorpay before being accepted, Fernet-encrypted at rest | **Real** |
| `payment.failed` / `order.created` / `payment.captured` webhooks, HMAC-verified per merchant | **Real** |
| Abandoned-checkout sweeper (DB-backed, survives restarts) | **Real** |
| Agent runtime (LangGraph), bounded iterations + deadline, never raises into the caller | **Real** |
| Two-phase money gate: `check_offer` computes, `issue_offer` acts only on a server-side token | **Real** |
| Deterministic policy engine (discount ceiling, margin floor, product exclusions) | **Real** |
| Real Razorpay Payment Links, with `offer_token` / `recovery_attempt_id` in `notes` | **Real** |
| Outbound voice calls over Twilio (Gather + Play, turn-based) | **Real** |
| Append-only consent ledger; "don't call me again" honoured immediately | **Real** |
| Audit trail of every agent tool call, queryable in the dashboard | **Real** |
| Merchant Q&A grounded strictly in that merchant's own DB rows | **Real** |
| Batch recovery report (`scripts/run_recovery_batch.py`) | **Real pipeline, simulated customer speech** — see below |
| AI Commerce / MCP surface for external AI buyers | **Not multi-tenant yet.** `create_purchase_intent` deliberately returns `REJECTED: catalog_not_yet_multi_tenant` rather than guessing a merchant. The dashboard marks this page "soon" instead of faking it. |
| Speech-to-text on live calls | Twilio's built-in `Gather` transcription. Accents and noise degrade it; this is the weakest link in the live demo. |

### The batch report, stated precisely

`backend/scripts/run_recovery_batch.py` measures money recovered across a batch.
The **pipeline is real**: real eligibility checks, the real policy engine, real
offer tokens, real Razorpay test-mode payment links, real consent gating, real
audit rows. What is **simulated** is the customer's side of the conversation —
scripted utterances stand in for live speech-to-text, so the run is deterministic
and repeatable.

It is **not** N real phone calls. It is the real machinery driven by labelled
inputs. Live calls are real and can be demonstrated separately.

---

## Run it yourself

### 1. Backend

```bash
cd backend && pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in the values you have. The app boots
without Twilio/ElevenLabs keys — those paths degrade visibly rather than
pretending to work.

```bash
cd backend && python run_backend.py
```

* API: `http://localhost:8000`
* Swagger: `http://localhost:8000/docs`

### 2. Frontend

```bash
cd frontend && npm install && npm run dev
```

Open `http://localhost:3000`.

### 3. Test suite

```bash
cd backend && python -m pytest tests/ -q
```

82 tests. They run against a real database — there are no mocked repositories.
Test doubles are used only where a test must not place a real phone call, spend
real money, or call a paid API.

---

## The 5-minute walkthrough

**0:00 — Sign up and connect.** `/login` → create an account → paste Razorpay
**test** keys. They are validated against Razorpay's live API before being
accepted; a wrong key produces a real error, not a fake tick.

**0:45 — Integrate with zero code.** The Integrate step gives you one webhook
URL to paste into your Razorpay dashboard. No script tag, no code on your
storefront. The screen polls for your first real event and says so when it
arrives. If no event comes, it does not trap you — after a wait it lets you
continue, clearly labelled *"continuing without a verified event."*

**1:30 — Set the rules.** `/dashboard/policies`: discount ceiling, margin floor,
calling hours. **The AI can never exceed these**, and that is enforced in code,
not in the prompt.

**2:15 — Trigger a real failure.** Use the Razorpay test dashboard to make a
payment fail. The webhook lands, HMAC is verified, and because `payment.failed`
carries the customer's real email and phone, recovery starts in seconds with no
timer.

**3:00 — Watch the agent work.** `/dashboard/recoveries` → click any row. The
drawer is the centrepiece: every real tool call in order — `get_cart`,
`get_policy_limits`, `check_offer`, `issue_offer` — with its real decision,
latency, and whether it ran degraded.

**4:00 — Show the limits holding.** Ask for a discount above the ceiling. The
policy engine returns `MODIFY` and caps it. The audit trail records what was
requested versus what was approved. Say *"don't call me again"* — consent is
revoked immediately, as a new append-only row, and no further outreach happens.

**4:30 — Ask a question.** `/dashboard/ask`: "How much revenue is at risk right
now?" It answers from that merchant's own rows. With no data, it says so instead
of inventing an example.

---

## Where the money safety actually lives

**A tool call from the model is a request, never an effect.**

1. The model calls `check_offer(discount_percent=40)`. This **applies nothing**.
   It loads the real cart and the real policy, runs the deterministic engine, and
   returns an opaque `offer_token` — plus what was actually approved (perhaps 10%).
2. The only tool that can act is `issue_offer(offer_token=...)`.
3. `issue_offer` reads the money terms **from the server-side token row**, never
   from any argument the model passed.

A hallucinating or prompt-injected model can only hand back an opaque string
whose amounts were computed by code it never touched. A unit test asserts that no
tool schema accepts `merchant_id`, `amount`, `phone`, or `email` from the model.

---

## Stopping rules

* **Consent revoked** → recorded as a new append-only row; checked before every outreach.
* **Razorpay rail down** → `payment.downtime.started` sets a durable per-merchant flag; no customer is called about a failure that may be Razorpay's outage, not their card.
* **No contact details** → recovery is blocked and the reason is surfaced on the dashboard rather than silently dropped.
* **Degraded agent** → an agent running without its LLM cannot call any mutating tool at all.
* **Duplicate webhooks** → deduplicated against a UNIQUE constraint in the database, so a redeploy landing between Razorpay's retries cannot double-count revenue.

---

## Known weaknesses

Listed because pretending they do not exist is worse than having them.

* **Speech-to-text is still the weakest link, but it can no longer move money.** Twilio's `Gather` transcription struggles with accents and background noise, and it fails *silently* — returning a fluent sentence the customer never said. Twilio reports a confidence score on every turn, and it is now read: below 0.6 no money tool will run at all (`REJECTED_LOW_CONFIDENCE`), and below 0.3 the model is not called and the agent asks them to repeat it. Every prompt also accepts the keypad, which transcription cannot corrupt — 9 opts out immediately with no model involved. The agent's ears are still worse than its reasoning; they are no longer trusted with the merchant's margin.
* **AI Commerce is not multi-tenant.** It refuses clearly instead of guessing a merchant.
* **The spend-mandate layer is a Kinato-enforced daily cap on top of real Razorpay Orders** — not an NPCI/RBI UPI Autopay settlement. Real UPI Autopay needs the customer's own in-app approval, which a headless agent cannot complete. `app/payments/spend_mandate.py` says so in its own docstring.
* **ElevenLabs is best-effort.** When it is slow or unreachable the call falls back to Twilio's neural voice, which is rendered on Twilio's infrastructure and cannot fail from our side.
