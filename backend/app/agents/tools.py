"""
Tool definitions for the agent runtime. Every tool schema below is checked
at import time (see the assertion loop at the bottom) against
FORBIDDEN_ARG_NAMES - no tool may let the model supply merchant_id,
customer_id, checkout_id, phone, email, or a raw amount. Those always come
from the AgentContext the *caller* built, never from the model's own
tool-call JSON.

check_offer / issue_offer are the two-phase money gate this whole rebuild
hangs on (see the plan's "one architectural idea" section):

  1. check_offer() APPLIES NOTHING. It loads the real cart, the real
     merchant policy, runs the deterministic policy_engine, and returns an
     opaque offer_token whose amounts were computed by code the model never
     touched.
  2. issue_offer() is the only way to *act*, and it can only ever act on
     what the token row says - never on any amount/percent argument the
     model passes alongside it.

A hallucinating or prompt-injected model can still call these tools with
whatever arguments it wants; what it cannot do is make money move by a
different number than what the server-side token says.
"""
import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional

from app.agents.state import AgentContext
from app.db.database import run_db_async
from app.db.repositories import checkouts as checkouts_repo
from app.db.repositories import consents as consents_repo
from app.db.repositories import offer_tokens as offer_tokens_repo
from app.db.repositories import customers as customers_repo
from app.db.repositories import merchants as merchants_repo
from app.db.repositories import recovery_attempts as recovery_attempts_repo
from app.services.failure_diagnosis import FULL_PRICE_FIRST_CLASSES
from app.services.policy_engine import policy_engine
from app.services.payment_execution import payment_execution, PaymentExecutionError
from app.services.identity_service import identity_service
from app.gateway.event_bus import bus

logger = logging.getLogger(__name__)

# Background writes that must not be garbage collected mid-flight.
#
# asyncio holds only a WEAK reference to a running task, so a bare
# `create_task(...)` whose handle nobody keeps can be collected before it
# finishes. voice_runtime learned this the hard way and has its own copy of
# this pattern; importing it here would be circular, so this is the second
# holder rather than a shared one.
_BACKGROUND_WRITES: set = set()


def _spawn_write(coro) -> None:
    task = asyncio.create_task(coro)
    _BACKGROUND_WRITES.add(task)
    task.add_done_callback(_BACKGROUND_WRITES.discard)


# Never accepted as a tool-call argument from the model, in any tool below.
# These come from AgentContext, built by the caller (call_orchestrator /
# voice_runtime / merchant intelligence route), never from model output.
FORBIDDEN_ARG_NAMES = {
    "merchant_id", "customer_id", "checkout_id", "recovery_attempt_id",
    "phone", "email", "amount", "amount_paise", "price", "cogs",
}

# --- The mishearing gate -----------------------------------------------
# Below this Twilio Gather confidence, no money tool may run at all. The
# agent's only remaining move is to ask the customer to repeat themselves.
#
# The reasoning is the same one that produced the offer token. A model that
# is prompted "be careful if you might have misheard" is being asked for a
# promise; a tool that refuses to execute is a mechanism. Speech-to-text is
# the least reliable input in this entire system - accents and line noise
# degrade it badly, and it degrades SILENTLY, returning a confident-looking
# sentence that is simply not what the customer said. Money must not move
# on a sentence we are not reasonably sure we heard.
#
# 0.6 is deliberately conservative for a MONEY action while being no bar at
# all to conversation: the agent still replies, still confirms, still
# closes warmly on a 0.35 turn. It just cannot spend the merchant's margin
# on one.
LOW_CONFIDENCE_FLOOR = 0.6

# Above this, we heard them clearly enough that reading the barrier back
# would be a ritual rather than a check.
#
# The first version of the confirmation rule required it on EVERY spoken
# discount request, and that was wrong in a way worth recording. A customer
# who says "can you do 40% off?" has stated the barrier themselves, in
# their own words, unmistakably. Answering that with "so is it the price
# that's holding you up?" is exactly the scripted interrogation FINDINGS #1
# is about - the script outranking the sale - and on a single-exchange call
# it loses the sale outright, which is how this was caught.
#
# So confirmation is a remedy for MISHEARING, not a negotiation ritual, and
# it applies only in the band where we could plausibly have misheard: above
# the floor where money is allowed to move at all, below the point where
# the transcription is clean.
CLEARLY_HEARD_FLOOR = 0.85

# Every channel a customer is revoked from when they opt out. Listing them
# is deliberate rather than deriving it from "channels with a granted row":
# a customer who says "stop contacting me" must be stopped on channels we
# have not yet tried them on, and on channels added after they said it.
OPT_OUT_CHANNELS = ("voice", "email")


