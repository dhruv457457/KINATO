"""
================================================================================
FILE: app/policy/engine.py
MODULE: Module 1 - Policy Engine & Guardrails Orchestrator
--------------------------------------------------------------------------------
WHAT THIS FILE DOES:
Acts as the central Deterministic Safety Gate between the AI negotiation layer
and the Razorpay financial execution layer.

It executes the 4 core assertion rules:
  1. Merchant Floor Price Rule (Gross margin protection).
  2. Buyer Cashflow & Liquidity Rule (Daily & weekly spend caps).
  3. Freshness & Consumption Match Rule (Shelf-life alignment).
  4. Cryptographic HMAC Digest Rule (Tamper detection).

DECISION MATRIX:
  - If any assertion fails:
    -> Sets status = BLOCKED or INVALIDATED.
    -> allowed_execution = False.
    -> Generates an Actionable Suggestion (e.g. "Short by ₹180. Adjust budget or reduce qty by 20%").
  - If all assertions pass:
    -> Sets status = PASSED.
    -> allowed_execution = True (Cleared to mint Razorpay Order).

KEY FUNCTIONS:
  - PolicyEngine.evaluate(proposal, buyer_context, check_signature): Evaluates proposal.
================================================================================
"""
from typing import Dict, Any
from app.models.schemas import (
    A2A_FinalOffer,
    BuyerContext,
    PolicyEvaluation,
    PolicyStatus,
    PolicyCheckResult
)
from app.policy.rules import (
    verify_merchant_floor_price,
    verify_buyer_cashflow_limit,
    verify_freshness_alignment,
    verify_hmac_proposal
)


class PolicyEngine:
    """
    Deterministic Policy Engine orchestrating safety assertions.
    """
    @staticmethod
    def evaluate(
        proposal: A2A_FinalOffer,
        buyer_context: BuyerContext,
        check_signature: bool = True
    ) -> PolicyEvaluation:
        checks = []
        is_blocked = False
        is_invalidated = False
        summary_reasons = []
        actionable_suggestion = None

        # ----------------------------------------------------------------------
        # 1. Merchant Floor Price Guardrail
        # ----------------------------------------------------------------------
        floor_pass, floor_msg = verify_merchant_floor_price(proposal.items)
        checks.append(PolicyCheckResult(
            check_name="Merchant Floor Price Check",
            passed=floor_pass,
            details=floor_msg
        ))
        if not floor_pass:
            is_blocked = True
            summary_reasons.append(floor_msg)

        # ----------------------------------------------------------------------
        # 2. Buyer Cashflow & Daily Limit Check
        # ----------------------------------------------------------------------
        cashflow_pass, cashflow_msg = verify_buyer_cashflow_limit(
            total_amount=proposal.final_total,
            daily_limit=buyer_context.daily_budget_limit,
            weekly_limit=buyer_context.weekly_budget_limit,
            weekly_spent=buyer_context.weekly_spent_so_far
        )
        checks.append(PolicyCheckResult(
            check_name="Buyer Cashflow Limit Check",
            passed=cashflow_pass,
            details=cashflow_msg
        ))
        if not cashflow_pass:
            is_blocked = True
            summary_reasons.append(cashflow_msg)
            diff = round(proposal.final_total - buyer_context.daily_budget_limit, 2)
            actionable_suggestion = (
                f"Order exceeds daily budget by ₹{diff}. Recommended: "
                f"Increase daily liquidity limit to ₹{proposal.final_total} or reduce reorder quantity by 25%."
            )

        # ----------------------------------------------------------------------
        # 3. Freshness Alignment Check
        # ----------------------------------------------------------------------
        fresh_pass, fresh_msg = verify_freshness_alignment(proposal.items)
        checks.append(PolicyCheckResult(
            check_name="Perishable Freshness Match",
            passed=fresh_pass,
            details=fresh_msg
        ))
        if not fresh_pass:
            is_blocked = True
            summary_reasons.append(fresh_msg)

        # ----------------------------------------------------------------------
        # 4. HMAC Proposal Signature Integrity Check
        # ----------------------------------------------------------------------
        if check_signature:
            # Canonical dict matching hashing format
            payload_for_hash = {
                "proposal_id": proposal.proposal_id,
                "rfq_id": proposal.rfq_id,
                "winning_supplier_id": proposal.winning_supplier_id,
                "subtotal": proposal.subtotal,
                "total_discount": proposal.total_discount,
                "final_total": proposal.final_total,
                "items": [item.model_dump() for item in proposal.items]
            }
            hash_pass, hash_msg = verify_hmac_proposal(payload_for_hash, proposal.proposal_hash)
            checks.append(PolicyCheckResult(
                check_name="Cryptographic HMAC Signature Verification",
                passed=hash_pass,
                details=hash_msg
            ))
            if not hash_pass:
                is_invalidated = True
                summary_reasons.append(hash_msg)

        # ----------------------------------------------------------------------
        # Final Verdict Determination
        # ----------------------------------------------------------------------
        if is_invalidated:
            status = PolicyStatus.INVALIDATED
            summary = "Policy INVALIDATED: " + "; ".join(summary_reasons)
            allowed = False
        elif is_blocked:
            status = PolicyStatus.BLOCKED
            summary = "Policy BLOCKED: " + "; ".join(summary_reasons)
            allowed = False
        else:
            status = PolicyStatus.PASSED
            summary = "All deterministic policy guardrails satisfied. Cleared for Razorpay execution."
            allowed = True

        return PolicyEvaluation(
            proposal_id=proposal.proposal_id,
            status=status,
            allowed_execution=allowed,
            summary_reason=summary,
            checks=checks,
            actionable_suggestion=actionable_suggestion
        )


policy_engine = PolicyEngine()
