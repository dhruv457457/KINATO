import logging
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Dict, Any, List, Optional
from app.gateway.event_bus import bus
from app.services.policy_engine import policy_engine
from app.services.merchant_intelligence import merchant_intel
from app.payments.upi_reserve_pay import upi_reserve_pay
from app.core.auth import get_current_merchant

logger = logging.getLogger(__name__)
router = APIRouter()

class PolicyUpdateRequest(BaseModel):
    max_discount_percent: Optional[float] = None
    minimum_margin_percent: Optional[float] = None
    calling_start_hour: Optional[int] = None
    calling_end_hour: Optional[int] = None
    voice_persona: Optional[str] = None
    bundle_discount_percent: Optional[float] = None

class MerchantChatRequest(BaseModel):
    question: str

class MandateRequest(BaseModel):
    customer_email: str = ""
    customer_phone: str = ""
    daily_limit_inr: float = 10000.0

@router.get("/dashboard/state")
async def get_dashboard_state(current_merchant: dict = Depends(get_current_merchant)) -> Dict[str, Any]:
    """
    Exposes aggregated system state, hero KPIs, live recovery pipeline,
    customer intelligence, and external AI buyer activity for the
    authenticated merchant.
    """
    merchant_id = current_merchant["merchant_id"]

    # TODO(event scoping): bus.get_recent_events() is still global across all
    # merchants (see app/gateway/event_bus.py) - it hasn't been filtered by
    # merchant_id yet. Until the ingestion work (real per-merchant events)
    # lands, this shows the whole process's event log, not just this
    # merchant's. Tracked, not silently hidden.
    recent_events = bus.get_recent_events(limit=300)

    # 1. Revenue Metrics
    attributed_revenue = sum(
        e["payload"].get("amount", 0)
        for e in recent_events
        if e["event_type"] == "revenue.attributed"
    )

    abandoned_value = sum(
        e["payload"].get("amount", 0)
        for e in recent_events
        if e["event_type"] == "checkout.abandoned"
    )

    active_recoveries = len([e for e in recent_events if e["event_type"] == "call.started"])
    completed_recoveries = len([e for e in recent_events if e["event_type"] == "payment.succeeded"])

    revenue_at_risk = max(0.0, abandoned_value - attributed_revenue)
    # No fabricated benchmark: with zero recoveries attempted there is no win
    # rate to report. Return None and let callers render "-", not a made-up number.
    win_rate = (completed_recoveries / active_recoveries * 100) if active_recoveries > 0 else None

    # 2. Extract Latest Customer Intelligence
    latest_intel = next(
        (e["payload"] for e in reversed(recent_events) if e["event_type"] == "customer.understood"),
        None
    )

    # 3. Extract Latest AI Commerce Rejection
    latest_ai_rejection = next(
        (e["payload"] for e in reversed(recent_events) if e["event_type"] == "ai_commerce.intent_rejected"),
        None
    )

    # 4. Active Policy
    current_policy = policy_engine.get_policy(merchant_id)

    return {
        "hero": {
            "revenue_at_risk": revenue_at_risk,
            "kinato_attributed_revenue": attributed_revenue,
            "active_recoveries": active_recoveries,
            "completed_recoveries": completed_recoveries,
            "win_rate_percent": round(win_rate, 1) if win_rate is not None else None
        },
        "events": recent_events,
        "latest_intel": latest_intel,
        "latest_ai_rejection": latest_ai_rejection,
        "policy": current_policy
    }

@router.get("/merchant/policy")
async def get_merchant_policy(current_merchant: dict = Depends(get_current_merchant)):
    return policy_engine.get_policy(current_merchant["merchant_id"])

@router.post("/merchant/policy")
async def update_merchant_policy(payload: PolicyUpdateRequest, current_merchant: dict = Depends(get_current_merchant)):
    updates = payload.dict(exclude_unset=True)
    updated = policy_engine.update_policy(current_merchant["merchant_id"], updates)
    return {"status": "success", "policy": updated}

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
    return upi_reserve_pay.create_mandate(
        business_id=current_merchant["merchant_id"],
        customer_email=payload.customer_email,
        customer_phone=payload.customer_phone,
        daily_limit_inr=payload.daily_limit_inr,
    )

@router.get("/commerce/mandate")
async def get_ai_buyer_mandate(current_merchant: dict = Depends(get_current_merchant)):
    mandate = upi_reserve_pay.get_mandate_status(current_merchant["merchant_id"])
    if not mandate:
        return {"status": "NONE", "message": "No active autonomous-payment mandate for this merchant."}
    return mandate

@router.post("/commerce/mandate/{mandate_id}/revoke")
async def revoke_ai_buyer_mandate(mandate_id: str, current_merchant: dict = Depends(get_current_merchant)):
    """Merchant can revoke the AI agent's autonomous payment authority at any time."""
    return upi_reserve_pay.revoke_mandate(mandate_id)