def _say_rupees(paise: Optional[int]) -> str:
    """The amount as a person says it, ready to be read aloud.

    Every rupee figure the agent speaks comes from one of these. The model
    is never asked to convert paise itself: on a live call it turned a
    ₹1,290 cart into "two hundred ninety-nine thousand paise, which is two
    hundred ninety-nine rupees" - a price no customer could act on, said
    with complete confidence.

    Whole rupees when it is whole, which it almost always is, because
    "1,290 rupees" is what a person says and "1,290 rupees and 0 paise" is
    what a computer says.
    """
    if not paise:
        return "0 rupees"
    rupees, remainder = divmod(int(paise), 100)
    if remainder:
        return f"{rupees:,} rupees and {remainder} paise"
    return f"{rupees:,} rupees"


def _confidence_ok(ctx: AgentContext) -> bool:
    """None means the input was not speech (keypad, email, scripted batch
    case) and so is not subject to this gate at all."""
    return ctx.stt_confidence is None or ctx.stt_confidence >= LOW_CONFIDENCE_FLOOR


@dataclass
class Tool:
    name: str
    description: str
    parameters: Dict[str, Any]  # JSON-schema `properties`, sent to the model
    required: List[str]
    fn: Callable[..., Awaitable[Dict[str, Any]]]  # fn(ctx: AgentContext, **args) -> dict
    mutating: bool  # True => sequential execution + subject to the degraded gate
    # True => a successful call ENDS the conversation turn, and the runtime
    # must not route back to the model for a closing sentence.
    #
    # This is not a shortcut: the caller already refuses to use the model's
    # text after these tools (voice_runtime prefers its own line, because
    # what is said after money moves has to be true about what moved). The
    # second LLM round trip was therefore always paid for output that was
    # discarded - and it is the round trip that pushed the "yes, send it"
    # turn past Twilio's 15s window on every live call.
    terminal: bool = False

    def to_openai_schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": self.parameters,
                    "required": self.required,
                },
            },
        }


# ---------------------------------------------------------------------------
# Read-only tools - safe to run concurrently with each other.
#
# Every DB call below goes through run_db_async (a worker thread), NOT a
# direct blocking psycopg2 call. This is what makes the runtime's
# `asyncio.gather` over read-only tools real parallelism instead of
# theatre: a plain sync call inside an `async def` still occupies the one
# event loop thread, so gathering two of them just runs them back to back
# AND stalls every other concurrent caller (every other live voice call,
# every dashboard request) for the duration. With N concurrent voice calls
# on one worker, that difference is the whole ballgame.
# ---------------------------------------------------------------------------

async def _get_cart(ctx: AgentContext) -> Dict[str, Any]:
    if not ctx.checkout_id:
        return {"error": "no_checkout_in_context"}
    checkout = await run_db_async(checkouts_repo.get_checkout, ctx.checkout_id)
    if not checkout:
        return {"error": "checkout_not_found"}
    # Rupees, spelled out, because this number is READ ALOUD.
    #
    # Handing the model paise made it do the arithmetic itself, and on a
    # live call it said "two hundred ninety-nine thousand paise, which is
    # two hundred ninety-nine rupees" about a cart costing 1,290. Wrong by a
    # factor of ten and unintelligible in the same breath. A model asked to
    # divide by 100 mid-sentence will sometimes get it wrong; a model handed
    # the finished figure cannot.
    #
    # amount_paise stays for the machinery. say_amount is what the agent is
    # told to speak.
    amount_paise = checkout["amount_paise"]
    return {
        "amount_paise": amount_paise,
        "amount_inr": round(amount_paise / 100.0, 2),
        "say_amount": _say_rupees(amount_paise),
        "currency": checkout.get("currency", "INR"),
        # cogs is NOT returned. It is the merchant's cost price, it is
        # nothing to do with what this agent says to a customer, and a model
        # that can see it can say it out loud. The policy engine reads it
        # straight from the database where it belongs - see
        # policy_engine.evaluate's margin arithmetic.
        "status": checkout["status"],
    }


get_cart = Tool(
    name="get_cart",
    description=(
        "Look up the real cart for the current checkout - amount, currency, and cost basis. "
        "Only needed if the customer asks what is in their cart. Do NOT call this before "
        "check_offer: check_offer loads the cart itself."
    ),
    parameters={},
    required=[],
    fn=_get_cart,
    mutating=False,
)


