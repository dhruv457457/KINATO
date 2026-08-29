"""
The two things a merchant wants to do from a row in the Recoveries table:
try that one again, and understand what happened.

Both are deliberately narrow.

**Retry does not bypass anything.** It re-publishes the same
`checkout.payment_failed` the webhook would have sent, so the recovery runs
the identical path from the top: already-paid, consent, rail health, quiet
hours, contact caps, active promises. A "retry" that skipped the guards
would be a button that lets a merchant phone someone at midnight, and every
stopping rule in this system would become advisory the moment it shipped.
When a guard refuses, the merchant is told which one and why - a refusal
they can read is the point, not a failure to work around.

**Explain reads rows and does not decide anything.** It is given the real
audit trail, the real transcript and the real cart for one attempt, and
asked to narrate them. It has no tools, cannot act, and is told plainly to
say it does not know rather than fill a gap - because the one thing worse
than no explanation is a fluent invented one attached to a real customer's
money.
"""
import json
import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.auth import get_current_merchant
from app.core.config import settings
from app.db.database import run_db_async
from app.db.repositories import audit as audit_repo
from app.db.repositories import checkouts as checkouts_repo
from app.db.repositories import conversation_turns as turns_repo
from app.db.repositories import recovery_attempts as recovery_attempts_repo
from app.gateway.event_bus import bus
from app.services import outreach_guards
from app.services.identity_service import identity_service

logger = logging.getLogger(__name__)
actions_router = APIRouter()


async def _load_owned_attempt(recovery_attempt_id: str, merchant_id: str) -> Dict[str, Any]:
    attempt = await run_db_async(recovery_attempts_repo.get_recovery_attempt, recovery_attempt_id)
    if not attempt or attempt["merchant_id"] != merchant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such recovery attempt.")
    return attempt


@actions_router.post("/dashboard/recoveries/{recovery_attempt_id}/retry")
async def retry_recovery(
    recovery_attempt_id: str, current_merchant: dict = Depends(get_current_merchant)
) -> Dict[str, Any]:
    """Try this cart again, through every gate that ran the first time.

    Answers before doing anything, because a merchant pressing this wants
    to know what will happen rather than to be told "queued" and left to
    guess:

      - already paid  -> refuses, and says so. There is nothing to recover.
      - a hard stop   -> refuses, and names the stop. Quiet hours is the
                         common one, and it is not an error.
      - no consent    -> refuses. This is the one a retry must never dodge.
      - otherwise     -> republishes, and the normal pipeline takes over.
    """
    merchant_id = current_merchant["merchant_id"]
    attempt = await _load_owned_attempt(recovery_attempt_id, merchant_id)
    checkout_id = attempt.get("checkout_id")
    customer_id = attempt.get("customer_id")

    checkout = await run_db_async(checkouts_repo.get_checkout, checkout_id) if checkout_id else None
    if not checkout:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="That cart no longer exists.")

    if checkout.get("status") == "paid":
        return {
            "started": False,
            "reason": "already_paid",
            "detail": "This cart has been paid. There is nothing left to recover.",
        }

    channels = await identity_service.reachable_channels(merchant_id, customer_id) if customer_id else []
    if not channels:
        return {
            "started": False,
            "reason": "no_consent",
            "detail": (
                "This customer has no channel we are allowed to contact them on. "
                "If they opted out, that is permanent and a retry cannot override it."
            ),
        }

    allowed, stop_reason = await run_db_async(outreach_guards.check_all, merchant_id, checkout_id)
    if not allowed:
        code = outreach_guards.stop_code(stop_reason)
        return {
            "started": False,
            "reason": code,
            "detail": f"A hard stop is in force: {stop_reason}. It will be retried automatically once that clears.",
        }

    await bus.publish(
        event_type="checkout.payment_failed",
        payload={
            "checkout_id": checkout_id,
            "customer_id": customer_id,
            "amount": (checkout.get("amount_paise") or 0) / 100.0,
            "amount_paise": checkout.get("amount_paise"),
            "currency": checkout.get("currency", "INR"),
            "retried_by_merchant": True,
        },
        correlation_id=checkout_id,
        merchant_id=merchant_id,
        # No idempotency key. Every other producer of this event is a
        # machine that may fire twice for one real occurrence; this one is a
        # person who pressed a button because they meant it, and silently
        # dropping their second press as a duplicate would look broken.
        # The guards above are what stop it becoming a nuisance.
    )
    logger.info(f"Merchant {merchant_id} retried recovery for checkout {checkout_id}.")
    return {
        "started": True,
        "reason": "",
        "detail": "Recovery restarted. It runs through the same checks as an automatic attempt.",
    }


