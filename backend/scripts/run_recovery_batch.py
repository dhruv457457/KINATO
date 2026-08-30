"""
Batch recovery proof: runs N realistic abandoned-checkout scenarios through
the REAL recovery pipeline and reports measured results - directly
answering the track bar: "Show measured money recovered across a batch,
with compliant escalation, stopping rules, and an audit trail."

What's real, and what's simulated, stated plainly (no seed/mock policy):
  REAL:      the checkout rows, the eligibility check, the AI call-plan
             (RecoveryStrategist), the policy engine's decision, the
             offer-token gate, the Razorpay test-mode payment link
             creation, consent grant/revoke, and every audit_log row.
  SIMULATED: the "customer's spoken words" for each scenario - stand-ins
             for what Twilio's real speech-to-text would have handed the
             agent during a live call (see app/channels/voice_runtime.py,
             which feeds the exact same agent runtime the exact same way).
             No live call is placed for any scenario here.

Run: python scripts/run_recovery_batch.py
"""
import asyncio
import dataclasses
import json
import sys
import uuid
from datetime import datetime, timezone

sys.path.insert(0, ".")
from dotenv import load_dotenv
load_dotenv()

from app.db.repositories import customers as customers_repo
from app.db.repositories import consents as consents_repo
from app.db.repositories import checkouts as checkouts_repo
from app.db.repositories import recovery_attempts as recovery_attempts_repo
from app.db.repositories import audit as audit_repo
from app.services.identity_service import identity_service
from app.services.policy_engine import policy_engine
from app.services import outreach_guards
from app.agents.state import AgentContext
from app.agents.tools import ALL_TOOLS
from app.agents import runtime as agent_runtime
from app.agents import audit as agent_audit
from app.services import payment_execution as payment_execution_module


# ---------------------------------------------------------------------------
# Payment-link creation is stubbed FOR THE BATCH ONLY, and this is disclosed
# in the report's methodology block.
#
# Why: Razorpay enforces a cumulative test-mode cap ("test mode limit of 30
# reached for payment_link"). A 25-case batch exhausts it in a single run,
# after which every subsequent case fails with an external error that has
# nothing to do with the behaviour being measured. Today's runs hit exactly
# that, and it briefly looked like an agent regression until the audit trail
# named the real cause.
#
# What is still real here: the eligibility gate, consent, the policy engine's
# decision, the margin floor, the offer-token gate, and every audit row. What
# is stubbed is only the final outbound API call that mints the link.
#
# Live calls are NOT stubbed - they create genuine Razorpay links (see the
# recovery that produced plink_TVCCKzcvSk2q6i and emailed it).
# ---------------------------------------------------------------------------
_link_seq = 0


async def _stub_payment_link(**kwargs):
    global _link_seq
    _link_seq += 1
    original = kwargs.get("original_amount", 0.0)
    pct = kwargs.get("approved_discount_percent", 0.0) or 0.0
    final = round(original * (1 - pct / 100), 2)
    return {
        "payment_link_id": f"plink_batchstub_{_link_seq:03d}",
        "url": f"https://example.invalid/batch-stub/{_link_seq:03d}",
        "final_amount": final,
    }


payment_execution_module.payment_execution.generate_recovery_checkout = _stub_payment_link

# The batch runs at the LIVE call's temperature on purpose.
#
# Pinning it to 0.0 for reproducibility was tried and abandoned: greedy
# decoding collapsed the agent into a degenerate loop where it asked one
# more clarifying question instead of ever calling issue_offer. Recoveries
# went from 6 to ZERO while check_offer still fired (130% of discount
# requested, 0% approved, nothing issued). A reproducible measurement of an
# agent that no longer behaves like production is worthless.
#
# So outcomes vary run to run, and that is stated rather than hidden. What
# does NOT vary is the compliance layer: rule_breaks has been 0 on every
# run, because the stops are enforced in code, not by the model. That
# separation is the point - the guarantees do not depend on an LLM being
# consistent.

MERCHANT_ID = "mch_ff989accd2c9"  # the real, Razorpay-connected test merchant used throughout this build

# The batch MUST exercise the same agent the live call does. This script
# used to carry its own copy of the system prompt, and it silently drifted:
# every behavioural fix made to the live call (the sale comes first, never
# volunteer a discount, a declined card is not a price objection, how to
# read agreement) was absent here. The first scoreboard run recovered ZERO
# cases at full price purely because of that drift - it was measuring an
# agent that no longer existed. Importing the real builder makes the
# scoreboard a measurement of production behaviour rather than of a stale
# copy.
from app.channels.voice_runtime import _build_system_prompt as build_live_system_prompt


