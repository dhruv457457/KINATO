import json
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import Dict, Any, List, Optional
from app.services.merchant_intelligence import merchant_intel
from app.payments.spend_mandate import spend_mandate_service
from app.core.auth import get_current_merchant
from app.db.database import run_db_async
from app.db.repositories import checkouts as checkouts_repo
from app.db.repositories import conversation_turns as turns_repo
from app.db.repositories import recovery_attempts as recovery_attempts_repo
from app.db.repositories import audit as audit_repo
from app.db.repositories import customers as customers_repo
from app.db.repositories import products as products_repo
from app.db.repositories import events as events_repo
from app.services.identity_service import identity_service
from app.services import timing_planner
from app.services.policy_engine import policy_engine

logger = logging.getLogger(__name__)
router = APIRouter()

class MerchantChatRequest(BaseModel):
    question: str

class MandateRequest(BaseModel):
    customer_email: str = ""
    customer_phone: str = ""
    daily_limit_inr: float = 10000.0

class ProductVisibilityRequest(BaseModel):
    visible: bool

@router.get("/dashboard/overview")
async def get_dashboard_overview(current_merchant: dict = Depends(get_current_merchant)) -> Dict[str, Any]:
    """
    Real, DB-backed KPIs for this merchant only. Replaces the old
    /dashboard/state, which read bus.get_recent_events() - an in-memory
    log that isn't scoped per merchant and is empty after every process
    restart (a real problem on Railway, where a redeploy restarts the
    process). A merchant with zero activity gets real zeros/None here,
    never a fabricated benchmark.
    """
    merchant_id = current_merchant["merchant_id"]

    # Sequential on purpose, and this was measured rather than assumed.
    #
    # These three reads are independent, so running them concurrently through
    # run_db_async looks like the obvious win. It is 80% SLOWER against this
    # database: 2507ms versus 1387ms, best-of-3 with an already-warm pool.
    # Sequential calls reuse one hot connection, while concurrency forces
    # three separate ones, and establishing those costs more here than the
    # overlapping round trips save.
    #
    # "Parallelise independent IO" is good advice that happens to be wrong in
    # this case. Left sequential until a measurement says otherwise.
    stats = recovery_attempts_repo.summary_stats(merchant_id)
    blocked_reasons = events_repo.count_blocked_reasons(merchant_id)
    rule_breaks = events_repo.count_rule_breaks(merchant_id)
    # Two more sequential reads, for the same measured reason as above.
    daily = recovery_attempts_repo.daily_series(merchant_id)
    breakdown = recovery_attempts_repo.channel_breakdown(merchant_id)

    return {
        "revenue_at_risk_paise": stats["revenue_at_risk_paise"],
        "revenue_recovered_paise": stats["recovered_paise"],
        "active_recoveries": stats["active_count"],
        "recovered_count": stats["recovered_count"],
        "total_attempts": stats["total_attempts"],
        "opted_out_count": stats["opted_out_count"],
        "call_failed_count": stats["call_failed_count"],
        # Recoveries a hard stop refused before dialling. Reported apart
        # from dial failures because they are the opposite thing: a rule
        # the merchant configured doing its job, not a broken phone number.
        "blocked_count": stats["blocked_count"],
        "abandoned_count": stats["abandoned_count"],
        "recovery_rate_pct": stats["recovery_rate_pct"],
        # Customers who committed to a date. Outreach is paused for them -
        # they are neither lost nor recovered, and lumping them into either
        # would misrepresent both numbers.
        "promised_count": stats["promised_count"],
        "promised_paise": stats["promised_paise"],
        # Why recoveries never started: real `recovery.blocked` events. A
        # payment failed (so there IS money on the table) but Kinato
        # deliberately stayed silent - no contact details on file, or
        # Razorpay's own rail was degraded. Without this the merchant just
        # sees a smaller recovered number and no reason for it.
        "blocked_reasons": blocked_reasons,
        # Outreach that happened DESPITE a hard stop (already paid, no
        # consent, quiet hours, discount over ceiling). Not a metric to
        # optimise - any non-zero value means a guarantee the merchant
        # relies on was broken.
        "rule_breaks": rule_breaks,
        # Recovered money per day for the last fortnight, one row per day
        # including the empty ones - a gap in a chart and a zero in a chart
        # say different things, and only one of them is true.
        "daily_series": daily,
        # Outreach is not one thing. A single recovery count cannot tell a
        # phone operation from an email one, and they cost very differently.
        "by_channel": breakdown["by_channel"],
        "by_state": breakdown["by_state"],
    }