async def _get_policy_limits(ctx: AgentContext) -> Dict[str, Any]:
    policy = await run_db_async(policy_engine.get_policy, ctx.merchant_id)
    return {
        "max_discount_percent": policy["max_discount_percent"],
        "minimum_margin_percent": policy["minimum_margin_percent"],
        "offer_ladder": policy.get("offer_ladder", []),
        "auto_approval_threshold_inr": policy.get("auto_approval_threshold_inr", 0),
    }


get_policy_limits = Tool(
    name="get_policy_limits",
    description=(
        "Look up this merchant's configured discount ceiling, margin floor, and offer ladder. "
        "Rarely needed: check_offer applies these limits itself and tells you what was approved. "
        "Do NOT call this before check_offer."
    ),
    parameters={},
    required=[],
    fn=_get_policy_limits,
    mutating=False,
)


# ---------------------------------------------------------------------------
# Mutating tools - DB writes, executed strictly sequentially by the runtime.
# ---------------------------------------------------------------------------

async def _check_offer(ctx: AgentContext, requested_discount_percent: float, reason: str = "") -> Dict[str, Any]:
    """Applies nothing. Computes what WOULD be approved and mints a token
    the model can hand back to issue_offer - the only tool that can act."""
    if not ctx.checkout_id:
        return {"decision": "DENY", "reason": "no_checkout_in_context"}

    # Did we actually hear them? See LOW_CONFIDENCE_FLOOR.
    if not _confidence_ok(ctx):
        return {
            "decision": "DENY",
            "reason": "REJECTED_LOW_CONFIDENCE",
            "detail": (
                f"speech confidence {ctx.stt_confidence:.2f} is below "
                f"{LOW_CONFIDENCE_FLOOR:.2f} - ask them to repeat that before "
                "discussing any price"
            ),
        }

    # A DISCOUNT on a SPOKEN turn requires that the customer has confirmed
    # the barrier we read back to them.
    #
    # Three things are deliberately narrow here. Full price is never gated
    # - a customer who asks for the link gets the link, which is the whole
    # point of FINDINGS #1. Non-speech turns are never gated, because the
    # rule exists to guard against MISHEARING and a keypad press or a typed
    # email reply cannot be misheard. And the check is on the tool, not in
    # the prompt, for the same reason the offer token exists.
    # The sale comes first, as a mechanism rather than a sentence.
    #
    # A declined card, a failed 3DS step or a bank timeout is not a price
    # objection - those customers want to pay the full amount and need a
    # working link. That rule has existed since FINDINGS #1, but only ever
    # as a paragraph in the system prompt, which is precisely the kind of
    # guarantee this codebase does not accept anywhere else.
    #
    # Note what this does NOT do: it is full price FIRST, not full price
    # forever. If the customer goes on to say plainly that the price is too
    # high, the confirmation turn sets barrier_confirmed and the discount
    # becomes available. FINDINGS #1 records the correction to that bug
    # overshooting in exactly this direction - sending full price to people
    # who had explicitly raised price - and both directions lose money.
    if (
        requested_discount_percent > 0
        and ctx.failure_class in FULL_PRICE_FIRST_CLASSES
        and not ctx.barrier_confirmed
    ):
        return {
            "decision": "DENY",
            "reason": "REJECTED_FULL_PRICE_FIRST",
            "detail": (
                f"this checkout failed as {ctx.failure_class} - their payment broke, they did not "
                "object to the price. Send a working link at full price "
                "(requested_discount_percent=0) unless they themselves say the price is too high"
            ),
        }

    if (
        requested_discount_percent > 0
        and ctx.input_is_speech
        and ctx.stt_confidence is not None
        and ctx.stt_confidence < CLEARLY_HEARD_FLOOR
        and not ctx.barrier_confirmed
    ):
        return {
            "decision": "DENY",
            "reason": "REJECTED_UNCONFIRMED_BARRIER",
            "detail": (
                "the customer has not yet confirmed that price is what stopped "
                "them - read it back to them and ask, or send full price with "
                "requested_discount_percent=0"
            ),
        }

    checkout = await run_db_async(checkouts_repo.get_checkout, ctx.checkout_id)
    if not checkout:
        return {"decision": "DENY", "reason": "checkout_not_found"}

    # Already paid, checked at the MONEY GATE itself.
    #
    # This was checked before dialling and once per conversation turn, but
    # never here - so the one place that actually mints spendable offers
    # was the one place that never asked. The turn check closes the gap in
    # practice on a voice call; it does nothing for any other caller, and
    # "the caller happens to check first" is not a property of this tool.
    if checkout.get("status") == "paid" or checkout.get("paid_at"):
        return {
            "decision": "DENY",
            "reason": "REJECTED_ALREADY_PAID",
            "detail": "this checkout has already been paid - there is nothing to recover",
        }

    policy = await run_db_async(policy_engine.get_policy, ctx.merchant_id)
    product_ids = []
    try:
        line_items = json.loads(checkout.get("line_items") or "[]")
        product_ids = [li["product_id"] for li in line_items if isinstance(li, dict) and li.get("product_id")]
    except (TypeError, ValueError):
        pass
    cart_details = {
        "amount": checkout["amount_paise"] / 100.0,
        "cogs": (checkout.get("cogs_paise") or 0) / 100.0,
        "product_ids": product_ids,
    }
    decision = policy_engine.evaluate(
        requested_discount=requested_discount_percent,
        merchant_policy=policy,
        cart_details=cart_details,
    )

    if decision["decision"] == "DENY":
        return {
            "decision": "DENY",
            "reason": decision["reason"],
            "requested_percent": requested_discount_percent,
            "ceiling_percent": decision.get("ceiling_percent"),
            "margin_floor_percent": decision.get("margin_floor_percent"),
        }

    approved_percent = decision["approved_discount"]
    base_amount_paise = checkout["amount_paise"]
    final_amount_paise = int(round(base_amount_paise * (1 - approved_percent / 100)))

    token_row = await run_db_async(
        lambda: offer_tokens_repo.create_offer_token(
            merchant_id=ctx.merchant_id,
            decision=decision["decision"],
            reason=decision["reason"],
            base_amount_paise=base_amount_paise,
            final_amount_paise=final_amount_paise,
            requested_percent=requested_discount_percent,
            approved_percent=approved_percent,
            checkout_id=ctx.checkout_id,
            recovery_attempt_id=ctx.recovery_attempt_id,
        )
    )

    # The mint is conditional on the cart still being unpaid, in the same
    # statement (see create_offer_token). No row means it was paid between
    # the read above and the mint - a real window on a live call, because
    # the customer can be paying from the earlier link while we talk. Same
    # refusal code as the read-based check, so nothing downstream can tell
    # which one caught it, and neither can be skipped.
    if not token_row:
        return {
            "decision": "DENY",
            "reason": "REJECTED_ALREADY_PAID",
            "detail": "this checkout was paid while the offer was being prepared",
        }

    return {
        "decision": decision["decision"],
        # The reason travels on an approval too, not only on a refusal:
        # "you asked 40, you got 10, because of your ceiling" is the whole
        # explanation, and splitting half of it into a different code path
        # is how it ends up missing from the audit row that matters.
        "reason": decision["reason"],
        "requested_percent": requested_discount_percent,
        "approved_percent": approved_percent,
        "ceiling_percent": decision.get("ceiling_percent"),
        "margin_floor_percent": decision.get("margin_floor_percent"),
        "final_amount_paise": final_amount_paise,
        # What to SAY. The model must quote this verbatim rather than
        # converting final_amount_paise itself - see _say_rupees.
        "say_amount": _say_rupees(final_amount_paise),
        "offer_token": token_row["offer_token"],
        "expires_at": str(token_row["expires_at"]),
    }


