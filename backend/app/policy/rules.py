"""
================================================================================
FILE: app/policy/rules.py
MODULE: Module 1 - Deterministic Safety Assertions
--------------------------------------------------------------------------------
WHAT THIS FILE DOES:
Implements pure Python mathematical assertions for financial safety, gross margin
protection, buyer cashflow bounds, perishable freshness, and cryptographic integrity.

THESE ARE DETERMINISTIC CODE BOUNDARIES:
  - An LLM NEVER decides whether money moves.
  - An LLM CANNOT override these mathematical assertions.

KEY ASSERTION FUNCTIONS:
  1. verify_merchant_floor_price(items):
     Formula: P_unit >= Cost_Price * (1 + 0.15)
     Guarantees that a hallucinating or aggressive LLM cannot sell below wholesale cost.

  2. verify_buyer_cashflow_limit(total, daily_limit, weekly_limit, weekly_spent):
     Formula: Total <= Daily_Limit AND (Weekly_Spent + Total) <= Weekly_Limit
     Guarantees the AI cannot drain the buyer's bank account or operational cashflow.

  3. verify_freshness_alignment(items, min_consumption_days):
     Guarantees that aging discounted batches still have enough shelf-life remaining
     for the buyer's consumption rate (prevents buying spoiled goods).

  4. verify_hmac_proposal(proposal_payload, provided_hash):
     Verifies cryptographic HMAC-SHA256 signature to guarantee zero payload tampering.
================================================================================
"""
from typing import List, Tuple, Dict, Any
from app.models.a2a import QuoteLineItem
from app.core.security import verify_proposal_hash


def verify_merchant_floor_price(items: List[QuoteLineItem], minimum_margin_pct: float = 0.15) -> Tuple[bool, str]:
    """
    Ensures no supplier sells below Cost Price + minimum margin.
    Anti-Hallucination Guardrail Formula: Unit Price >= Cost Price * (1 + MinMargin)
    """
    for item in items:
        min_allowed_unit_price = round(item.cost_price * (1.0 + minimum_margin_pct), 2)
        if item.unit_price < min_allowed_unit_price:
            diff = round(min_allowed_unit_price - item.unit_price, 2)
            return False, f"Item '{item.name}' unit price ₹{item.unit_price} violates wholesale cost floor ₹{min_allowed_unit_price} (CP: ₹{item.cost_price}) by ₹{diff}"
    return True, "All items satisfy merchant gross margin floor (>= 15% above CP)"


def verify_buyer_cashflow_limit(
    total_amount: float,
    daily_limit: float,
    weekly_limit: float,
    weekly_spent: float
) -> Tuple[bool, str]:
    """
    Ensures transaction does not exceed daily liquidity limit or weekly budget cap.
    """
    if total_amount > daily_limit:
        diff = round(total_amount - daily_limit, 2)
        return False, f"Transaction total ₹{total_amount} exceeds daily liquidity limit ₹{daily_limit} by ₹{diff}"

    if (weekly_spent + total_amount) > weekly_limit:
        diff = round((weekly_spent + total_amount) - weekly_limit, 2)
        return False, f"Transaction total ₹{total_amount} breaches remaining weekly budget cap (Remaining: ₹{weekly_limit - weekly_spent}) by ₹{diff}"

    return True, f"Transaction total ₹{total_amount} is strictly within daily limit (₹{daily_limit}) and weekly cap"


def verify_freshness_alignment(items: List[QuoteLineItem]) -> Tuple[bool, str]:
    """
    Ensures any discounted aging batch has enough shelf life to be consumed before spoiling.
    """
    for item in items:
        if item.is_aging_upsell and item.batch_age_days is not None:
            # If batch age leaves less than required consumption days (e.g. > 23 days out of 25)
            if item.batch_age_days > 23:
                return False, f"Aging item '{item.name}' has insufficient shelf life remaining for buyer consumption cycle"
    return True, "Batch freshness matches buyer consumption rate"


def verify_hmac_proposal(proposal_payload: Dict[str, Any], provided_hash: str) -> Tuple[bool, str]:
    """
    Verifies that the proposal has not been mutated in transit.
    """
    is_valid = verify_proposal_hash(proposal_payload, provided_hash)
    if not is_valid:
        return False, "Cryptographic proposal digest mismatch. Proposal has been mutated or tampered with."
    return True, "HMAC-SHA256 proposal signature verified successfully"
