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

## 10. Twilio told us how well it heard, on every turn, and nothing read it

Every Gather callback Twilio has ever posted to this app carried a
`Confidence` float alongside `SpeechResult`. The handler read the words and
dropped the number on the floor.

So a turn like this:

```
SpeechResult: "yes that sounds fine send it"
Confidence:   0.14
```

reached the agent with exactly the same authority as a clean 0.95 utterance,
and could mint an offer token and send a real payment link on the strength of
it. Speech-to-text was already documented as the weakest link in this system.
What was not appreciated is *how* it fails: not with an error, but with a
fluent, plausible sentence that the customer never said. The agent then
reasons impeccably about words that do not exist.

**Fix.** Two thresholds, both enforced in code rather than asked of the prompt:

- Below **0.3** the model is not called at all. There is nothing to reason
  about, so the agent says the line broke up and asks them to repeat it. Two
  consecutive misses stop asking — being asked a third time is where a bad
  line becomes an irritating call — and offer the keypad instead.
- Below **0.6** the conversation continues normally, but `check_offer` and
  `issue_offer` refuse outright with `REJECTED_LOW_CONFIDENCE`. The agent can
  still acknowledge, confirm, and close warmly. It cannot spend the merchant's
  margin on a sentence we are not sure we heard.

`issue_offer` checks independently of `check_offer`, deliberately: a token
minted on a clearly-heard turn must not become spendable by a later garbled
"yes" that was never a yes.

**And the keypad, which cannot be misheard at all.** Every `<Gather>` now
accepts `speech dtmf`. A digit arrives as a digit, at confidence 1.0 by
construction — it is the only input path in this system that transcription
cannot corrupt, which is exactly why opt-out belongs on it: 9 revokes consent
immediately, with no model involved, and still writes a real audit row because
it goes through the same `execute_tool` choke point as everything else. When a
digit and a transcription arrive together, the digit wins. Acting on the
speech there would mean selling to someone who had just asked to be left alone.

**The instructive part** is where the first version of the barrier-confirmation
rule went wrong. Requiring the customer to confirm a stated price objection
before any discount is right on a phone call. Implementing it as a default on
`AgentContext` was not: every caller that built a context without thinking
about it — the batch scoreboard, and any future channel — silently lost the
ability to discount at all. Eight existing tests failed and were, correctly,
pointing at the product rather than at themselves. The rule now has an explicit
scope (`input_is_speech`), because it exists to guard against *mishearing*, and
a keypad press or a typed reply cannot be misheard. A guarantee whose blast
radius is decided by a default value is not a guarantee; it is an accident that
has not happened yet.

The batch runner sets that flag too, and carries the same confirmation state
machine, so the scoreboard keeps measuring the agent that actually runs — the
exact drift #6 is about, which would otherwise have reappeared the same day it
was fixed.

---

## 11. The reminder we were careful to send only once was never sent at all

Promise-to-pay is one of the more thought-through things in this codebase.
A customer who says "I'll pay Friday" has outreach paused until Friday. If
Friday passes unpaid, the sweeper allows exactly **one** reminder, enforced
by a `promise_reminded_at` column so it can never become a campaign. There
are tests. They pass.

```python
await bus.publish(
    event_type="recovery.promise_lapsed",
    payload={...,  "reminder": "final"},
    idempotency_key=f"promise_reminder_{attempt['recovery_attempt_id']}",
)
```

Nothing subscribed to `recovery.promise_lapsed`. Not one handler, ever. The
event fired, the row was marked reminded, the audit trail recorded a
reminder — and no email reached any customer for the entire life of the
feature.

The care was real. The reminder was imaginary.

**Why the tests didn't catch it.** Every existing test asserted the thing
the code was proud of: that a lapsed promise *surfaces*, and that it is
marked reminded exactly once. All of that was true. None of them asked
whether a customer ever received anything, because the restraint was the
interesting part and the delivery was assumed.

**Fix.** A real subscriber, with three refusals in front of it — all of
which would otherwise turn a helpful nudge into the thing merchants get
complaints about:

- **They have paid.** The sweeper's query already excludes paid checkouts;
  this checks again at the last moment before the email leaves, because a
  payment landing in that gap is exactly the race worth losing safely.