check_offer = Tool(
    name="check_offer",
    description=(
        "Ask whether a discount could be approved for this customer's cart. Applies nothing - "
        "returns a decision and, if not DENY, an offer_token that must be passed to issue_offer "
        "to actually send it. The approved percent may be lower than what you asked for. "
        "This tool ALREADY loads the real cart and the real policy itself, so you do NOT need to "
        "call get_cart or get_policy_limits first - doing so just wastes time on a live call."
    ),
    parameters={
        "requested_discount_percent": {
            "type": "number",
            "description": "The discount percent you'd like to offer, e.g. 15 for 15%.",
        },
        "reason": {
            "type": "string",
            "description": "One short phrase for why this discount is being considered (audit trail only).",
        },
    },
    required=["requested_discount_percent"],
    fn=_check_offer,
    mutating=True,  # writes an offer_tokens row - sequential, not just a read
)


# What to DO about each way a token can fail, written for the model that
# just got one back. Every line names the next action, because "not found"
# on its own reads as "try a different one" - which is exactly what a model
# did, repeatedly, on a real call.
_OFFER_TOKEN_REJECTION_HELP = {
    "offer_token_not_found": (
        "There is no such offer_token. It cannot be guessed, invented or copied from an example - "
        "the only valid value is one returned by a check_offer call in THIS conversation. "
        "Call check_offer with requested_discount_percent=0 now, then call issue_offer with the "
        "offer_token from its result. Do not call issue_offer again until you have done that."
    ),
    "offer_token_already_consumed": (
        "That offer was already sent - the link is with the customer. Do not send it again; "
        "tell them it is on its way and check their spam folder."
    ),
    "offer_token_expired": (
        "That offer has expired. Call check_offer again to get a fresh one, then issue_offer with it."
    ),
    "offer_token_was_denied": (
        "The policy engine refused that discount, so it can never be sent. Call check_offer with a "
        "lower requested_discount_percent, or 0 for full price."
    ),
    "offer_token_merchant_mismatch": (
        "That token belongs to a different merchant and can never be spent here. "
        "Call check_offer to mint one for this order."
    ),
    "offer_token_checkout_mismatch": (
        "That token was minted for a different order. Call check_offer to mint one for this order."
    ),
}


