# Disclosure: trade-offs, boundaries, and what is not built

> **Razorpay AI Buildathon 2026 — Track 03: AI Revenue Recovery**

This document exists so a judge never has to guess which parts of Kinato are
real. `FINDINGS.md` covers what broke. This one covers what we chose, what we
deliberately did not build, and where the edges are.

---

## 1. What runs against real infrastructure

Everything in this list touches a live third-party API in **test mode**, with
real credentials, and fails visibly when that API is down.

| Surface | Real? | Notes |
|---|---|---|
| Razorpay Payment Links | **Real** | Created via the Razorpay API. The link in a recovery email is payable. |
| Razorpay webhooks | **Real** | HMAC-SHA256 verified, path-scoped per merchant, idempotent on redelivery. |
| Twilio voice calls | **Real** | Outbound calls to a real phone, turn-based Gather + Play. |
| ElevenLabs / Twilio Neural TTS | **Real** | ElevenLabs opportunistically, Twilio Neural as the guaranteed fallback. |
| LLM reasoning + tool calling | **Real** | No scripted dialogue anywhere in the call path. |
| Postgres (Supabase) | **Real** | Single source of truth. No seeded rows, no fixtures. |
| Email delivery | **Real** | Sends to the customer's real address on file. |

**There is no demo data anywhere in this repository.** A fresh merchant sees
zeros, not a fabricated benchmark. Every figure on the dashboard is read from a
row that some real event wrote. This was a hard constraint throughout: a
failure that is invisible is worse than a failure that is loud, and seeded data
makes every failure invisible.

## 2. What is simulated, and exactly where

Two things, both disclosed in the artefact that reports them.

1. **Payment-link creation inside the 25-case batch is stubbed.** Razorpay's
   test mode caps payment links at 30, and the batch alone would exhaust that
   in one run. Everything else in the batch is real: the live system prompt,
   the real agent runtime, the real policy engine, the real offer-token gate,
   real audit rows. Only the final HTTP call to mint the link is replaced. The
   batch report says so in its own `methodology` block rather than in a
   footnote somewhere else. A single live recovery — the one in the demo —
   creates a real link.

2. **Autonomous-buyer spend mandates are a Kinato-enforced daily cap, not a
   UPI Autopay mandate.** UPI Autopay requires in-app customer approval, which
   cannot be completed headlessly. The cap is real and enforced; the underlying
   rail is not a live recurring mandate. The module is named
   `spend_mandate.py` — it was previously named after the API it did not call.

Nothing else is stubbed. Where a dependency is unavailable, the system degrades
**visibly**: heuristic paths emit no confidence number at all, and a degraded
agent is structurally forbidden from mutating anything.

## 3. The security boundary

The architectural claim of this project is that the model can *request* money
decisions but can never *make* one.

```text
[ LLM tool call ]
        │
        ▼
[1] Forbidden-argument gate ──► No tool schema may accept merchant_id,
        │                        customer_id, amount, phone or email.
        │                        Asserted at import time, so a violating
        │                        tool cannot be added without the app
        │                        refusing to start.
        ▼
[2] Identity from context ────► merchant_id / customer_id come from
        │                        AgentContext, never from the model.
        ▼
[3] check_offer ─────────────► Loads the REAL cart and the REAL policy.
        │                        Applies nothing. Runs the deterministic
        │                        policy engine and mints an opaque
        │                        offer_token whose amounts are stored
        │                        server-side.
        ▼
[4] issue_offer(offer_token) ─► Money terms are read from the token ROW.
        │                        The model's arguments are not consulted.
        │                        Rejects forged, expired, consumed and
        │                        cross-merchant tokens.
        ▼
[5] Stopping rules ──────────► Consent, quiet hours (IST), call cap,
        │                        already-paid, active promise. Enforced
        │                        before dialling; already-paid re-checked
        │                        every turn.
        ▼
[6] Razorpay ────────────────► Real link. Real HMAC-verified webhook.
                                 Idempotent on redelivery.
```

A prompt-injected or hallucinating model can, at most, hand back an opaque
string whose amounts were computed by code it never touched. This is why the
guarantees in `FINDINGS.md` hold across runs while the conversation does not.

**Other boundaries worth naming:**

- Merchant Razorpay secrets are Fernet-encrypted at rest, with the key from the
  environment. They are never returned by any API, never logged, and never
  rendered in the dashboard.
- Consent is an **append-only** ledger. Revocation is a new row, never an
  UPDATE, so "when did this customer opt out, and what did we do after" is
  answerable rather than inferred.
- Every tool call is audited **including the ones that fail** — the one time
  this mattered most is Finding 7.

## 4. Known limits

Stated plainly, because a judge will find them anyway.

- **Test mode only.** No production Razorpay keys are used or supported today.
- **Voice is turn-based, not full-duplex.** Twilio's Gather + Play model means
  the customer cannot interrupt mid-sentence. Real conversation overlaps; this
  does not.
- **One deployment, one region.** The container runs in Amsterdam, which is
  precisely why quiet hours are computed in IST rather than server time
  (Finding 4). This has not been tested across multiple regions.
- **Batch numbers vary.** Which cases recover and the exact rupees recovered
  depend on LLM sampling. The rule guarantees do not. `FINDINGS.md` states
  which is which rather than presenting a single run as reproducible.
- **The scoreboard is 25 cases, not a production sample.** It is a labelled
  fixture set designed to exercise the stopping rules, not evidence of
  real-world recovery rate.
- **No key rotation or revocation UI.** Deliberately out of scope.

## 5. What we would build next

Ordered by what the current code most obviously wants.

1. **Full-duplex voice**, so a customer can interrupt. The turn-based
   constraint shapes the whole conversation design and is the single biggest
   gap between this and a call that feels natural.
2. **Multi-region quiet hours** driven by each customer's own timezone rather
   than a per-merchant window. The IST fix is correct for one market and
   obviously insufficient for several.
3. **Razorpay Offer objects** rather than a discounted payment link, so a
   discount is an auditable Razorpay entity on their side as well as ours.
4. **Outcome-weighted policy**, using the audit trail already being written:
   which openings, ladders and channels actually convert, per merchant, fed
   back into the ceiling rather than set by hand.