- **They have opted out — on *any* channel.** `record_opt_out` revokes the
  voice channel, and a per-channel check would have let the email go out to
  someone who had said "don't contact me again" on the phone. Consent to
  contact is per-channel and must be granted explicitly; a refusal is read
  as broadly as possible. The asymmetry is deliberate: the safe direction
  is different for the two questions.
- **There is no link on file.** Only Razorpay's payment-link *id* was
  stored, never the payable URL, so the one thing needed to remind someone
  of a promise existed nowhere once the event carrying it had been handled.
  The URL is persisted now, and an attempt without one sends nothing —
  an email that asks for money with no way to pay it is worse than silence.

**The pattern, which is the reason this is written down.** This is the same
shape as #4's `calling_start_hour` and #5's terminal-state list: a column,
a state, or an event that *looks* wired up, is described in comments as
wired up, has tests around the half that is real — and is connected to
nothing at the far end. Publishing to an empty subscriber list is not an
error in any language. It is indistinguishable from working, right up until
someone asks a customer whether they got the email.

Worth noting what the fix does **not** do: it does not add a second
reminder, a retry, or an escalation. The original restraint was correct.
It just needed to be restraint about something that happens.

---

## 12. The integration on the front page of the README recovered nothing

The product's headline pitch is one sentence:

> Setup is one webhook URL. No code on the storefront.

A merchant who did exactly that got **zero recoveries, and no explanation**.

The trace is four steps long and nothing in it throws:

1. `payment.failed` arrives. `upsert_by_contact` creates the customer from
   Razorpay's own payload — email, phone, everything needed to reach them.
2. It records **no consent**, because nothing asked it to.
3. `recovery_eligibility` requires a granted `voice` consent row.
4. It fails, and `return`s — **without publishing `recovery.blocked`**,
   unlike the `no_contact` and `rail_degraded` branches immediately above
   it, which both do.

The only place consent had ever been granted was `customer.identified` from
the JavaScript SDK — the integration path the README explicitly describes as
optional.

So the recommended setup produced a customer row, a checkout row, and
silence. The dashboard showed revenue at risk with nothing recovered against
it and nothing anywhere saying why, which is the exact symptom of FINDINGS
#3 reappearing in a different part of the pipeline.

**A second bug was hiding inside the first.** The gate asked about *voice*
specifically, so a customer with an email address and no phone number was
refused outright — not "recovered by email instead", refused. That one would
have survived the consent fix untouched.

**Fix.** Consent is recorded when a payment fails, for the channels the
contact details actually support, with `source="razorpay_transactional"` so
a merchant can tell it apart from an explicit opt-in in the ledger. It is
defensible on its own terms: the customer typed those details into this
merchant's checkout minutes earlier, in order to pay this merchant, and the
contact is about that specific failed payment. The gate now asks which
channels are open rather than whether one particular channel is, and a
refusal publishes `recovery.blocked{reason: no_consent}` with copy on the
dashboard explaining what to do about it.

**The part worth being careful about is the fix, not the bug.** Two ways it
could have been worse than what it replaced, both now covered by tests:

- The consent ledger is append-only and *the latest row wins*. Inserting a
  grant without checking would silently resurrect anyone who had opted
  out — and every subsequent failed payment would do it again. A customer
  who asked us to stop would be re-enrolled by the act of trying to pay.
  The revocation check is the first thing the function does.
- Granting `email` made a dormant bug live: `record_opt_out` revoked
  *voice only*. That was survivable while voice was the only channel anyone
  was ever contacted on. The moment email became real, "take me off your
  list", said on a phone call, would have stopped the calls and kept the
  emails. Opt-out now covers every channel, including ones we have not
  tried them on yet. Nobody should have to opt out once per protocol.

Note the asymmetry that came out of this, which is now deliberate: consent
to contact is **per channel and must be granted explicitly**, while a
refusal is read **as broadly as possible**. The safe direction is different
for the two questions, so they are answered by two different functions.

**Why this went unnoticed for so long.** Every test in the suite that
exercised recovery built its own consent row first, because that is what a
test does when it wants to test the thing after the gate. The gate itself
was never the subject. `tests/test_zero_code_recovery.py` starts one step
earlier — from a merchant who has pasted in a webhook URL and done nothing
else — and four of its assertions fail against the old code. That was
verified by disabling the fix and watching them go red, rather than assumed.

---

## 13. A safety check we added that morning quietly re-created #1