@router.get("/dashboard/recoveries")
async def list_recoveries(current_merchant: dict = Depends(get_current_merchant)) -> Dict[str, Any]:
    """Real recovery_attempts rows for this merchant, joined with checkout
    amount and customer contact - the Recoveries table's data source."""
    rows = recovery_attempts_repo.list_for_merchant(current_merchant["merchant_id"], limit=100)
    return {"recoveries": rows}


@router.get("/dashboard/recoveries/{recovery_attempt_id}")
async def get_recovery_detail(recovery_attempt_id: str, current_merchant: dict = Depends(get_current_merchant)) -> Dict[str, Any]:
    """The full audit timeline for one recovery attempt - what backs the
    detail drawer: every real tool call (get_cart, check_offer,
    issue_offer, record_opt_out, ...) in order, with its real decision,
    latency, and whether it ran degraded."""
    attempt = recovery_attempts_repo.get_recovery_attempt(recovery_attempt_id)
    if not attempt or attempt["merchant_id"] != current_merchant["merchant_id"]:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such recovery attempt.")

    audit_rows = audit_repo.get_audit_trail_for_correlation(recovery_attempt_id)
    for row in audit_rows:
        for field in ("args", "result"):
            try:
                row[field] = json.loads(row[field]) if isinstance(row[field], str) else row[field]
            except (TypeError, ValueError):
                pass

    try:
        attempt["plan"] = json.loads(attempt["plan"]) if isinstance(attempt.get("plan"), str) else attempt.get("plan")
    except (TypeError, ValueError):
        pass

    # What was actually SAID, alongside what the agent DID. The drawer
    # called itself "the screen the whole product is judged on" while
    # showing every tool call and none of the conversation those calls were
    # a response to - "check_offer returned MODIFY 10%" means very little
    # until you can read the sentence that prompted it.
    transcript = turns_repo.list_for_attempt(recovery_attempt_id)

    # Why the payment failed is a fact about the CHECKOUT, not about any
    # one attempt at recovering it - a second attempt days later diagnoses
    # from the same evidence. The drawer still needs it here, though, so it
    # is joined on rather than duplicated into recovery_attempts: the
    # alternative is two rows that can disagree about the same payment.
    checkout = checkouts_repo.get_checkout(attempt["checkout_id"]) if attempt.get("checkout_id") else None
    attempt["failure_class"] = (checkout or {}).get("failure_class")
    attempt["error_description"] = (checkout or {}).get("error_description")

    # Recomputed here rather than stored anywhere. plan_windows is pure and
    # anchored on the failure's own timestamp, so this returns exactly what
    # the agent was offered on the call - today, or a month from now. A
    # stored copy would be a second version of the same fact, free to drift
    # from the row it came from.
    return {
        "recovery_attempt": attempt,
        "audit_trail": audit_rows,
        "transcript": transcript,
        "timing_plan": _timing_plan_for(checkout, current_merchant["merchant_id"]),
    }


def _timing_plan_for(checkout: Optional[Dict[str, Any]], merchant_id: str) -> Dict[str, Any]:
    """The follow-up windows this failure earns, for the drawer.

    Returns `windows: []` with a reason rather than nothing at all when the
    answer is "never". A stopping rule the merchant cannot see is a stopping
    rule they have to take on trust.
    """
    if not checkout:
        return {"windows": [], "no_window_reason": None}
    anchor = checkout.get("abandoned_at") or checkout.get("started_at")
    if not anchor:
        return {"windows": [], "no_window_reason": None}
    if isinstance(anchor, str):
        try:
            anchor = datetime.fromisoformat(anchor.replace("Z", "+00:00"))
        except ValueError:
            return {"windows": [], "no_window_reason": None}
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=timezone.utc)

    policy = policy_engine.get_policy(merchant_id)
    start = int(policy.get("calling_start_hour", 10))
    end = int(policy.get("calling_end_hour", 20))
    windows = timing_planner.plan_windows(
        checkout.get("failure_class"), anchor, calling_start_hour=start, calling_end_hour=end
    )
    return {
        "windows": [
            {
                "at": w.at.isoformat(),
                "say_window": w.say_window,
                "say_reason": w.say_reason,
                "reason_code": w.reason_code,
                "is_past": w.is_past,
            }
            for w in windows
        ],
        "no_window_reason": (
            "This decline will not clear on the same card, so no follow-up time is suggested."
            if not windows and checkout.get("failure_class") == "HARD_DECLINE"
            else None
        ),
        "calling_hours": f"{start:02d}:00-{end:02d}:00 IST",
    }