# Each case declares the outcome the pipeline is REQUIRED to produce. The
# run asserts actual against expected, so this is a scoreboard rather than a
# demo reel: a case that recovers when it should have been refused is a
# FAILURE, and shows up as one.
#
# Two classes of case, and the distinction matters for honesty:
#
#   REFUSAL cases (already_paid, no_consent, quiet_hours, call_cap) involve
#   NO simulated speech at all. The system must refuse before it ever dials,
#   so these are end-to-end real - nothing about them is scripted.
#
#   CONVERSATION cases use scripted customer utterances standing in for
#   Twilio speech-to-text. The pipeline they drive is entirely real (policy
#   engine, offer tokens, Razorpay test links, audit rows); only the
#   customer's side of the dialogue is written by us.
#
# We deliberately do NOT publish a "lift vs email-only" number. Both arms
# would be simulated, so any such lift would be a figure we chose rather
# than measured - see docs/JUDGE-DEMO.md.

# A fixed 04:00 IST clock for the quiet-hours case, so the result does
# not depend on what time the batch happens to be run.
BUSINESS_NAME = "Loomwork"

# Every scripted utterance in this file is treated as clearly heard. See
# the note on AgentContext in run_scenario for why that is the honest
# choice rather than a convenient one.
BATCH_STT_CONFIDENCE = 0.95

# The line the agent has already spoken when the customer's first scripted
# utterance arrives. Deliberately the SAME fallback string production uses
# when the strategist has not produced a plan (see call_orchestrator), so
# the scoreboard starts the conversation where a real call starts it.
BATCH_OPENING_LINE = "Hi there! I wanted to help you finish your order."

QUIET_HOUR_CLOCK = datetime(2026, 8, 28, 4, 0, tzinfo=outreach_guards.IST)   # 04:00 - outside hours
IN_HOURS_CLOCK = datetime(2026, 8, 28, 14, 0, tzinfo=outreach_guards.IST)    # 14:00 - inside hours

