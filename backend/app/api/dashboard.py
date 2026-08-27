import json
import logging
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import Dict, Any, List, Optional
from app.gateway.event_bus import bus
from app.services.policy_engine import policy_engine
from app.services.merchant_intelligence import merchant_intel
from app.payments.upi_reserve_pay import upi_reserve_pay
from app.core.auth import get_current_merchant
from app.db.repositories import recovery_attempts as recovery_attempts_repo
from app.db.repositories import audit as audit_repo

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
    stats = recovery_attempts_repo.summary_stats(merchant_id)

    return {
        "revenue_at_risk_paise": stats["revenue_at_risk_paise"],
        "revenue_recovered_paise": stats["recovered_paise"],
        "active_recoveries": stats["active_count"],
        "recovered_count": stats["recovered_count"],
        "total_attempts": stats["total_attempts"],
        "opted_out_count": stats["opted_out_count"],
        "call_failed_count": stats["call_failed_count"],
        "abandoned_count": stats["abandoned_count"],
        "recovery_rate_pct": stats["recovery_rate_pct"],
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

    return {"recovery_attempt": attempt, "audit_trail": audit_rows}

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