The mishearing work (#10) added a rule: before proposing a discount, read
the barrier back to the customer and get an answer. On a phone call that is
obviously right. It shipped with tests, and the tests passed.

Then the ablation harness ran this case:

```
Customer: "Can you do 40% off? Otherwise I am not interested."
Agent:    (asks whether price is the problem)
[call ends]                                    outcome: NO SALE
```

The customer had stated the barrier themselves, in their own words,
unmistakably — and the agent answered by asking them to confirm the thing
they had just said. On a short call there is no second turn, so the sale
was simply lost.

That is **FINDINGS #1 again**: the script outranking the sale. The first
version of that bug was a prompt that made discovery mandatory. This one
was a *guardrail* doing the same thing, which is worse, because a guardrail
is exactly the kind of code nobody re-examines — it is on the safety side,
so its failures look like caution.

**Cause.** The rule was written as "confirm before every spoken discount"
when what it was actually for was "confirm when we might have misheard."
Those are the same rule only if you assume every spoken turn is equally
trustworthy — and #10 exists precisely because they are not.

**Fix.** Confirmation now applies only in the band where mishearing is
plausible: above the floor where money may move at all (0.6), below the
point where the transcription is clean (0.85). A 0.95-confidence "can you
do 40% off" goes straight to the policy engine, which caps it at 10% and
says why. Three confidence bands, three behaviours, one coherent idea:
**confirmation is a remedy for mishearing, not a negotiation ritual.**

**What is worth noticing** is what caught it. Not a test — the tests were
written from the same misunderstanding as the code, so they agreed with it.
Not review. It was `scripts/run_ablation.py`, a harness built for an
entirely different purpose: proving the guardrails are load-bearing by
removing them. Running the *gated* arm surfaced a gated-arm bug on the
first case that exercised it.

A harness that runs the real agent over real cases finds things that
assertions written alongside the code cannot, because it does not share
the author's assumptions. That is the same lesson as #6, arriving from the
opposite direction: there, a drifting harness measured an agent that no
longer existed; here, an honest harness measured the agent that did, and
disagreed with its author.

---

## 14. Two safe-looking changes, one symptom: every recovery went to zero

The ablation harness reported `recovered: 0` in all three arms. Not fewer
recoveries — none, across twenty-five cases. Both causes were changes made
that same day, both looked like improvements, and neither raised an error.

### Cause one: the right check, asked about the wrong thing

`issue_offer` used to verify VOICE consent before sending. Delivering by
email while checking the phone channel is plainly the wrong question, so it
was changed to check consent on the delivery channel instead.

That is more correct and it is worse. Consent is granted per channel, and
plenty of customers have granted voice and not email. Those customers were
now phoned, agreed to the offer on the call — and had it silently refused.
Worse still, the call's fallback line says:

> "Great news - I've sent that offer to your email, you should see it any
> moment."

Nothing had been sent. That is **FINDINGS #2 exactly**: the customer told
the opposite of what happened, by a well-intentioned change to a different
part of the system.

The mistake was framing. At that point in a call the customer has *asked*
for the link, during a conversation we already had consent for. Sending it
completes a request; it does not initiate contact. The right question is
therefore the broad one — "have they told us to stop?" — not "do we hold a
separate opt-in for this protocol?" A customer who opted out anywhere gets
nothing; everyone else gets the thing they just asked for.

### Cause two: the prompt got longer, so the agent stopped selling

The mishearing work added about 1,050 characters of instruction to the live
system prompt: how to handle the confirmation turn, and what the keypad
does. Every sentence was accurate. The prompt went from ~5,600 to ~6,650
characters.

The agent then did this, with the customer saying *"just send me the link
again"*:

```
Turn 1  "Hello - can you hear me alright? Am I speaking with Rahul?"
Turn 2  "I'm calling from Loomwork. I noticed you were looking at a linen
         shirt but didn't finish checking out..."
[no tool calls at all]
```

It was working through the numbered opening sequence while the customer
asked twice to buy. The prompt still contained **THE SALE COMES FIRST**, in
capitals, unchanged. It had simply been diluted: instructions compete for
attention, and adding more of them weakens every one already there.

**Fix.** The additions were cut from 1,048 characters to 391. Almost all of
the confirmation explanation was deleted outright — not softened, deleted —
because `check_offer` already returns `REJECTED_UNCONFIRMED_BARRIER` with a
sentence saying what to do about it. The prompt does not need to pre-teach
a rule the tool states at the moment it applies. The keypad went from a
paragraph to one line. Recoveries returned immediately.

### The rule this project should hold itself to

Prompt space is a budget, not a document. Anything enforced in code should
be **absent** from the prompt, not summarised in it: the enforcement
already carries the explanation, and the summary costs attention that the
rules with no mechanism behind them cannot spare.

Three separate bugs in one day — this, #13, and the original #1 — were the
same failure: an instruction added for good reasons made the agent follow a
script instead of making a sale. In two of the three, the instruction was a
*safety* feature, which is what makes this class hard. Safety text is the
text nobody deletes.

And both causes here were invisible to the test suite. Every test passed
throughout. What caught them was a harness that runs the real agent over
real cases and counts money — `recovered: 0` is a sentence no unit test in
this repository was in a position to say.

---

## 15. #1 was never actually fixed, and the scoreboard had been saying so

The very first entry in this document describes a customer asking twice for
a checkout link and being interrogated instead. The fix was a rule added to
the system prompt: **THE SALE COMES FIRST**, plus *"Never ask a second time
why they didn't complete the order."*

Running the scoreboard case that exercises exactly that scenario:

```
Customer: "I still want it, just send me the checkout link."
Agent:    "Can I ask what stopped you from finishing?"
Customer: "Yes."
Agent:    "Could you share what held you back from completing the order?"
[no tool calls at all]                              outcome: NO SALE
```

That is #1, unchanged, including the second ask that the prompt explicitly
forbids. The rule had been in the prompt for weeks. It had never worked.

**The scoreboard had been reporting it the whole time.** The case is named
"Ready to buy, asks for link immediately", it carries an expected outcome of
`RECOVERED_FULL_PRICE`, and it had been coming back `NO_SALE`. It was one
line in a list of mismatches under a headline number that read fine, so
nobody read it. A scoreboard that prints a failure nobody looks at is
telling the truth to an empty room.

### Why the prompt rule lost

Three reasons, and the third is the one that generalises.

The rule sat roughly 4,000 characters below the numbered opening sequence it
was supposed to override — and that sequence was imperative ("work through
this sequence, ONE step per turn"), specific, and came first. Distance from
the thing being overridden turned out to matter more than emphasis: the rule
was in capitals and still lost.

The sequence's step 1 also carried a verbatim example line — *"Hello, can
you hear me alright?"* — and the model copied it literally, spending a turn
of a two-turn conversation on a greeting.

And the agent did not know it had already greeted. The opening line is
generated before dialling and played by Twilio, but nothing ever put it in
the agent's message thread, so the model reasonably assumed it was speaking
first. **On every real call, customers have been introduced to twice.**

### What actually fixed it

Three changes, all of them moving or deleting text rather than adding it:

- The opening line is now seeded into the agent's thread, so the model knows
  what it has already said.
- **THE SALE COMES FIRST** was hoisted above the sequence instead of below.
- The exception was written **onto step 3 itself** — the step that was
  misfiring — rather than stated once, far away, as a general principle.

Only the third made it reliable. Hoisting the rule fixed three of four
failing cases; putting the exception on the misbehaving step fixed the
fourth, and did so on three consecutive runs.

And then that fix broke something of its own, which is worth keeping because
it is the same mistake one level down. The exception first read *"skip
straight to step 5"* — and step 5 is "ask whether to send it", while step 4
is where `check_offer` mints the token. So the agent obediently skipped the
tool call that produces the thing `issue_offer` requires, called
`issue_offer` with no token, and failed. Two scoreboard cases went from
passing to failing on that one phrase.

Referring to a step by NUMBER rather than by what it does is the same
category of error as putting a rule far from what it governs: it assumes the
model is holding a map of the document, when all it is really doing is
following the nearest concrete instruction. The exception now names the
actions — "call check_offer with requested_discount_percent=0, then
issue_offer with the token it gives you" — and all three cases pass on
repeated runs.

**The lesson: an instruction competes with the instruction it is nearest
to.** A general rule stated far from the specific step it contradicts will
lose to that step, no matter how emphatic it is, because the model is
reading the step when it acts. If a rule has an exception, the exception
belongs on the rule — not in a section of its own.

This is the fourth time this project has hit the same wall (#1, #13, #14 and
now this), and the conclusion is consistent every time: **anything that must
hold should be a refusal a tool returns, not a sentence in a prompt.** Where
that genuinely is not possible — and "recognise that this person wants to
buy" is one of those places, because detecting it in code means keyword
matching, which this codebase deleted once already — the fallback is not a
stronger sentence. It is a sentence placed where the model is looking.

---

## 16. Two bugs the deployed logs showed, and neither was the one reported

A production log was handed over with the description "calls failing". Every
recovery attempt in it ended `CALL_FAILED`. Nothing was failing.

```
15:06:45 UTC  =  20:36 IST
Orchestrator halting rec_c2a05251755a: STOP_QUIET_HOURS
```

The merchant's calling window closes at 20:00 and the system correctly
refused to telephone someone at half past eight at night. It then recorded
that refusal as `CALL_FAILED` — the same state a broken phone number
produces. Three consequences, in ascending order of seriousness:

- The dashboard told the merchant these were *"real dial failures (no phone
  on file, carrier issue)"*. A false statement to a merchant about their own
  system.
- `CALL_FAILED` sits in `CONTACTED_STATES`, so every refusal burned the
  customer's contact cap for a call that never happened — precisely what
  that counter's own docstring says must not happen.
- A working guardrail looked like a broken integration, which is why it was
  reported as a bug at all. **A compliance success that reads as a failure
  will eventually be "fixed" by someone.** That is the real cost.

`BLOCKED` is now its own terminal state: out of contact counting, reported
apart from dial failures, coloured neutral rather than red. Terminal on
purpose — the case becomes eligible again the moment the reason expires,
which is the difference between stopping and failing.

### The bug underneath it

The same logs showed one abandoned cart producing two of everything:

```
checkout.started         order_TVd3XEkPFeE9VI        (SDK, on the storefront)
checkout.payment_failed  chk_wh_pay_TVd3g0Qckljb1S   (webhook, seconds later)
→ rec_c2a05251755a  and  rec_27bc2087e23e
```

The SDK records a cart under the Razorpay order id well before anything
fails. The webhook then arrives carrying that same order id, consulted only
`notes.checkout_id`, found nothing, and opened a second checkout beside the
first. Every SDK-integrated merchant was getting **two recovery attempts,
and would have got two phone calls, for one order** — with revenue at risk
double-counted to match.

It resolves the order id against existing checkouts now, on both the
`checkout_id` and `rzp_order_id` columns, because which one holds it depends
on how the merchant integrated and a customer should not be called twice
over that detail. Verified by disabling the lookup and watching the
duplicate reappear.

### And a third, which is the interesting one

Every generated opening line in those logs was discarded:

```
RecoveryStrategist: model returned an opening line containing a placeholder
("Hi Dhruv, this is [Your Name] from Loomwork...")  - discarding it
```

Four generations, four placeholders. The guard held every time, so no
customer heard brackets read aloud — but the personalised opening was
therefore never used, and every call fell back to a generic line. A feature
failing 100% of the time, silently, behind a guard that was working.

The prompt already forbade it, in these words:

> NEVER output a placeholder, bracket, or template slot such as
> `[Your Name]`, `[Your Company]`, `{name}` or XYZ

**The prohibition was the cause.** The model had no personal name, wanted to
introduce itself, and the only concrete example of how to name the unknown
thing was sitting right there in its own instructions. Telling it never to
say `[Your Name]` is how `[Your Name]` got into the context.

The fix removes the forbidden strings entirely and specifies the *shape* of
the sentence instead — refer to yourself as the organisation, never as a
named individual, plus one example of a good line. Measured over six
consecutive generations: **6 of 6 usable, against 0 of 4 before.**

This is #14 and #15 again from a third angle. There, a rule lost because it
sat far from the instruction it contradicted. Here, a rule lost because
stating it required naming the exact thing it prohibited. Both are the same
underlying fact: **a prompt is not a rulebook the model consults, it is
context the model completes.** Anything phrased as "never produce X" has
put X in front of the model, and the reliable move is to describe what to
produce instead.

---

## 17. Every budget in this system was fiction, because the database was 8,000 km away

The call agent worked and then died, always on the same turn. Turns where it
only talks answered in 2.0–2.3s. The turn where the customer says *"yes, send
it"* blew the 11s ceiling every time. The production log dates the pieces:

```
+0.00  customer speech in
+1.47  LLM #1 returns
+4.91  PolicyEngine evaluates       <- check_offer's DB work  = 3.44s
+6.90  agent.tool_called published  <- ONE audit write        = 1.99s
+7.60  LLM #2 returns
+11.0  CUT OFF, issue_offer still running
```

Two seconds to write one audit row is not a query. Nothing about that row is
expensive. It is `INSERT` plus a redundant `SELECT` — two round trips — and
each round trip was costing about a second.

Railway was deployed in `ams`, Amsterdam. Supabase is `ap-northeast-2`,
Seoul. Roughly 8,600 km, badly routed, on every single database call, and the
money turn makes about twelve of them in sequence.

The evidence had been sitting in this repo for weeks, already written down
and already misread. `policies.py` carries a comment recording a single-row
indexed SELECT measured at **2365ms and 2795ms**, which is why a 60-second
policy cache exists at all. Finding #9 records three *concurrent* reads
timing **80% slower** than three sequential ones — concurrency forced new
connections, and establishing one cost more than the overlap saved. Both were
treated as facts about Supabase to be worked around. Both were the same fact
about geography, and it was fixable in a dropdown.

**Every latency constant in the codebase had been tuned against this.**
`VOICE_DEADLINE_S` was cut 9.0 → 7.5 after a live call died. The tool
descriptions were rewritten to stop the model making a second DB-backed call.
The system prompt was trimmed from 1048 characters to 391. Each of those was
a real improvement and each was reasoned about carefully, and together they
were an elaborate accommodation of a misconfiguration. **Tuning is what you
do when you have accepted a number you should have questioned.**

### The shield was killing the thing it protected

Finding #2 is about `issue_offer` being cancelled mid-flight, leaving a real
payment link created and unrecorded while the customer was told someone would
follow up. The fix was to shield money actions from the deadline and wait for
them. It read as careful. It was:

```python
await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=grace_s)
```

`asyncio.gather` propagates cancellation to its children. When `wait_for`
timed out it cancelled the gather, which cancelled the shielded `issue_offer`
tasks — and the handler then logged *"did not settle within 6.0s"* about
tasks it had just killed itself. **The code written to prevent the
half-applied effect was the code producing it,** and its log line described
the symptom while hiding the cause.

Worse, the tasks were only referenced by a list owned by the `run_agent`
frame. asyncio holds tasks *weakly*. When the outer 11s timeout cancelled
that frame, an in-flight Razorpay call could simply be garbage collected —
the same class of bug `_spawn_write` had already been invented to fix
elsewhere in the codebase, on the write that records that a payment link was
sent.

`asyncio.wait` does not cancel on timeout. The grace now ends the *waiting*,
never the work; a module-level set holds the tasks so nothing can collect
them. Both are covered by tests that fail against the old code — verified by
restoring it.

### The server was never crashing

*"When the policy engine starts, the Twilio server stops"* was reported
repeatedly and read as a crash. There was no crash. The razorpay SDK is
synchronous `requests`, and it was being called directly from a coroutine —
so it did not merely block that call, it froze the whole event loop, and
every other live call's Twilio webhook went unanswered for its duration.
`record_audit` was doing the same thing for two round trips per tool call.
**A frozen event loop and a dead process are indistinguishable from
outside,** and we spent weeks debugging the wrong one.

### And a round trip nobody was buying anything with

`voice_runtime` already prefers its own wording after `issue_offer` and
`record_opt_out`, because what is said once money has moved has to be true
about what moved. But the runtime still routed back to the model for a
closing sentence after every tool call — a full round trip whose output the
caller had always intended to discard. On the turn that blew the budget, it
was one of the two model calls in it.

The lesson is the one this project keeps relearning from the other side.
Findings #14 and #15 are about guarantees that lived in a prompt and
therefore did not hold. This is about *budgets* that lived in constants and
therefore did not hold: 7.5s reasoning + 6.0s settle + 2.0s speech = 15.5s,
wrapped in an 11.0s timeout, inside Twilio's 15.0s. Five numbers, each with a
careful comment explaining it, that could not all be true at once. Nothing
was checking. `tests/test_turn_budget.py` checks now — **arithmetic a comment
asserts is arithmetic nobody has done.**

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
| No money tool ran on a turn we may have misheard | **No** |
| A broken payment was never discounted before full price was offered | **No** |

The agent's *conversation* varies. What it is *allowed to do* does not, because
the stops are enforced in code rather than by the model. A hallucinating or
prompt-injected model can only hand back an opaque token whose amounts were
computed by code it never touched.

The last two rows moved into that column recently, and both moved the same
way: they were true beliefs expressed as sentences in a system prompt, and
they became guarantees when they became refusals a tool returns. "Be careful
if you might have misheard" and "a declined card is not a price objection"
are both correct, and neither was enforced by anything until a tool started
saying no.

Across 25 labelled cases, re-measured after the work in #10 through #15:

| | |
|---|---|
| **Rule breaks** | **0** |
| Refusal cases held before any contact | **10 of 10** |
| Discount requested vs approved | **115 points asked, 20 approved** (ceiling 10%) |
| Cases matching their expected outcome | **24 of 25** |
| Recovered | 9 cases — 7 at full price, 2 discounted |
| Money | ₹15,861 recovered of ₹49,875 at risk |
| Denominator check | contacted 15 + stopped 10 = 25 eligible |

That last row exists because a recovery rate which hides its stops is a lie:
if ten customers were deliberately not contacted, a rate quoted over
"attempts" has quietly shrunk its own denominator and reads better for having
refused more people. The batch prints the arithmetic so anyone can check it.

**One case of the 25 does not match its expected outcome, and it is named
here rather than rounded away.** In "price objection, recovers at capped
rate" the customer says the item *"felt a bit pricey"* and the agent sends a
full-price link instead of the capped discount. That is the overshoot #1
already warns about, in the direction #1 identified: having been taught not
to discount a won sale, the agent under-discounts a real price objection.

Two things about it are worth stating plainly. It **costs a recovery, not
margin or compliance** — of the two directions this error can take, it is
the cheaper one, and no guarantee is involved. And attempts to fix it by
further prompt editing simply moved the failure to a different case: each
edit bought one scenario and sold another, which is #14's lesson arriving as
a live experience rather than a maxim. The configuration here is the best
one measured, and the remaining miss is left visible instead of tuned into
somebody else's column.

Nothing here is quoted from a run that predates the code it describes. The
previous figures were withdrawn the moment that stopped being true of them,
and re-measured only after the work in #15 landed — which is also when the
recovery count moved from 6 to 9, because that entry was a real defect and
not a rounding error.

---

## The guardrails, measured by removing them

"AI proposes, deterministic policy disposes" is the correct architecture and
it is also what every project in this category says. A sentence in a README
is not evidence, so `backend/scripts/run_ablation.py` runs the experiment
instead: the same model, the same prompt, the same tools, the same 25 cases,
with one layer removed at a time.

| Arm | Rule breaks | Contacted past a stop | Discounts over ceiling | Largest discount approved |
|---|---|---|---|---|
| **GATED** (production) | **0** | 0 | 0 | 10% |
| NO_POLICY | 3 | 0 | 3 | **40%** |
| NO_GUARDS | 5 | 5 | 0 | 10% |

`NO_POLICY` makes the policy engine approve whatever the model asks for. The
two-phase offer token still exists; it simply stops being a gate. `NO_GUARDS`
makes every pre-dial hard stop return "allowed".

Unguarded, the agent approved **40% off against a configured ceiling of 10%**
— a discount that is not merely unlikely while gated but arithmetically
impossible, because the number is computed by code the model never touches.
Without the pre-dial stops, five customers were contacted after a hard stop
said no: three who had already paid, one outside their merchant's calling
hours, one already at the contact cap.

**The part that argues against us, and belongs here for that reason.**
`NO_POLICY` recovered **seven** cases; `GATED` recovered **six**. Removing
the guardrails bought one extra sale. It also gave away roughly ₹1,400 more
margin on this run and broke three rules. That is the actual trade, stated
in the direction that is inconvenient: the gate costs a sale at the edges,
and what it buys is that the worst case cannot happen. A merchant can decide
whether that is worth it; they cannot decide it if we only publish the half
that flatters us.

**What varies and what does not.** The rupee figures move between runs,
because the agent runs at live temperature and its own sampling moves them
more than the ablation does — one earlier run had the ungated arm asking for
*less* than the gated one purely by chance. The report prints those under a
`SAMPLED` heading and labels a negative delta as noise rather than a result.
The rule-break column does not vary, because it is enforced in code rather
than drawn from a distribution. That distinction is the whole claim: the
difference between "usually fine" and "cannot happen".

The harness has also paid for itself twice over as a debugging tool rather
than a demo — see #13 and #14, both of which are bugs it found in gated
production code that the entire test suite was happy with.

---

