# Findings: what broke, and what we did about it

> **Razorpay AI Buildathon 2026 — Track 03: AI Revenue Recovery**

Every failure below is real, was hit while running the product against a live
storefront and a real phone, and is fixed in this repository with a regression
test. Several were found only because a person used the thing and said "that
felt wrong."

The pattern worth noticing: almost none of these threw an exception. The
system kept reporting success while doing the wrong thing.

---

## 1. The agent discounted a sale it had already won

Verbatim, from a live call:

```
Customer: "I want to check that out right now. Can you send me the link?"
Agent:    "Before we proceed, could you share what stopped you?"
Customer: "No, I don't want to share that. Can you please send me the link?"
Agent:    (asks a second time anyway)
Customer: "my card got declined"
Agent:    "Would you like me to offer a discount?"   -> 14% approved
```

A customer ready to pay full price, asking twice for a link, was interrogated,
refused twice, then handed 14% off he never requested. Worse, *"my card got
declined"* is not a price objection at all — that customer wants to pay the
full amount and needs a working link.

**Cause.** Our own prompt made discovery mandatory rather than conditional.
The script outranked the sale.

**Fix.** The sale comes first: any signal the customer wants to buy sends a
full-price link immediately. The agent never volunteers a discount, never asks
twice why they didn't complete, and treats a declined card as a broken
checkout rather than a price problem.

**Why it matters.** Nothing failed. No error, no exception, no failing test.
The agent followed its instructions exactly, and the instructions were subtly
against the merchant's interest. This is the hardest class of bug in an
agentic system, and it was caught by a human saying the call made him
uncomfortable — not by any assertion we had written.

The correction then overshot the other way: the agent began sending full price
to customers who *had* explicitly said the price was too high. Both directions
lose money. The rule now distinguishes "ready to buy" from "raised price."

---

## 2. A money action was cancelled mid-flight, and the customer was told the opposite

```
11:44:44.999  Generating Payment Link for rec_be35f6c4c297
11:44:46.586  Created Razorpay Payment Link plink_TVB5C9hmdgE2UK
11:44:46.587  Agent: "let me have someone follow up with you by email"
```

`issue_offer` worked. A real, payable link existed. Then the reasoning
deadline fired 1.5 seconds later, cancelled the agent mid-tool, and the
fallback message told the customer the opposite of what had just happened. No
audit row, no email, a live payment link stranded in Razorpay.

**Fix.** Mutating tools are shielded from cancellation. Once a money action
starts it always finishes; the deadline may end the conversation turn, never a
half-applied effect. When the deadline fires with mutations in flight, the
runtime waits for them to settle before reporting what happened.

**The instructive part.** The *first* version of that fix stored the shield
wrapper instead of the inner task. Cancelling a shield cancels only the
wrapper — so the real work kept running but became unreachable and
unawaitable. The same half-applied effect, dressed as a fix. It looked correct
on review and passed a naive test that exercised `asyncio.shield` rather than
our runtime. Only a test that drives the real agent to its actual deadline
catches it.

---

## 3. Every SDK checkout was silently unrecoverable

The SDK identifies customers by a merchant-chosen `externalId`, in practice an
email. That string was written straight into `checkouts.customer_id`, while
consent is recorded against the real `cust_...` id. So the pre-outreach consent
lookup searched for a customer literally named `someone@example.com`, found no
granted consent, and blocked recovery — for a customer who *had* consented.

The dashboard showed ₹11,46,000 of revenue at risk and **zero** recovery
attempts against it, with nothing anywhere explaining why. No exception, no
rejected event, no error line.

**Fix.** Incoming identifiers resolve to the real customer by id, external id,
email or phone, and an unresolvable identifier logs a warning naming it, so the
next occurrence is visible rather than silent.

---

## 4. "Compliant" was a claim, not a mechanism

`merchant_policies.calling_start_hour` / `calling_end_hour` had been in the
schema since it was written, were editable on the Policies screen, and **no
service read them.** A merchant setting 10:00–20:00 got no protection at all.
There was likewise no cap on how many times one checkout could be dialled, and
the "already paid" check ran once before the phone rang — so a customer who
paid *during* the call was still being sold to.

**Fix.** All three are enforced before dialling, and "already paid" is
re-checked every conversation turn. Quiet hours are evaluated in **IST, not
server time**: this deployment runs in Amsterdam, where 22:30 UTC is 04:00 for
the customer. Server-time logic would have rung them in the middle of the night
while reporting full compliance.

A `rule_breaks` counter now appears on the dashboard and in the batch report.
It is not a metric to improve — any non-zero value means a guarantee broke.

---

## 5. One dropped call blocked that cart from ever recovering

Seven recovery attempts sat in `CALLING`, the oldest for over an hour; 21 such
rows existed across all merchants. A call can end with no completion signal
ever reaching us — Twilio gives up waiting, the customer drops, the network
dies mid-turn — and nothing moved the attempt out of `CALLING`.