async def _issue_offer(ctx: AgentContext, offer_token: str, channel: str = "email") -> Dict[str, Any]:
    """The only tool that can make an offer real. Every amount below comes
    from the token row consume_offer_token() returns - never from an
    argument the model passed alongside offer_token. Self-contained: on
    success this also dispatches the real email and advances the recovery
    attempt's state, so a caller (voice_runtime, or anything else that runs
    this agent) doesn't need its own copy of that logic - the exact
    duplication that made call_orchestrator's old _process_offer_request
    and this tool two different paths to the same money-moving action."""
    # The mishearing gate again, on the acting half of the money gate.
    # check_offer already refuses under the floor, but issue_offer must
    # refuse independently: a token minted on a clearly-heard turn must not
    # become spendable by a later, garbled "yes" that was never a yes.
    if not _confidence_ok(ctx):
        return {
            "status": "REJECTED",
            "reason": "REJECTED_LOW_CONFIDENCE",
            "detail": (
                f"speech confidence {ctx.stt_confidence:.2f} is below "
                f"{LOW_CONFIDENCE_FLOOR:.2f} - confirm what they want before sending anything"
            ),
        }
    try:
        token = await run_db_async(
            offer_tokens_repo.consume_offer_token,
            offer_token,
            merchant_id=ctx.merchant_id,
            checkout_id=ctx.checkout_id,
        )
    except ValueError as e:
        # An INSTRUCTIVE refusal, not just a code.
        #
        # On a live call the model invented an offer_token, was told
        # "offer_token_not_found", and responded by inventing another one -
        # five times, while telling the customer the link was on its way.
        # The system prompt already said "call check_offer, then
        # issue_offer". It said it twice. The model does not consult a
        # prompt at the moment it is choosing arguments; it reads the tool
        # result it just got back.
        #
        # So the tool result is where the instruction has to live. This is
        # the same lesson as FINDINGS #14/#15 from the other direction: a
        # rule far from the point of decision loses to one at it.
        reason = str(e)
        detail = _OFFER_TOKEN_REJECTION_HELP.get(
            reason, "Call check_offer first and use the offer_token it returns."
        )
        return {
            "status": "REJECTED",
            # REJECTED_ prefix so this reaches AgentResult.refusals and can
            # be counted. A token the model made up was previously
            # invisible to every refusal tally in the system.
            "reason": f"REJECTED_{reason.upper()}",
            "detail": detail,
        }

    # The question here is "have they told us to stop?", NOT "do we hold a
    # marketing opt-in for this particular protocol?"
    #
    # Both of the obvious versions of this check are wrong. The original
    # asked for VOICE consent while delivering by email - the wrong
    # question, which happened to give the right answer on a phone call and
    # would give a nonsense one anywhere else. Replacing it with a check for
    # consent on the DELIVERY channel looked more correct and was worse: a
    # customer who consented to calls but not email would be phoned, agree
    # to the offer, and have it silently refused - while the call's fallback
    # line told them "I've sent that offer to your email." That is FINDINGS
    # #2 exactly: the customer told the opposite of what happened. It also
    # took the scoreboard's recoveries to zero, which is how it was caught.
    #
    # What is actually happening is that a customer, mid-conversation on a
    # channel we already had consent for, has ASKED for this link. Sending
    # it is completing a request, not initiating contact. So the bar is the
    # broad one: a customer who has opted out anywhere gets nothing.
    if ctx.customer_id and await run_db_async(
        consents_repo.has_opted_out, ctx.merchant_id, ctx.customer_id
    ):
        return {
            "status": "REJECTED",
            "reason": "consent_revoked",
            "detail": "this customer has asked not to be contacted",
        }

    approved_percent = token["approved_percent"] or 0.0
    original_amount = token["base_amount_paise"] / 100.0

    # The cart and the store name used to be read HERE, two blocking round
    # trips, purely to fill in the item name and business name of the email
    # that follows. They are resolved by the email subscriber now.
    #
    # This was not an optimisation, it was the bug. Twilio's real webhook
    # budget on this deployment measures about FIVE seconds, not the 15 the
    # documentation implies - established from six live turns, where every
    # turn under 4s survived and every turn over 5.2s was killed with "an
    # application error has occurred". The turn that sends the link was
    # taking 5.7s, and roughly a third of that was these two reads. The call
    # was being destroyed to look up a product name for an email nobody was
    # waiting on.
    #
    # Nothing about a cart's items or a merchant's name can change between
    # this moment and the email going out, so there was never a reason to
    # know them any earlier - see email_service.handle_send_requested.
    # This runs INSIDE a live phone call, where every sequential DB round
    # trip costs ~2-2.8s from Railway to Supabase and the whole turn must
    # finish before Twilio's ~15s deadline. The customer lookup does not
    # depend on the payment link, so the two run concurrently instead of
    # back to back - this is the single most latency-sensitive tool in the
    # system, and it used to make six sequential awaits at the exact moment
    # the customer had just said yes.
    customer_task = (
        asyncio.create_task(run_db_async(customers_repo.get_customer, ctx.customer_id))
        if ctx.customer_id else None
    )

    # Reuse a live link for this exact cart at this exact price, rather than
    # minting a second one that says the same thing.
    #
    # Razorpay's test mode caps an account at thirty payment links FOREVER,
    # and this project has exhausted that twice - both times presenting as a
    # broken integration rather than an exhausted quota (FINDINGS #7). The
    # cause is that every attempt minted a fresh link, including a retry of
    # a cart nothing about which had changed.
    #
    # A customer sent the same URL twice receives the thing they were
    # already promised, so this is the correct artifact and not a
    # workaround. The offer token is still consumed either way: the offer
    # was spent, only the link is shared.
    reused = None
    if ctx.checkout_id:
        try:
            reused = await run_db_async(
                recovery_attempts_repo.find_reusable_payment_link,
                ctx.merchant_id,
                ctx.checkout_id,
                token["final_amount_paise"],
            )
        except Exception as e:
            # A lookup that fails must not stop the sale - mint instead.
            logger.warning(f"issue_offer: reusable-link lookup failed ({e}); minting a new one.")

    if reused:
        logger.info(
            f"issue_offer: reusing live payment link {reused['rzp_payment_link_id']} "
            f"for {ctx.checkout_id} at {token['final_amount_paise']} paise."
        )
        payment_result = {
            "payment_link_id": reused["rzp_payment_link_id"],
            "url": reused["rzp_payment_link_url"],
            "final_amount": token["final_amount_paise"] / 100.0,
            "expires_at": reused["rzp_payment_link_expires_at"],
        }
    else:
        try:
            payment_result = await payment_execution.generate_recovery_checkout(
                merchant_id=ctx.merchant_id,
                checkout_id=ctx.checkout_id or "",
                customer_id=ctx.customer_id or "",
                recovery_attempt_id=ctx.recovery_attempt_id or "",
                original_amount=original_amount,
                approved_discount_percent=approved_percent,
                offer_token=offer_token,
            )
        except PaymentExecutionError as e:
            if customer_task:
                customer_task.cancel()
            return {"status": "REJECTED", "reason": f"payment_execution_failed: {e}"}

    # Bookkeeping, not the money action - the link already exists and the
    # customer is waiting on the line. Don't hold the call open for it.
    #
    # _spawn_write, not a bare create_task: asyncio keeps only a WEAK
    # reference to a running task, so a fire-and-forget write can be
    # garbage collected before it lands. Of all the writes in this file
    # that would be the worst one to lose - it is the record that a real
    # payment link went out.
    if ctx.recovery_attempt_id:
        _spawn_write(
            run_db_async(
                lambda: recovery_attempts_repo.update_state(
                    ctx.recovery_attempt_id,
                    "PAYMENT_LINK_SENT",
                    approved_discount_percent=approved_percent,
                    final_amount_paise=token["final_amount_paise"],
                    rzp_payment_link_id=payment_result["payment_link_id"],
                    rzp_payment_link_url=payment_result["url"],
                    # Stored so a later attempt can tell this link from a
                    # dead one. Without it no link is ever reusable.
                    rzp_payment_link_expires_at=payment_result.get("expires_at"),
                )
            )
        )

    customer_email = ""
    if customer_task:
        try:
            customer = await customer_task
            customer_email = (customer or {}).get("email", "")
        except Exception as e:
            logger.warning(f"issue_offer: customer lookup failed ({e}); link created, email skipped.")

    if customer_email and channel == "email":
        # checkout_id and merchant_id travel instead of the strings they
        # resolve to. The subscriber looks up the item name and business
        # name itself, off this call's clock.
        #
        # The identity still has to be REAL - the template once fell back to
        # hardcoded demo values and emailed a Loomwork customer about a
        # "Handcrafted Bamboo Lamp" from "JIVA LIFESTYLE". That guarantee is
        # unchanged and now lives with the code that renders the email,
        # which is where it belongs.
        _spawn_write(
            bus.publish(
                event_type="email.send_requested",
                payload={
                    "recovery_attempt_id": ctx.recovery_attempt_id,
                    "checkout_id": ctx.checkout_id,
                    "customer_email": customer_email,
                    "customer_name": (customer or {}).get("name", "") if ctx.customer_id else "",
                    "payment_url": payment_result["url"],
                    "amount": payment_result["final_amount"],
                    "base_price": original_amount,
                    "discount": approved_percent,
                },
                correlation_id=ctx.correlation_id,
                merchant_id=ctx.merchant_id,
                idempotency_key=f"email_{ctx.recovery_attempt_id or offer_token}",
            )
        )
    elif channel == "email":
        logger.warning(f"issue_offer for {ctx.recovery_attempt_id}: no email on file, offer link not sent anywhere")

    return {
        "status": "ISSUED",
        "offer_token": offer_token,
        "approved_percent": approved_percent,
        "final_amount_paise": token["final_amount_paise"],
        "say_amount": _say_rupees(token["final_amount_paise"]),
        "payment_url": payment_result["url"],
        "channel": channel,
        "email_sent": bool(customer_email and channel == "email"),
    }