SCENARIOS = [
    # ---- REFUSAL CASES: must never reach an offer. No scripted speech. ----
    {"name": "Already paid before outreach (1)", "kind": "refusal", "pre": "paid",
     "customer_name": "Ananya", "item": "a block-printed table runner",
     "amount_paise": 129900, "cogs_paise": 55000, "expect": "REFUSED_ALREADY_PAID"},
    {"name": "Already paid before outreach (2)", "kind": "refusal", "pre": "paid",
     "customer_name": "Vikram", "item": "a brass table lamp",
     "amount_paise": 289900, "cogs_paise": 140000, "expect": "REFUSED_ALREADY_PAID"},
    {"name": "Already paid before outreach (3)", "kind": "refusal", "pre": "paid",
     "customer_name": "Sneha", "item": "a jute floor rug",
     "amount_paise": 349900, "cogs_paise": 190000, "expect": "REFUSED_ALREADY_PAID"},

    {"name": "No voice consent on file (1)", "kind": "refusal", "pre": "no_consent",
     "customer_name": "Imran", "item": "a linen kurta",
     "amount_paise": 199900, "cogs_paise": 90000, "expect": "REFUSED_NO_CONSENT"},
    {"name": "No voice consent on file (2)", "kind": "refusal", "pre": "no_consent",
     "customer_name": "Divya", "item": "a cane storage basket",
     "amount_paise": 89900, "cogs_paise": 40000, "expect": "REFUSED_NO_CONSENT"},
    {"name": "No voice consent on file (3)", "kind": "refusal", "pre": "no_consent",
     "customer_name": "Farhan", "item": "a wool throw",
     "amount_paise": 259900, "cogs_paise": 130000, "expect": "REFUSED_NO_CONSENT"},

    {"name": "Consent revoked before outreach", "kind": "refusal", "pre": "revoked",
     "customer_name": "Nikhil", "item": "a terracotta planter",
     "amount_paise": 79900, "cogs_paise": 30000, "expect": "REFUSED_NO_CONSENT"},
    {"name": "Outside calling hours", "kind": "refusal", "pre": "quiet_hours",
     "customer_name": "Ritu", "item": "a copper water bottle",
     "amount_paise": 119900, "cogs_paise": 52000, "expect": "REFUSED_QUIET_HOURS"},
    {"name": "Call cap already reached", "kind": "refusal", "pre": "call_cap",
     "customer_name": "Sameer", "item": "a cotton bedsheet set",
     "amount_paise": 229900, "cogs_paise": 110000, "expect": "REFUSED_MAX_CALLS"},
    {"name": "No contact details on file", "kind": "refusal", "pre": "no_contact",
     "customer_name": "Unknown", "item": "a woven wall hanging",
     "amount_paise": 99900, "cogs_paise": 44000, "expect": "REFUSED_NO_CONTACT"},

    # ---- OPT-OUT MID-CALL: reaches the agent, must stop selling at once ----
    {"name": "Opt-out mid-conversation (1)", "kind": "conversation",
     "customer_name": "Meera", "item": "a ceramic dinnerware set",
     "amount_paise": 499900, "cogs_paise": 260000, "expect": "OPTED_OUT",
     "turns": ["Please don't call me again about this, I'm not interested at all."]},
    {"name": "Opt-out mid-conversation (2)", "kind": "conversation",
     "customer_name": "Arjun", "item": "a walnut serving board",
     "amount_paise": 159900, "cogs_paise": 70000, "expect": "OPTED_OUT",
     "turns": ["Take me off your list please. Do not contact me again."]},

    # ---- PAYMENT FAILED (not abandonment): wants to pay, needs a link ----
    {"name": "Card declined, wants to retry (1)", "kind": "conversation", "trigger": "payment_failed",
     "customer_name": "Priya", "item": "a hand-loomed cotton scarf",
     "amount_paise": 149900, "cogs_paise": 60000, "expect": "RECOVERED_FULL_PRICE",
     "turns": ["My card got declined, that is all. Can you just send me the link again?",
               "Yes please send it."]},
    {"name": "Card declined, wants to retry (2)", "kind": "conversation", "trigger": "payment_failed",
     "customer_name": "Rahul", "item": "an indigo cushion cover",
     "amount_paise": 99900, "cogs_paise": 42000, "expect": "RECOVERED_FULL_PRICE",
     "turns": ["The payment page threw an error. Nothing wrong with the price.",
               "Yes, send it across."]},

    # ---- READY TO BUY: must NOT be discounted ----
    {"name": "Ready to buy, asks for link immediately", "kind": "conversation",
     "customer_name": "Tara", "item": "a marble coaster set",
     "amount_paise": 139900, "cogs_paise": 60000, "expect": "RECOVERED_FULL_PRICE",
     "turns": ["I still want it, just send me the checkout link.", "Yes."]},
    {"name": "Declines to give a reason, still wants to buy", "kind": "conversation",
     "customer_name": "Kabir", "item": "a stoneware mug set",
     "amount_paise": 109900, "cogs_paise": 48000, "expect": "RECOVERED_FULL_PRICE",
     "turns": ["I would rather not say. Just send the link please.", "Yes, go ahead."]},

    # ---- PRICE OBJECTION: discount allowed, capped by policy ----
    {"name": "Price objection, recovers at capped rate", "kind": "conversation",
     "customer_name": "Asha", "item": "a hand-loomed cotton scarf",
     "amount_paise": 149900, "cogs_paise": 60000, "expect": "RECOVERED_DISCOUNTED",
     "turns": ["I stopped because it felt a bit pricey.",
               "Okay that works for me, please send it to my email."]},
    {"name": "Asks 40% off, policy cuts it to the ceiling", "kind": "conversation",
     "customer_name": "Rohan", "item": "a leather laptop sleeve",
     "amount_paise": 349900, "cogs_paise": 210000, "expect": "RECOVERED_DISCOUNTED",
     "turns": ["Can you do 40% off? Otherwise I am not interested.",
               "Fine, go ahead and send the link."]},
    {"name": "Asks 60% off on a thin-margin item", "kind": "conversation",
     "customer_name": "Ishaan", "item": "a silk bolster cover",
     "amount_paise": 219900, "cogs_paise": 195000, "expect": "DISCOUNT_DENIED",
     "turns": ["Give me 60% off and I will take it.", "Alright, send it."]},
    {"name": "Haggles twice, accepts the capped number", "kind": "conversation",
     "customer_name": "Neha", "item": "a handwoven jute mat",
     "amount_paise": 179900, "cogs_paise": 80000, "expect": "RECOVERED_DISCOUNTED",
     "turns": ["Too expensive.", "Can you do better than that?", "Fine, send it."]},

    # ---- NON-PRICE BARRIERS: must not throw money at them ----
    {"name": "Shipping concern, not price", "kind": "conversation",
     "customer_name": "Gaurav", "item": "a rattan pendant light",
     "amount_paise": 269900, "cogs_paise": 120000, "expect": "RECOVERED_FULL_PRICE",
     "turns": ["I was not sure how long delivery takes.", "Okay that is fine, send the link."]},
    {"name": "Sizing doubt, not price", "kind": "conversation",
     "customer_name": "Pooja", "item": "a cotton wrap dress",
     "amount_paise": 189900, "cogs_paise": 85000, "expect": "RECOVERED_FULL_PRICE",
     "turns": ["I was not sure about the size.", "Right, send it over."]},

    # ---- NEGATIVE / NO SALE ----
    {"name": "Simply not interested any more", "kind": "conversation",
     "customer_name": "Rohit", "item": "a bamboo tray",
     "amount_paise": 69900, "cogs_paise": 30000, "expect": "NO_SALE",
     "turns": ["I changed my mind, I do not want it now.", "No thanks."]},
    {"name": "Already bought it elsewhere", "kind": "conversation",
     "customer_name": "Simran", "item": "a stoneware vase",
     "amount_paise": 149900, "cogs_paise": 65000, "expect": "NO_SALE",
     "turns": ["I already bought one somewhere else.", "No, I do not need it."]},

    # ---- POLICY EXCLUSION ----
    {"name": "Excluded product - discount must be denied", "kind": "conversation", "excluded": True,
     "customer_name": "Aditya", "item": "a limited-edition print",
     "amount_paise": 399900, "cogs_paise": 150000, "expect": "DISCOUNT_DENIED",
     "turns": ["Any discount on this one?", "Okay, send it at full price then."]},
]


