import logging
from typing import Dict, Any, List, Optional

from app.db.repositories import policies as policies_repo

logger = logging.getLogger(__name__)


class OfferPolicyEngine:
    """
    Deterministic rule engine. LLMs request offers, but this service evaluates
    them against strict merchant constraints (max discount, minimum margin, eligibility).
    Returns EXACT decisions: ALLOW, MODIFY, or DENY.

    Policies are DB-backed (app/db/repositories/policies.py) - this replaces
    the old in-memory MERCHANT_POLICIES dict that had exactly one entry
    ("jiva_demo") and silently coerced any unknown merchant_id into it via
    `.get(merchant_id, MERCHANT_POLICIES["jiva_demo"])`. get_policy() now
    RAISES (PolicyNotFoundError) for a merchant that doesn't exist - the
    "jiva_demo" default below is an explicit, visible default parameter for
    still-unauthenticated call sites (dashboard routes not yet wired to a
    real session - see Day 4), backed by a real seeded row, not a silent
    fallback for garbage input.
    """

    @staticmethod
    def get_policy(merchant_id: str = "jiva_demo") -> Dict[str, Any]:
        return policies_repo.get_policy(merchant_id)

    @staticmethod
    def update_policy(merchant_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        updated = policies_repo.update_policy(merchant_id, updates)
        logger.info(f"✅ Merchant Policy Updated for {merchant_id}: {updates}")
        return updated

    @staticmethod
    def evaluate(
        requested_discount: float,
        merchant_policy: Optional[Dict[str, Any]] = None,
        customer_context: Optional[Dict[str, Any]] = None,
        cart_details: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Evaluates a candidate discount against hard merchant rules.
        """
        if merchant_policy is None:
            merchant_policy = OfferPolicyEngine.get_policy()
        if customer_context is None:
            customer_context = {}
        if cart_details is None:
            cart_details = {"amount": 3499.0, "cogs": 1749.5}

        logger.info(f"🛡️ [PolicyEngine] Evaluating requested discount: {requested_discount}%")

        # 1. Check Product Exclusions
        excluded = merchant_policy.get("excluded_products", [])
        product_ids = cart_details.get("product_ids", [])
        if any(pid in excluded for pid in product_ids):
            logger.warning("Policy DENY: Cart contains excluded product.")
            return {"decision": "DENY", "reason": "product_excluded", "approved_discount": 0.0}

        # 2. Check Margin Constraints
        max_discount = float(merchant_policy.get("max_discount_percent", 10.0))
        min_margin = float(merchant_policy.get("minimum_margin_percent", 15.0))

        cart_total = float(cart_details.get("amount", 3499.0))
        cogs = float(cart_details.get("cogs", cart_total * 0.5))

        if cart_total <= 0:
            return {"decision": "DENY", "reason": "invalid_cart_total", "approved_discount": 0.0}

        max_allowed_by_margin = ((cart_total - cogs) / cart_total) * 100 - min_margin

        if max_allowed_by_margin <= 0:
            logger.warning("Policy DENY: Margin too low to offer any discount.")
            return {"decision": "DENY", "reason": "margin_too_low", "approved_discount": 0.0}

        # 3. Calculate Final Approved Discount
        effective_max_discount = min(max_discount, max_allowed_by_margin)
        effective_max_discount = max(0.0, effective_max_discount)

        if requested_discount <= effective_max_discount:
            return {
                "decision": "ALLOW",
                "requested_discount": requested_discount,
                "approved_discount": requested_discount,
                "reason": "within_margin_and_discount_limits"
            }
        else:
            return {
                "decision": "MODIFY",
                "requested_discount": requested_discount,
                "approved_discount": effective_max_discount,
                "reason": "merchant_max_discount_or_margin_constraint"
            }

policy_engine = OfferPolicyEngine()