issue_offer = Tool(
    name="issue_offer",
    description=(
        "Send an already-approved offer to the customer. Requires an offer_token from check_offer - "
        "the amount sent is whatever that token says, not anything passed here."
    ),
    parameters={
        "offer_token": {"type": "string", "description": "The token returned by check_offer."},
        "channel": {"type": "string", "enum": ["email", "sms"], "description": "How to deliver the offer."},
    },
    required=["offer_token"],
    fn=_issue_offer,
    mutating=True,
    # The link is sent; there is nothing left to negotiate this turn, and
    # voice_runtime already speaks its own line here rather than the
    # model's.
    terminal=True,
)



def _parse_promise_date(raw: str):
    """Turns what a person says on the phone into a real date.

    Speech-to-text gives us "tomorrow" or "in 3 days" far more often than an
    ISO string, so accept both rather than rejecting a valid promise on
    formatting. Anything in the past, or beyond a sensible horizon, is
    refused - a promise for last Tuesday is a transcription error, not a
    commitment.
    """
    from datetime import datetime, timedelta, timezone as _tz
    import re as _re

    if not raw:
        return None
    text = str(raw).strip().lower()
    now = datetime.now(_tz.utc)

    if text in ("today", "tonight"):
        return now + timedelta(hours=8)
    if text in ("tomorrow", "tmrw"):
        return now + timedelta(days=1)

    m = _re.search(r"(\d+)\s*day", text)
    if m:
        days = int(m.group(1))
        return now + timedelta(days=days) if 0 < days <= 60 else None

    try:
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_tz.utc)
        # A date in the past, or absurdly far out, is a mishearing.
        if parsed < now - timedelta(days=1) or parsed > now + timedelta(days=60):
            return None
        return parsed
    except ValueError:
        return None

