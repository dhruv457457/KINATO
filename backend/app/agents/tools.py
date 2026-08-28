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
from app.db.repositories import offer_tokens as offer_tokens_repo
from app.db.repositories import customers as customers_repo
from app.db.repositories import merchants as merchants_repo
from app.db.repositories import recovery_attempts as recovery_attempts_repo
from app.services.policy_engine import policy_engine
from app.services.payment_execution import payment_execution, PaymentExecutionError
from app.services.identity_service import identity_service
from app.gateway.event_bus import bus

logger = logging.getLogger(__name__)

# Never accepted as a tool-call argument from the model, in any tool below.
# These come from AgentContext, built by the caller (call_orchestrator /
# voice_runtime / merchant intelligence route), never from model output.
FORBIDDEN_ARG_NAMES = {
    "merchant_id", "customer_id", "checkout_id", "recovery_attempt_id",
    "phone", "email", "amount", "amount_paise", "price", "cogs",
}


@dataclass
class Tool:
    name: str
    description: str
    parameters: Dict[str, Any]  # JSON-schema `properties`, sent to the model
    required: List[str]
    fn: Callable[..., Awaitable[Dict[str, Any]]]  # fn(ctx: AgentContext, **args) -> dict
    mutating: bool  # True => sequential execution + subject to the degraded gate

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
    return {
        "amount_paise": checkout["amount_paise"],
        "currency": checkout.get("currency", "INR"),
        "cogs_paise": checkout.get("cogs_paise"),
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

    checkout = await run_db_async(checkouts_repo.get_checkout, ctx.checkout_id)
    if not checkout:
        return {"decision": "DENY", "reason": "checkout_not_found"}

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
        return {"decision": "DENY", "reason": decision["reason"]}

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

    return {
        "decision": decision["decision"],
        "requested_percent": requested_discount_percent,
        "approved_percent": approved_percent,
        "final_amount_paise": final_amount_paise,
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


async def _issue_offer(ctx: AgentContext, offer_token: str, channel: str = "email") -> Dict[str, Any]:
    """The only tool that can make an offer real. Every amount below comes
    from the token row consume_offer_token() returns - never from an
    argument the model passed alongside offer_token. Self-contained: on
    success this also dispatches the real email and advances the recovery
    attempt's state, so a caller (voice_runtime, or anything else that runs
    this agent) doesn't need its own copy of that logic - the exact
    duplication that made call_orchestrator's old _process_offer_request
    and this tool two different paths to the same money-moving action."""
    try:
        token = await run_db_async(
            offer_tokens_repo.consume_offer_token,
            offer_token,
            merchant_id=ctx.merchant_id,
            checkout_id=ctx.checkout_id,
        )
    except ValueError as e:
        return {"status": "REJECTED", "reason": str(e)}

    if ctx.customer_id and not await identity_service.check_consent(ctx.merchant_id, ctx.customer_id, channel="voice"):
        return {"status": "REJECTED", "reason": "consent_revoked"}

    approved_percent = token["approved_percent"] or 0.0
    original_amount = token["base_amount_paise"] / 100.0

    # Real cart + real store name for the email that follows.
    checkout_row = await run_db_async(checkouts_repo.get_checkout, ctx.checkout_id) if ctx.checkout_id else None
    business_name = ""
    try:
        merchant_row = await run_db_async(merchants_repo.get_merchant, ctx.merchant_id)
        business_name = ((merchant_row or {}).get("name") or "").strip()
    except Exception:
        business_name = ""

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
    if ctx.recovery_attempt_id:
        asyncio.create_task(
            run_db_async(
                lambda: recovery_attempts_repo.update_state(
                    ctx.recovery_attempt_id,
                    "PAYMENT_LINK_SENT",
                    approved_discount_percent=approved_percent,
                    final_amount_paise=token["final_amount_paise"],
                    rzp_payment_link_id=payment_result["payment_link_id"],
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
        # Real identity for the email. Without these the template fell back
        # to hardcoded demo values and sent a customer of "Loomwork" an
        # email branded "JIVA LIFESTYLE" about a "Handcrafted Bamboo Lamp"
        # they had never looked at - leftovers of the deleted demo product.
        item_name = ""
        try:
            line_items = json.loads(checkout_row.get("line_items") or "[]") if checkout_row else []
            names = [li.get("name") or li.get("product_id") for li in line_items if isinstance(li, dict)]
            names = [n for n in names if n]
            item_name = ", ".join(names[:3])
        except (TypeError, ValueError):
            item_name = ""

        await bus.publish(
            event_type="email.send_requested",
            payload={
                "recovery_attempt_id": ctx.recovery_attempt_id,
                "checkout_id": ctx.checkout_id,
                "customer_email": customer_email,
                "customer_name": (customer or {}).get("name", "") if ctx.customer_id else "",
                "business_name": business_name,
                "item_name": item_name,
                "payment_url": payment_result["url"],
                "amount": payment_result["final_amount"],
                "base_price": original_amount,
                "discount": approved_percent,
            },
            correlation_id=ctx.correlation_id,
            merchant_id=ctx.merchant_id,
            idempotency_key=f"email_{ctx.recovery_attempt_id or offer_token}",
        )
    elif channel == "email":
        logger.warning(f"issue_offer for {ctx.recovery_attempt_id}: no email on file, offer link not sent anywhere")

    return {
        "status": "ISSUED",
        "offer_token": offer_token,
        "approved_percent": approved_percent,
        "final_amount_paise": token["final_amount_paise"],
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
)


async def _record_opt_out(ctx: AgentContext) -> Dict[str, Any]:
    if not ctx.customer_id:
        return {"status": "REJECTED", "reason": "no_customer_in_context"}
    await identity_service.revoke_consent(ctx.merchant_id, ctx.customer_id, channel="voice", source="agent_tool")
    if ctx.recovery_attempt_id:
        # Was previously only reflected in the consents table - the
        # recovery_attempts row itself stayed wherever it was (e.g.
        # "CREATED"), which undercounted opt-outs on the dashboard's
        # Overview KPIs. This is the same state issue_offer already
        # updates on its own success; opt-out deserves the same honesty.
        await run_db_async(recovery_attempts_repo.update_state, ctx.recovery_attempt_id, "CONSENT_REVOKED")
    return {"status": "RECORDED"}


record_opt_out = Tool(
    name="record_opt_out",
    description="Record that the customer asked not to be contacted again. Call this the moment they say so.",
    parameters={},
    required=[],
    fn=_record_opt_out,
    mutating=True,
)


ALL_TOOLS: List[Tool] = [get_cart, get_policy_limits, check_offer, issue_offer, record_opt_out]
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
