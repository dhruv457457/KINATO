"""
[RecoveryStrategist] Runs once per recovery opportunity, before any call is
placed, and produces the plan (opening line + one talking point) the call
actually opens with.

Previously this ran as a *parallel* subscriber to the same
`recovery.opportunity.created` event as call_orchestrator - the orchestrator
never waited for this agent's output, so its brief was published to
`recovery.brief_ready`, an event nobody consumed. This module now publishes
`recovery.plan_ready`, which call_orchestrator explicitly waits for (with a
6s watchdog - see call_orchestrator.py) before dialing.

Also previously read customer_name/phone/email/product_ids off the
*incoming event payload* with hardcoded defaults ("Dhruv", a bamboo lamp,
₹3,499) - but recovery.opportunity.created (see recovery_eligibility.py)
only ever carries checkout_id/customer_id/amount/currency, so those
defaults fired on every single call, unconditionally. This version loads
the real checkout and customer rows instead.
"""
import json
import logging
import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.core.config import settings
from app.gateway.event_bus import bus
from app.db.repositories import checkouts as checkouts_repo
from app.db.repositories import customers as customers_repo
from app.db.repositories import merchants as merchants_repo

logger = logging.getLogger(__name__)


class CallPlan(BaseModel):
    opening_line: str
    talking_point: str = ""
    degraded: bool = False


def _describe_cart(checkout: Dict[str, Any]) -> str:
    """Best-effort human description from real line_items - never a
    fabricated product name when none is on record."""
    try:
        line_items = json.loads(checkout.get("line_items") or "[]")
    except (TypeError, ValueError):
        line_items = []
    names = [li.get("name") for li in line_items if isinstance(li, dict) and li.get("name")]
    if names:
        return ", ".join(names[:3]) + (" and more" if len(names) > 3 else "")
    return "your order"


_PLACEHOLDER_PATTERN = re.compile(
    r"\[[^\]]{1,40}\]"           # [Your Name], [Company], [product]
    r"|\{[^}]{1,40}\}"            # {name}, {{company}}
    r"|<[^>]{1,40}>"              # <name>
    r"|\byour (?:name|company|business|store)\b"  # bare "Your Company"
    r"|\bxyz\b|\bacme\b",
    re.IGNORECASE,
)


def contains_placeholder(text: str) -> bool:
    """True if this text still contains a template slot.

    An opening line is SPOKEN ALOUD to a real customer, and nothing
    downstream fills these in - a model that writes "this is [Your Name]
    from [Your Company]" produces a call that reads those brackets out.
    That happened on a real call. The prompt now forbids it, but a prompt
    is a request, not a guarantee, so this is the structural check: any
    line matching here is discarded in favour of a safe line that simply
    names nobody.
    """
    return bool(text) and bool(_PLACEHOLDER_PATTERN.search(text))


class RecoveryStrategist:
    @staticmethod
    async def handle_opportunity_created(event: Dict[str, Any]):
        payload = event.get("payload", {})
        checkout_id = payload.get("checkout_id")
        customer_id = payload.get("customer_id")
        merchant_id = event.get("merchant_id")
        correlation_id = event.get("correlation_id")

        checkout = checkouts_repo.get_checkout(checkout_id) if checkout_id else None
        customer = customers_repo.get_customer(customer_id) if customer_id else None

        if not checkout:
            logger.warning(f"RecoveryStrategist: no checkout row for {checkout_id}, skipping plan generation.")
            return

        customer_name = (customer or {}).get("name") or ""
        item_description = _describe_cart(checkout)
        amount = checkout["amount_paise"] / 100.0

        # Who the agent says it is. If the merchant has no usable business
        # name on file we pass an empty string and the opening line simply
        # doesn't name one - far better than the model inventing a
        # placeholder and reading "[Your Company]" aloud to a customer.
        business_name = ""
        try:
            merchant = merchants_repo.get_merchant(merchant_id) if merchant_id else None
            business_name = ((merchant or {}).get("name") or "").strip()
        except Exception:
            business_name = ""

        plan = await RecoveryStrategist._generate_plan(customer_name, item_description, amount, business_name)

        await bus.publish(
            event_type="recovery.plan_ready",
            payload={"checkout_id": checkout_id, "customer_id": customer_id, "plan": plan.model_dump()},
            correlation_id=correlation_id,
            merchant_id=merchant_id,
        )

    @staticmethod
    async def _generate_plan(customer_name: str, item_description: str, amount: float, business_name: str = "") -> CallPlan:
        greeting_name = f" Is this {customer_name}?" if customer_name else ""
        if not settings.OPENROUTER_API_KEY:
            return CallPlan(
                opening_line=f"Hi there!{greeting_name} I noticed you were checking out {item_description} "
                             f"and wanted to see if I could help you finish that up.",
                talking_point="",
                degraded=True,
            )

        who = (
            f"You are calling on behalf of {business_name}. You may say that name."
            if business_name
            else "You do NOT know the caller's name or the business name. Do not state either one - "
                 "open with the reason for the call instead."
        )
        prompt = (
            f"Write a one-sentence warm, natural phone opening line for a checkout-recovery call, and one short "
            f"talking point to use if the customer hesitates on price. "
            f"{who} "
            f"Customer name: {customer_name or 'unknown - do not guess a name'}. "
            f"Item(s): {item_description}. Cart total: INR {amount:.2f}. "
            f"This text is SPOKEN ALOUD to a real customer. NEVER output a placeholder, bracket, or template "
            f"slot such as [Your Name], [Your Company], {{name}} or XYZ - there is no later step that fills "
            f"them in. If you do not know a fact, leave it out entirely. "
            f"Do not invent product details you weren't given. Output JSON with keys: opening_line, talking_point."
        )
        try:
            from openai import AsyncOpenAI
            from app.core.net import ipv4_client

            client = AsyncOpenAI(base_url=settings.LLM_BASE_URL, api_key=settings.OPENROUTER_API_KEY, http_client=ipv4_client())
            response = await client.chat.completions.create(
                model=settings.LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                max_tokens=120,
                temperature=0.4,
                timeout=4.0,
            )
            parsed = json.loads(response.choices[0].message.content)
            safe_fallback = (
                f"Hi there!{greeting_name} I noticed you were checking out {item_description} "
                f"and wanted to see if I could help you finish that up."
            )
            line = parsed.get("opening_line") or safe_fallback
            if contains_placeholder(line):
                logger.warning(
                    "RecoveryStrategist: model returned an opening line containing a placeholder "
                    f"({line!r}) - discarding it rather than speaking it to a customer."
                )
                line = safe_fallback
            talking_point = parsed.get("talking_point", "")
            if contains_placeholder(talking_point):
                talking_point = ""
            return CallPlan(opening_line=line, talking_point=talking_point, degraded=False)
        except Exception as e:
            logger.warning(f"RecoveryStrategist LLM call failed, using heuristic plan: {e}")
            return CallPlan(
                opening_line=f"Hi there!{greeting_name} I noticed you were checking out {item_description} "
                             f"and wanted to see if I could help you finish that up.",
                talking_point="",
                degraded=True,
            )


recovery_strategist = RecoveryStrategist()
bus.subscribe("recovery.opportunity.created", RecoveryStrategist.handle_opportunity_created)