def _build_explain_context(attempt: Dict[str, Any], checkout: Dict[str, Any],
                           audit_rows: list, transcript: list) -> Dict[str, Any]:
    """Everything the explanation is allowed to be about. Nothing else
    reaches the model, so it cannot narrate a cart or a customer that is
    not this one."""
    try:
        line_items = json.loads(checkout.get("line_items") or "[]")
        items = [li.get("name") for li in line_items if isinstance(li, dict) and li.get("name")]
    except (TypeError, ValueError):
        items = []

    steps = []
    for row in audit_rows:
        result = row.get("result")
        try:
            result = json.loads(result) if isinstance(result, str) else result
        except (TypeError, ValueError):
            result = {}
        steps.append({
            "tool": row.get("action"),
            "decision": row.get("decision"),
            "reason": (result or {}).get("reason"),
            "requested_percent": (result or {}).get("requested_percent"),
            "approved_percent": (result or {}).get("approved_percent"),
        })

    return {
        "state": attempt.get("state"),
        "channel": attempt.get("channel"),
        "cart_items": items,
        "cart_total_inr": (checkout.get("amount_paise") or 0) / 100.0,
        "why_the_payment_failed": checkout.get("failure_class"),
        "bank_said": checkout.get("error_description"),
        "approved_discount_percent": attempt.get("approved_discount_percent"),
        "final_amount_inr": (attempt.get("final_amount_paise") or 0) / 100.0 or None,
        "recovered_inr": (attempt.get("attributed_revenue_paise") or 0) / 100.0 or None,
        "promised_date": str(attempt["promised_at"])[:10] if attempt.get("promised_at") else None,
        "promise_words": attempt.get("promise_words"),
        "tool_calls": steps,
        "conversation": [
            {"who": t["speaker"], "said": t["text"], "heard_confidence": t.get("stt_confidence")}
            for t in transcript
        ],
    }


@actions_router.post("/dashboard/recoveries/{recovery_attempt_id}/explain")
async def explain_recovery(
    recovery_attempt_id: str, current_merchant: dict = Depends(get_current_merchant)
) -> Dict[str, Any]:
    """Narrate one recovery from its own rows.

    Grounded the same way merchant_intelligence is: the context handed to
    the model is built from this merchant's own persisted data, so there is
    nothing in scope to leak and nothing to invent from. If the model is
    unavailable the endpoint says so rather than degrading into a guess -
    an explanation is worth nothing if the reader cannot tell whether it
    was read off the record or made up.
    """
    merchant_id = current_merchant["merchant_id"]
    attempt = await _load_owned_attempt(recovery_attempt_id, merchant_id)
    checkout = await run_db_async(checkouts_repo.get_checkout, attempt["checkout_id"])
    audit_rows = await run_db_async(audit_repo.get_audit_trail_for_correlation, recovery_attempt_id)
    transcript = await run_db_async(turns_repo.list_for_attempt, recovery_attempt_id)

    if not checkout:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="That cart no longer exists.")

    context = _build_explain_context(attempt, checkout, audit_rows, transcript)

    if not settings.OPENROUTER_API_KEY:
        return {
            "explanation": "",
            "degraded": True,
            "detail": "No language model is configured, so there is nothing to write this from.",
        }

    prompt = (
        "You are explaining ONE payment-recovery attempt to the merchant who owns it. "
        "Everything you know is in the JSON below - it is that merchant's own data. "
        "Write 3 to 5 short sentences in plain English, no bullet points, no headings.\n\n"
        "Cover, in this order and only where the data supports it: what the customer was buying and "
        "for how much, why their payment did not go through, what the agent did about it, and how it "
        "ended. If a discount was requested and reduced, say what was asked for, what was approved, "
        "and that a policy limit did it - that is the most useful sentence you can write.\n\n"
        "Never invent a fact that is not below. If something is missing say so plainly, in a few "
        "words, and move on. Do not speculate about what the customer was thinking. Do not give "
        "advice unless the data points at something specific.\n\n"
        f"{json.dumps(context, indent=1, default=str)}"
    )

    try:
        from openai import AsyncOpenAI
        from app.core.net import ipv4_client

        client = AsyncOpenAI(
            base_url=settings.LLM_BASE_URL, api_key=settings.OPENROUTER_API_KEY, http_client=ipv4_client()
        )
        response = await client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            timeout=20.0,
        )
        text = (response.choices[0].message.content or "").strip()
    except Exception as e:
        logger.warning(f"explain_recovery failed for {recovery_attempt_id}: {e}")
        return {
            "explanation": "",
            "degraded": True,
            "detail": "The explanation could not be generated just now. The audit trail below is unaffected.",
        }

    return {"explanation": text, "degraded": False, "detail": ""}