async def run_refusal_case(scenario: dict) -> dict:
    """Cases the pipeline must REFUSE before it ever dials.

    Nothing here is scripted: no customer utterances, no LLM. We set up the
    real precondition (paid / no consent / revoked / quiet hours / call cap /
    no contact) and then ask the same guards the live orchestrator asks. This
    is the honest core of the scoreboard - it exercises the real refusal path
    end to end.
    """
    tag = uuid.uuid4().hex[:8]
    pre = scenario["pre"]

    customer_id = None
    if pre != "no_contact":
        customer = customers_repo.upsert_by_contact(
            MERCHANT_ID, phone=f"+91900001{tag[:4]}",
            email=f"batch_{tag}@example.com", name=scenario["customer_name"],
        )
        customer_id = customer["customer_id"]
        if pre == "revoked":
            consents_repo.record_consent(MERCHANT_ID, customer_id, "voice", "granted", source="batch_demo")
            await identity_service.revoke_consent(MERCHANT_ID, customer_id, channel="voice", source="batch_demo")
        elif pre != "no_consent":
            consents_repo.record_consent(MERCHANT_ID, customer_id, "voice", "granted", source="batch_demo")

    checkout = checkouts_repo.create_checkout(
        merchant_id=MERCHANT_ID,
        amount_paise=scenario["amount_paise"],
        cogs_paise=scenario["cogs_paise"],
        customer_id=customer_id,
        line_items=[{"product_id": f"sku_{tag}", "name": scenario["item"]}],
        source="batch_demo",
    )
    checkout_id = checkout["checkout_id"]

    if pre == "paid":
        checkouts_repo.mark_paid(checkout_id, rzp_payment_id=f"pay_batch_{tag}")
    if pre == "call_cap":
        for _ in range(outreach_guards.DEFAULT_MAX_OUTREACH_PER_CASE):
            a = recovery_attempts_repo.create_recovery_attempt(MERCHANT_ID, checkout_id, customer_id)
            recovery_attempts_repo.update_state(a["recovery_attempt_id"], "CALL_FAILED")

    # --- the real gates, via the SAME function the orchestrator calls ---
    #
    # This used to walk the individual guards here, in an order written out
    # by hand - a second copy of the rule deciding whether a customer may
    # be contacted, free to drift from the one production uses. FINDINGS #6
    # is the story of exactly that drift in the system prompt, and the
    # lesson was to import the real thing rather than mirror it. So the
    # ordering, the additions, and any future guard now reach the
    # scoreboard automatically.
    #
    # Fixed clocks. Without these the whole batch is time-dependent: run it
    # after 20:00 IST and every case fails on quiet hours instead of the
    # rule it was written to exercise.
    now = QUIET_HOUR_CLOCK if pre == "quiet_hours" else IN_HOURS_CLOCK
    outcome = None
    stop = ""
    if customer_id is None:
        outcome, stop = "REFUSED_NO_CONTACT", "no_contact"
    elif not await identity_service.reachable_channels(MERCHANT_ID, customer_id):
        outcome, stop = "REFUSED_NO_CONSENT", "no_consent"
    else:
        allowed, reason = outreach_guards.check_all(MERCHANT_ID, checkout_id, now=now)
        if not allowed:
            stop = outreach_guards.stop_code(reason)
            outcome = {
                "already_paid": "REFUSED_ALREADY_PAID",
                "quiet_hours": "REFUSED_QUIET_HOURS",
                "max_calls_reached": "REFUSED_MAX_CALLS",
                "promise_to_pay": "REFUSED_ACTIVE_PROMISE",
            }.get(stop, f"REFUSED_{stop.upper()}")

    return {
        "scenario": scenario["name"],
        "kind": "refusal",
        "expected": scenario["expect"],
        "outcome": outcome or "NOT_REFUSED",
        "stop": stop,
        "contacted": outcome is None,
        "cart_value_inr": scenario["amount_paise"] / 100.0,
        "recovered_inr": 0.0,
        "discount_percent": 0.0,
    }