@router.get("/dashboard/activity")
async def get_activity(current_merchant: dict = Depends(get_current_merchant)) -> Dict[str, Any]:
    """Every real tool call across every recovery attempt for this
    merchant, most recent first - the same audit_log rows the Recoveries
    drawer shows per-attempt, just unfiltered and chronological. This is
    what makes every money decision the AI ever made queryable, not just
    the ones a merchant happens to click into."""
    rows = audit_repo.recent_audit(current_merchant["merchant_id"], limit=200)
    for row in rows:
        for field in ("args", "result"):
            try:
                row[field] = json.loads(row[field]) if isinstance(row[field], str) else row[field]
            except (TypeError, ValueError):
                pass
    return {"activity": rows}


@router.get("/dashboard/customers")
async def list_customers(current_merchant: dict = Depends(get_current_merchant)) -> Dict[str, Any]:
    """Real customers for this merchant, each with their real, current
    voice-channel consent status."""
    rows = customers_repo.list_for_merchant(current_merchant["merchant_id"])
    return {"customers": rows}


@router.post("/dashboard/customers/{customer_id}/revoke-consent")
async def revoke_customer_consent(customer_id: str, current_merchant: dict = Depends(get_current_merchant)) -> Dict[str, Any]:
    """A merchant-initiated opt-out on a customer's behalf (e.g. a support
    request that didn't come through a live call). Uses the same
    append-only revoke_consent() path as the AI's own record_opt_out tool
    - one real mechanism, two ways to trigger it."""
    customer = customers_repo.get_customer(customer_id)
    if not customer or customer["merchant_id"] != current_merchant["merchant_id"]:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such customer.")
    await identity_service.revoke_consent(current_merchant["merchant_id"], customer_id, channel="voice", source="dashboard_manual")
    return {"status": "revoked"}

@router.get("/dashboard/catalog")
async def list_catalog(current_merchant: dict = Depends(get_current_merchant)) -> Dict[str, Any]:
    """Real products for this merchant - price, cogs_paise, margin, and
    whether external AI buyers (via /mcp) can see it. Backed by the same
    `products` table onboarding's CSV upload writes to; a fresh merchant
    with nothing uploaded gets a real empty list, not seeded rows."""
    rows = products_repo.list_products(current_merchant["merchant_id"])
    return {"products": rows}


@router.post("/dashboard/catalog/{product_id}/visibility")
async def set_catalog_visibility(
    product_id: str, payload: ProductVisibilityRequest, current_merchant: dict = Depends(get_current_merchant)
) -> Dict[str, Any]:
    """Merchant-controlled toggle for whether one product can be discovered
    by external AI buyers through the AI Commerce / MCP surface."""
    updated = products_repo.set_ai_buyer_visibility(current_merchant["merchant_id"], product_id, payload.visible)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such product.")
    return {"product": updated}


# NOTE: the merchant policy routes used to live here as well, duplicating
# GET /api/merchant/policy (and adding a POST variant nothing called). Both
# files registered the same path, so FastAPI's first-match-wins silently
# made merchant_settings.get_policy unreachable dead code while the same
# resource was split across two modules with different verbs. They now live
# only in app/api/merchant_settings.py (GET + PUT), which owns everything
# else under /api/merchant. policy_engine.get_policy was a bare passthrough
# to policies_repo.get_policy, so the served response shape is unchanged.

@router.post("/merchant-intel/chat")
async def merchant_chat(payload: MerchantChatRequest, current_merchant: dict = Depends(get_current_merchant)):
    result = await merchant_intel.query_insights(payload.question, current_merchant["merchant_id"])
    return result

@router.post("/commerce/mandate")
async def authorize_ai_buyer_mandate(payload: MandateRequest, current_merchant: dict = Depends(get_current_merchant)):
    """
    Merchant-only action: authorizes external AI buyers (Claude, Codex, etc.)
    to transact autonomously up to a daily cap via Razorpay UPI Reserve Pay
    (spec's "Path A"). One-time authorization; every subsequent autonomous
    purchase is capped and audited without further human approval.
    """
    return spend_mandate_service.create_mandate(
        business_id=current_merchant["merchant_id"],
        customer_email=payload.customer_email,
        customer_phone=payload.customer_phone,
        daily_limit_inr=payload.daily_limit_inr,
    )

@router.get("/commerce/mandate")
async def get_ai_buyer_mandate(current_merchant: dict = Depends(get_current_merchant)):
    mandate = spend_mandate_service.get_mandate_status(current_merchant["merchant_id"])
    if not mandate:
        return {"status": "NONE", "message": "No active autonomous-payment mandate for this merchant."}
    return mandate

@router.post("/commerce/mandate/{mandate_id}/revoke")
async def revoke_ai_buyer_mandate(mandate_id: str, current_merchant: dict = Depends(get_current_merchant)):
    """Merchant can revoke the AI agent's autonomous payment authority at any time."""
    return spend_mandate_service.revoke_mandate(mandate_id)