async def _record_opt_out(ctx: AgentContext) -> Dict[str, Any]:
    """"Don't contact me again" - across every channel, not just this one.

    This revoked voice only. That was survivable while voice was the only
    way anyone was ever contacted; it stopped being survivable the moment
    email became a real consented channel, because a customer who said
    "take me off your list" on the phone would have kept receiving email.
    They did not ask to stop being phoned. They asked to stop being
    contacted, and a customer should not have to opt out once per protocol
    we happen to have implemented.
    """
    if not ctx.customer_id:
        return {"status": "REJECTED", "reason": "no_customer_in_context"}
    for channel in OPT_OUT_CHANNELS:
        await identity_service.revoke_consent(
            ctx.merchant_id, ctx.customer_id, channel=channel, source="agent_tool"
        )
    if ctx.recovery_attempt_id:
        # Was previously only reflected in the consents table - the
        # recovery_attempts row itself stayed wherever it was (e.g.
        # "CREATED"), which undercounted opt-outs on the dashboard's
        # Overview KPIs. This is the same state issue_offer already
        # updates on its own success; opt-out deserves the same honesty.
        await run_db_async(recovery_attempts_repo.update_state, ctx.recovery_attempt_id, "CONSENT_REVOKED")
    return {"status": "RECORDED"}