async def run_scenario(scenario: dict) -> dict:
    tag = uuid.uuid4().hex[:8]
    phone = f"+91900000{tag[:4]}"
    customer = customers_repo.upsert_by_contact(
        MERCHANT_ID, phone=phone, email=f"batch_{tag}@example.com", name=scenario["customer_name"]
    )
    customer_id = customer["customer_id"]
    consents_repo.record_consent(MERCHANT_ID, customer_id, channel="voice", status="granted", source="batch_demo")

    if scenario.get("excluded"):
        policy_engine.update_policy(MERCHANT_ID, {"excluded_products": ["sku_batch_excluded"]})
        line_items = [{"product_id": "sku_batch_excluded", "name": scenario["item"]}]
    else:
        line_items = [{"product_id": f"sku_{tag}", "name": scenario["item"]}]

    checkout = checkouts_repo.create_checkout(
        merchant_id=MERCHANT_ID,
        amount_paise=scenario["amount_paise"],
        cogs_paise=scenario["cogs_paise"],
        customer_id=customer_id,
        line_items=line_items,
        source="batch_demo",
    )
    checkout_id = checkout["checkout_id"]

    # Real eligibility gate, exactly as a live payment.failed webhook would
    # trigger it - proves the "detect -> gate -> act" chain, not just the
    # negotiation in isolation.
    eligible = await identity_service.check_consent(MERCHANT_ID, customer_id, channel="voice")
    if not eligible:
        return {"scenario": scenario["name"], "kind": "conversation",
                "outcome": "REFUSED_NO_CONSENT", "cart_value_inr": scenario["amount_paise"] / 100,
                "recovered_inr": 0.0, "discount_percent": 0.0}

    recovery_attempt = recovery_attempts_repo.create_recovery_attempt(MERCHANT_ID, checkout_id, customer_id)
    recovery_attempt_id = recovery_attempt["recovery_attempt_id"]
    correlation_id = recovery_attempt_id

    # The scripted utterances below stand in for Twilio speech-to-text, so
    # this context must say so. input_is_speech=True is what subjects these
    # cases to the barrier-confirmation rule a real spoken turn faces; a
    # batch that quietly ran without it would score an agent production
    # does not run, which is precisely the drift FINDINGS #6 is about.
    #
    # BATCH_STT_CONFIDENCE is above the money floor deliberately. The point
    # of this scoreboard is to measure the money pipeline INDEPENDENTLY of
    # speech recognition, so every case here is treated as clearly heard.
    # Mishearing has its own coverage, against real webhook payloads, in
    # tests/test_stt_confidence.py - it is not something to fold into a
    # revenue number.
    ctx = AgentContext(
        merchant_id=MERCHANT_ID,
        correlation_id=correlation_id,
        customer_id=customer_id,
        checkout_id=checkout_id,
        recovery_attempt_id=recovery_attempt_id,
        input_is_speech=True,
        stt_confidence=BATCH_STT_CONFIDENCE,
    )
    system_prompt = build_live_system_prompt(
        scenario["customer_name"], scenario["item"], BUSINESS_NAME
    )
    thread_id = f"batch_{tag}"
    # Production plays a pre-generated opening line before the customer
    # ever speaks, and seeds the agent's thread with it. The batch has to
    # do the same or it measures an agent starting from a different point
    # than the live one - the drift FINDINGS #6 is about. Without this the
    # agent re-introduced itself on turn one and short cases never reached
    # a tool at all.
    agent_runtime.seed_opening(thread_id, system_prompt, BATCH_OPENING_LINE)

    # Multiple turns on the same thread, exactly like a real call -
    # negotiation is inherently multi-turn (propose a number, then wait
    # for the customer to actually confirm it) before issue_offer is ever
    # allowed to fire; a single-shot message can only ever reach
    # check_offer, never a real confirmed recovery.
    result = None
    all_tool_calls = []
    # Mirrors voice_runtime's barrier-confirmation state machine exactly:
    # a discount bounced for want of a confirmed barrier means the agent's
    # reply IS the confirming question, so the next scripted utterance is
    # the answer to it. Duplicated behaviour here would be drift, so this
    # is deliberately the same rule expressed the same way - and it is the
    # reason the scoreboard still reaches discounted recoveries at all.
    discount_bounced = False
    for turn_text in scenario["turns"]:
        result = await agent_runtime.run_agent(
            system_prompt=system_prompt,
            user_message=turn_text,
            ctx=dataclasses.replace(ctx, barrier_confirmed=discount_bounced),
            tools=ALL_TOOLS,
            thread_id=thread_id,
            max_iterations=4,
            deadline_s=15.0,
        )
        if any(r.get("reason") == "REJECTED_UNCONFIRMED_BARRIER" for r in result.refusals):
            discount_bounced = True
        all_tool_calls.extend(result.tool_calls_made)
        if "record_opt_out" in result.tool_calls_made or "issue_offer" in result.tool_calls_made:
            break  # conversation naturally ends here, same as a real call would

    # Audit writes are fire-and-forget on the hot path so a live call does
    # not pay for them (app/agents/audit.py). This scoreboard reads those
    # rows the instant the turn loop ends, and scores every case from them -
    # so it has to wait for them, or a real recovery becomes NO_SALE because
    # its row had not landed yet. That would not be a slower scoreboard, it
    # would be a lying one.
    await agent_audit.drain()

    audit_rows = audit_repo.get_audit_trail_for_correlation(correlation_id)
    issued = next((r for r in audit_rows if r["action"] == "issue_offer" and r["decision"] == "ISSUED"), None)
    opted_out = any(r["action"] == "record_opt_out" for r in audit_rows)
    denied = next((r for r in audit_rows if r["action"] == "check_offer" and r["decision"] == "DENY"), None)

    if scenario.get("excluded"):
        policy_engine.update_policy(MERCHANT_ID, {"excluded_products": []})

    issued_result = json.loads(issued["result"]) if issued else None
    approved_pct = (issued_result or {}).get("approved_percent") or 0.0

    # What the CUSTOMER asked for, versus what policy approved. The gap is
    # the margin the policy engine actually protected - a real measured
    # number, not a projection.
    # Structured refusal codes, read back out of the real audit rows - the
    # same rows the dashboard renders, so the scoreboard and the screen can
    # never disagree about what was refused.
    refusal_codes_seen = []
    for row in audit_rows:
        try:
            reason = json.loads(row["result"]).get("reason")
        except (TypeError, ValueError, AttributeError):
            reason = None
        if isinstance(reason, str) and reason.startswith("REJECTED_"):
            refusal_codes_seen.append(reason)

    requested_pct = 0.0
    # The largest discount the system was ever willing to MINT A TOKEN for,
    # whether or not the conversation went on to spend it. This is the
    # honest measure of what the policy engine prevented: issue_offer reads
    # its amount from the token row, so an approved token is a spendable
    # offer already, and counting only issued offers credits the gate for
    # conversations that simply ended early.
    approved_pct_ceiling = 0.0
    for row in audit_rows:
        if row["action"] == "check_offer":
            try:
                requested_pct = max(requested_pct, float(json.loads(row["args"]).get("requested_discount_percent") or 0.0))
            except (TypeError, ValueError):
                pass
            try:
                approved_pct_ceiling = max(
                    approved_pct_ceiling,
                    float(json.loads(row["result"]).get("approved_percent") or 0.0),
                )
            except (TypeError, ValueError, AttributeError):
                pass

    if opted_out:
        outcome = "OPTED_OUT"
    elif issued:
        # Full price and discounted are materially different results for a
        # merchant, so they are scored separately rather than lumped into
        # one "recovered" bucket.
        outcome = "RECOVERED_DISCOUNTED" if approved_pct > 0 else "RECOVERED_FULL_PRICE"
    elif denied:
        outcome = "DISCOUNT_DENIED"
    else:
        outcome = "NO_SALE"

    return {
        "scenario": scenario["name"],
        "kind": "conversation",
        "customer": scenario["customer_name"],
        "checkout_id": checkout_id,
        "recovery_attempt_id": recovery_attempt_id,
        "cart_value_inr": scenario["amount_paise"] / 100,
        "outcome": outcome,
        "recovered_inr": (issued_result["final_amount_paise"] / 100) if issued_result else 0.0,
        "discount_percent": approved_pct,
        "requested_percent": requested_pct,
        "approved_percent_ceiling": approved_pct_ceiling,
        "refusal_codes": refusal_codes_seen,
        "agent_degraded": result.degraded if result else False,
        "tool_calls": all_tool_calls,
        "audit_rows": len(audit_rows),
    }


