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


# Hinglish, for the FIRST sentence of the call.
#
# A call is written by two prompts: this one, which produces the opening
# line before the phone rings, and voice_runtime's, which produces every
# turn after it. AGENT_LANGUAGE was wired into the second and not the
# first, so a merchant who set hinglish heard:
#
#   "Hi Dhruv, I am calling from Loomwork about your order that you left
#    in your cart."
#
# - English, every time, on the one sentence that sets the register for the
# whole conversation. An agent that opens in English and drifts into
# Hinglish sounds like two different people.
#
# Roman script and English numerals, for the same reasons the turn-level
# style gives: the line is read aloud by an Indian-English voice, so
# Devanagari comes out as nonsense, and a mispronounced rupee amount is the
# worst mistake this call can make.
_HINGLISH_OPENING = (
    "WRITE THE OPENING LINE IN HINGLISH - the everyday Hindi-English mix an Indian "
    "salesperson actually uses on the phone, not formal Hindi. Natural code-switching: "
    '"Hi Dhruv, main Loomwork se baat kar raha hoon - aapka order cart mein reh gaya tha." '
    "Write it in ROMAN script only, never Devanagari, because this is read aloud by an "
    "English-language voice. Keep every number, amount and product name in English. "
)


def _opening_line_prompt(
    customer_name: str, item_description: str, amount: float, business_name: str
) -> str:
    """The prompt that writes the first thing a customer hears.

    A named function rather than an inline string so the language setting
    and a test can both reach it - the setting is read here, at call time,
    so a merchant changing it does not need a redeploy to hear the
    difference.
    """
    who = (
        f"You are calling on behalf of {business_name}. Refer to yourself only as "
        f"{business_name} - as an organisation, never as a named individual. "
        f'Say "I am calling from {business_name}", never "this is <somebody> from {business_name}".'
        if business_name
        else "You do NOT know the caller's name or the business name. Do not state either one - "
             "open with the reason for the call instead."
    )
    hinglish = (
        _HINGLISH_OPENING
        if (settings.AGENT_LANGUAGE or "").strip().lower() == "hinglish"
        else ""
    )
    return (
        f"Write a one-sentence warm, natural phone opening line for a checkout-recovery call, and one short "
        f"talking point to use if the customer hesitates on price. "
        f"{hinglish}"
        f"{who} "
        f"Customer name: {customer_name or 'unknown - do not guess a name'}. "
        f"Item(s): {item_description}. Cart total: INR {amount:.2f}. "
        f"This text is SPOKEN ALOUD to a real customer, exactly as written, by a system that fills in "
        f"nothing afterwards. Every word must be one you would say out loud. If you do not know a fact, "
        f"leave it out of the sentence entirely rather than gesturing at it. "
        f"Do not invent product details you weren't given. "
        f'A good opening line looks like: "Hi Asha, I am calling from Loomwork about the table runner you '
        f'were looking at earlier." '
        f"Output JSON with keys: opening_line, talking_point."
    )


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
                opening_line=f"Hello! Can you hear me alright?{greeting_name}",
                talking_point="",
                degraded=True,
            )

        # The caller has no personal name, and that is the whole problem.
        # Told only that it may say the business name, the model reaches for
        # a self-introduction anyway and invents a slot for the name it was
        # never given. So the shape of the sentence is specified instead of
        # a fact being withheld: say the business, never a person.
        who = (
            f"You are calling on behalf of {business_name}. Refer to yourself only as "
            f"{business_name} - as an organisation, never as a named individual. "
            f'Say "I am calling from {business_name}", never "this is <somebody> from {business_name}".'
            if business_name
            else "You do NOT know the caller's name or the business name. Do not state either one - "
                 "open with the reason for the call instead."
        )
        prompt = _opening_line_prompt(customer_name, item_description, amount, business_name)
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
                f"Hello! Can you hear me alright?{greeting_name}"
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
                opening_line=f"Hello! Can you hear me alright?{greeting_name}",
                talking_point="",
                degraded=True,
            )


recovery_strategist = RecoveryStrategist()
bus.subscribe("recovery.opportunity.created", RecoveryStrategist.handle_opportunity_created)