async def _record_promise_to_pay(
    ctx: AgentContext,
    pay_date: str,
    amount_inr: float = 0.0,
    customer_words: str = "",
    offer_token: str = "",
) -> Dict[str, Any]:
    """The customer said they will pay, just not now.

    This is a STOPPING rule, not a soft outcome. "I'll pay on Friday" means
    stop selling, stop calling, and wait until Friday - continuing to chase
    someone who has already committed is how a recovery call becomes
    harassment. The date they named is stored, their exact words are stored
    (so the merchant can see what was actually said rather than our summary
    of it), and the sweeper leaves them alone until that date passes.
    """
    if not ctx.recovery_attempt_id:
        return {"status": "REJECTED", "reason": "no_recovery_attempt_in_context"}

    parsed = _parse_promise_date(pay_date)
    if parsed is None:
        return {"status": "REJECTED", "reason": f"unparseable_date: {pay_date!r}"}

    await run_db_async(
        lambda: recovery_attempts_repo.update_state(
            ctx.recovery_attempt_id,
            "PROMISED",
            promised_at=parsed,
            promised_amount_paise=int(round(amount_inr * 100)) if amount_inr else None,
            promise_words=(customer_words or "")[:500],
            # WHICH offer they promised against. Recording only a date
            # loses the terms the promise was about, so a reminder days
            # later cannot say what was actually agreed - and a promise
            # nobody can restate is not much of a promise. Storing the
            # token moves nothing: it is a reference to terms the server
            # computed, exactly as everywhere else in this file.
            promised_offer_token=(offer_token or None),
        )
    )
    logger.info(f"Promise to pay recorded for {ctx.recovery_attempt_id}: {parsed.date()} - outreach paused until then.")
    return {
        "status": "RECORDED",
        "promised_date": parsed.date().isoformat(),
        "outreach_paused_until": parsed.date().isoformat(),
    }


record_promise_to_pay = Tool(
    name="record_promise_to_pay",
    description=(
        "Record that the customer has committed to paying on a specific date, and STOP selling. "
        "Call this the moment they name a time ('I'll pay tomorrow', 'after payday on the 1st'). "
        "Do not keep negotiating or offer a discount after this - they have already said yes, just "
        "not yet. Still send them the payment link so it is waiting for them."
    ),
    parameters={
        "pay_date": {
            "type": "string",
            "description": "When they said they will pay: an ISO date (2026-09-01), or 'today', 'tomorrow', or a number of days ('in 3 days').",
        },
        "amount_inr": {"type": "number", "description": "Amount they said they would pay, if they named one. 0 if not."},
        "customer_words": {"type": "string", "description": "Their own words, quoted as closely as you heard them."},
        "offer_token": {
            "type": "string",
            "description": (
                "The offer_token from check_offer, if they are promising to pay a price you already "
                "quoted them. Leave empty if no offer was discussed."
            ),
        },
    },
    required=["pay_date"],
    fn=_record_promise_to_pay,
    mutating=True,
)

record_opt_out = Tool(
    name="record_opt_out",
    description="Record that the customer asked not to be contacted again. Call this the moment they say so.",
    parameters={},
    required=[],
    fn=_record_opt_out,
    mutating=True,
    # They asked us to stop. Asking a model what to say next is the one
    # thing that cannot improve this turn.
    terminal=True,
)


ALL_TOOLS: List[Tool] = [
    get_cart, get_policy_limits, check_offer, issue_offer,
    record_promise_to_pay, record_opt_out,
]
TOOLS_BY_NAME: Dict[str, Tool] = {t.name: t for t in ALL_TOOLS}


def _assert_no_forbidden_args() -> None:
    for tool in ALL_TOOLS:
        offending = FORBIDDEN_ARG_NAMES & set(tool.parameters.keys())
        if offending:
            raise RuntimeError(
                f"Tool {tool.name!r} declares forbidden argument(s) {offending} - "
                f"identity/money fields must come from AgentContext, never model output."
            )


_assert_no_forbidden_args()