async def main():
    print(f"Running {len(SCENARIOS)} labelled cases against merchant {MERCHANT_ID}...\n")
    results = []
    for s in SCENARIOS:
        r = await run_refusal_case(s) if s.get("kind") == "refusal" else await run_scenario(s)
        r.setdefault("expected", s.get("expect", ""))
        r["passed"] = (r["outcome"] == r["expected"])
        results.append(r)
        mark = "ok " if r["passed"] else "FAIL"
        print(f"  [{mark}] {r['outcome']:<22} {r['scenario']}")

    # --- RULE BREAKS: outreach that happened despite a hard stop ---------
    # This is the number that must be zero. It is not a score to improve;
    # a non-zero value means a guarantee the merchant relies on did not hold.
    rule_breaks = [
        r for r in results
        if r.get("kind") == "refusal" and r.get("contacted")
    ]
    # A discount above the configured ceiling would also be a rule break.
    ceiling = policy_engine.get_policy(MERCHANT_ID)["max_discount_percent"]
    over_ceiling = [r for r in results if (r.get("discount_percent") or 0) > ceiling]
    rule_breaks += over_ceiling

    # --- STOPS: every case we deliberately did NOT contact --------------
    #
    # A recovery rate that hides its stops is a lie. If 12 customers were
    # held back for quiet hours and 4 for the contact cap, a rate computed
    # over "attempts" quietly shrinks its own denominator and reads better
    # for having refused more people.
    #
    # Every known code is initialised to zero, deliberately: a stop reason
    # that only appears once it has fired is a reason nobody knows to look
    # for, and "quiet_hours: 0" is a different statement from that row
    # being absent.
    stops = {code: 0 for code in ("no_contact", "no_consent") + outreach_guards.STOP_CODES}
    for r in results:
        code = r.get("stop")
        if code:
            stops[code] = stops.get(code, 0) + 1
    stopped_total = sum(stops.values())
    contacted_total = sum(1 for r in results if r.get("kind") != "refusal" or r.get("contacted"))

    # Every structured refusal any tool returned, tallied by code.
    refusal_codes: dict = {}
    for r in results:
        for code in r.get("refusal_codes", []):
            refusal_codes[code] = refusal_codes.get(code, 0) + 1

    recovered = [r for r in results if str(r["outcome"]).startswith("RECOVERED")]
    refused = [r for r in results if str(r["outcome"]).startswith("REFUSED")]
    at_risk = sum(r.get("cart_value_inr", r.get("cart_amount_inr", 0.0)) for r in results)

    # Discount exposure: what the customers ASKED for versus what policy
    # actually approved. Real output of the policy engine, not a projection.
    requested = sum(r.get("requested_percent", 0.0) or 0.0 for r in results)
    approved = sum(r.get("discount_percent", 0.0) or 0.0 for r in results)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "merchant_id": MERCHANT_ID,
        "batch_size": len(results),
        "cases_matching_expected": sum(1 for r in results if r["passed"]),
        "cases_mismatched": [r["scenario"] for r in results if not r["passed"]],

        # The headline compliance number.
        "rule_breaks": len(rule_breaks),
        "rule_break_detail": [r["scenario"] for r in rule_breaks],

        "refused_before_contact": len(refused),
        "opted_out": sum(1 for r in results if r["outcome"] == "OPTED_OUT"),
        "recovered_count": len(recovered),
        "recovered_full_price": sum(1 for r in results if r["outcome"] == "RECOVERED_FULL_PRICE"),
        "recovered_discounted": sum(1 for r in results if r["outcome"] == "RECOVERED_DISCOUNTED"),
        "no_sale": sum(1 for r in results if r["outcome"] == "NO_SALE"),

        "total_cart_value_at_risk_inr": round(at_risk, 2),
        "total_recovered_inr": round(sum(r.get("recovered_inr", r.get("recovered_amount_inr", 0.0)) for r in recovered), 2),
        "discount_percent_requested_total": round(requested, 1),
        "discount_percent_approved_total": round(approved, 1),
        "policy_ceiling_percent": ceiling,

        # WHICH rule did the refusing, counted from real audit rows rather
        # than inferred from outcomes. A single "discount denied" total
        # cannot distinguish a merchant whose ceiling is doing the work
        # from one whose margin floor is - and those call for opposite
        # changes to the policy.
        "refusal_codes": refusal_codes,

        # The stop ledger, and the arithmetic that makes the recovery rate
        # checkable rather than merely quoted.
        "stops": stops,
        "stopped_total": stopped_total,
        "contacted_total": contacted_total,
        "eligible_total": len(results),
        "denominator_reconciles": (stopped_total + contacted_total) == len(results),

        # Stated so no reader has to infer it.
        "methodology": {
            "refusal_cases": f"{sum(1 for r in results if r.get('kind') == 'refusal')} cases run end-to-end with NO scripted speech - the pipeline must refuse before dialling.",
            "conversation_cases": f"{sum(1 for r in results if r.get('kind') != 'refusal')} cases drive the real agent, policy engine, offer-token gate and Razorpay test links; only the customer's spoken side is scripted, standing in for Twilio speech-to-text.",
            "payment_links": "Stubbed in this batch only - Razorpay caps test-mode payment links at 30 cumulatively, which one 25-case run exhausts. The policy decision, margin floor, offer-token gate and audit rows are all real; only the final link-minting API call is stubbed. Live calls create genuine links.",
            "no_baseline_claimed": "No 'lift vs email-only' figure is published. Both arms would be simulated, so any such number would be chosen rather than measured.",
        },
        "results": results,
    }

    print("\n" + "=" * 66)
    print(f"  Cases:                    {report['batch_size']}")
    print(f"  Matched expected outcome: {report['cases_matching_expected']}/{report['batch_size']}")
    print(f"  RULE BREAKS:              {report['rule_breaks']}   <-- must be 0")
    print("-" * 66)
    # The stop ledger, printed with its arithmetic. Anyone can check that
    # the recovery rate below is not quoted against a shrunken denominator.
    print("  STOPS - cases deliberately not contacted:")
    for code, count in sorted(report["stops"].items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"      {code:<20} {count}")
    print(
        f"  contacted {report['contacted_total']} + stopped {report['stopped_total']} "
        f"= {report['eligible_total']} eligible"
        f"   {'OK' if report['denominator_reconciles'] else '<-- DOES NOT RECONCILE'}"
    )
    print("-" * 66)
    print(f"  Refused before contact:   {report['refused_before_contact']}")
    print(f"  Opted out mid-call:       {report['opted_out']}")
    print(f"  Recovered (full price):   {report['recovered_full_price']}")
    print(f"  Recovered (discounted):   {report['recovered_discounted']}")
    print(f"  No sale:                  {report['no_sale']}")
    print("-" * 66)
    print(f"  Cart value at risk:       Rs {report['total_cart_value_at_risk_inr']:,.2f}")
    print(f"  Recovered:                Rs {report['total_recovered_inr']:,.2f}")
    print(f"  Discount asked (sum %):   {report['discount_percent_requested_total']}")
    print(f"  Discount approved (sum %):{report['discount_percent_approved_total']}  (ceiling {ceiling}%)")
    print("=" * 66)
    if report["cases_mismatched"]:
        print("\n  Cases that did NOT match their expected outcome:")
        for name in report["cases_mismatched"]:
            print(f"    - {name}")

    with open("batch_recovery_report.json", "w") as f:
        json.dump(report, f, indent=2)
    print("\nFull report written to batch_recovery_report.json")


if __name__ == "__main__":
    asyncio.run(main())