Because an in-flight attempt blocks new recovery for that checkout, **a single
dropped call meant that cart could never be recovered again.**

Compounding it, the terminal-state list read `('RECOVERED','FAILED','OPTED_OUT')`
while the code actually writes `CALL_FAILED` and `CONSENT_REVOKED`. So even a
*correctly* failed call, or a customer who explicitly opted out, counted as
still in flight and blocked their checkout permanently. The state names merely
looked plausible.

**Fix.** The sweeper closes attempts stuck mid-call after 10 minutes, and the
terminal states are now the ones the code actually writes.

---

## 6. The measurement harness was testing an agent that no longer existed

The batch runner carried its own copy of the system prompt. It had silently
drifted, so every behavioural fix made to the live call was invisible to the
scoreboard. Full-price recoveries read **zero** purely because of that.

A reproducible measurement of something other than production is worse than no
measurement: it produces confident numbers about a system that isn't running.
The batch now imports the live prompt builder, so drift is impossible.

Two related traps in the same harness:

- **Pinning temperature to 0 for reproducibility made it worse.** Greedy
  decoding collapsed the agent into a loop of clarifying questions and it never
  called `issue_offer` — recoveries went from 6 to 0. The batch runs at the live
  temperature and the variance is stated rather than hidden.
- **The scoreboard was time-of-day dependent.** Run after 20:00 IST, every case
  failed on quiet hours instead of the rule it was written to exercise. The same
  bug then appeared in the test suite itself, and the first fix for *that*
  disabled the very tests that verify quiet hours.

---

## 7. An external quota impersonated a code regression

Recoveries dropped to zero immediately after a prompt edit. Every instinct said
the edit broke it. The audit trail said otherwise:

```
check_offer   ALLOW     approved 0%, token minted
issue_offer   REJECTED  "test mode limit of 30 reached for payment_link"
```

The agent had reasoned correctly every time. Razorpay simply refused to mint
link 31. Without per-tool auditing, three good changes would have been reverted
chasing a phantom.

This is the concrete argument for auditing every tool call *including the ones
that fail* — the audit trail is what distinguishes "our code is wrong" from
"the world changed."

---

## 8. We measured the obvious optimisation and did not ship it

`/dashboard/overview` makes three independent reads: ~1.5s locally, several
seconds from Railway. Running them concurrently is the textbook fix.

Measured, it is **80% slower**: 2507ms against 1387ms, best-of-3 with an
already-warm pool. Sequential calls reuse one hot pooled connection, while
concurrency forces three separate ones, and establishing those costs more here
than the overlapping round trips save. The endpoint stays sequential, with the
measurement recorded in a comment so nobody "fixes" it again.

Notably, the *same technique* applied to the agent tools was a real 3.9× win
(1.60s to 0.41s), because there the connections were long-lived and the work
was spread across a request lifecycle. Same idea, opposite result — which is
the argument for measuring rather than pattern-matching.

---

## 9. A nice-to-have dependency sat on the critical path of a hard deadline

Twilio gives a webhook roughly 15 seconds to answer before it gives up and the
call ends. ElevenLabs produces a much better voice than Twilio's built-in one,
so we called it first and fell back on timeout.

The budget was 4 seconds, with a retry. Live, two attempts plus overhead came
close enough to Twilio's ceiling to trip it intermittently: a real call dropped
because we were still waiting on a *voice quality upgrade*. ElevenLabs has been
the least reliable dependency in this system by a wide margin.

**Fix.** The budget is now 2 seconds, and it caps the whole opportunistic
attempt rather than each retry, so the fallback always has room to run. Twilio's
own neural voice renders on Twilio's infrastructure and cannot fail from our
side.

The general shape: an optional quality improvement was allowed to consume a
budget owned by a hard availability requirement. The fix is not a better
timeout value, it is making the optional thing structurally unable to spend
more than its share.

---

## What the guarantees do and don't depend on

The scoreboard makes one distinction sharply, and it is the architectural claim
of this project:

| | Varies between runs? |
|---|---|
| Which cases recover, exact rupees recovered | **Yes** — LLM sampling |
| `rule_breaks = 0` | **No** |
| Refusals held (already paid / no consent / opt-out) | **No** |
| Approved discount never exceeded the ceiling | **No** |

The agent's *conversation* varies. What it is *allowed to do* does not, because
the stops are enforced in code rather than by the model. A hallucinating or
prompt-injected model can only hand back an opaque token whose amounts were
computed by code it never touched.

Across 25 labelled cases: **rule breaks 0**, 10 of 10 refusals held before any
contact, and **130 percentage points of discount requested against 30
approved** — 100 points of margin the policy engine protected, read from real
audit rows.
