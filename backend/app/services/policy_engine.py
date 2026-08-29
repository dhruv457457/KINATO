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
    RAISES (PolicyNotFoundError) for a merchant that doesn't exist.

    get_policy() takes a REQUIRED merchant_id. It previously defaulted to
    "jiva_demo" for "dashboard routes not yet wired to a real session" -
    those routes have all been on get_current_merchant since Day 4, so that
    default outlived its reason and became a tenancy landmine: any call site
    that forgot the argument would silently read another merchant's discount
    ceiling and margin floor.
    """

    @staticmethod
    def get_policy(merchant_id: str) -> Dict[str, Any]:
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

        merchant_policy and cart_details are REQUIRED. They used to have
        silent defaults that were both genuinely dangerous:

          - merchant_policy fell back to get_policy() with no merchant_id,
            i.e. another tenant's discount ceiling and margin floor.
          - cart_details fell back to {"amount": 3499.0, "cogs": 1749.5} -
            the fabricated cart the rebuild plan called out ("every recovery
            prices a fictional cart"). It was removed from call_orchestrator
            but survived here, so a caller that omitted it would have sized
            a real discount against invented money.

        Every real call site already passes both, so these defaults were
        never exercised - they were landmines, not behaviour. Failing loudly
        is the only safe thing to do in a money path.
        """
        if merchant_policy is None:
            raise ValueError(
                "evaluate() requires an explicit merchant_policy - refusing to "
                "price a discount against an unspecified merchant's rules."
            )
        if cart_details is None:
            raise ValueError(
                "evaluate() requires explicit cart_details - refusing to price a "
                "discount against a fabricated cart."
            )
        if customer_context is None:
            customer_context = {}

        logger.info(f"🛡️ [PolicyEngine] Evaluating requested discount: {requested_discount}%")

        max_discount = float(merchant_policy.get("max_discount_percent", 10.0))
        min_margin = float(merchant_policy.get("minimum_margin_percent", 15.0))
        # What the agent may give away on its own authority, in rupees.
        #
        # This column has existed since the schema was written, is editable
        # on the Policies screen under the words "discounts that cost less
        # than this need no human review", and was read by NOTHING. It was
        # even handed to the model in get_policy_limits - so the agent knew
        # about a limit it was not bound by, which is the worst of both:
        # the merchant believed a control existed and the model was told a
        # number that constrained it not at all. Exactly FINDINGS #4, in a
        # different field.
        #
        # Zero is the schema default and therefore what every existing
        # merchant has, so zero means "not configured", never "approve
        # nothing" - reading it literally would have blocked every discount
        # on the platform.
        approval_cap_inr = float(merchant_policy.get("auto_approval_threshold_inr") or 0.0)

        # Every decision below carries the numbers it was made from. A
        # refusal a merchant cannot check is just an assertion, and this is
        # the one screen where the policy engine is visibly overruling the
        # model - "asked 40%, ceiling 10%" explains itself; a bare
        # "constraint" does not.
        def _decide(decision: str, reason: str, approved: float) -> Dict[str, Any]:
            return {
                "decision": decision,
                "reason": reason,
                "requested_discount": requested_discount,
                "approved_discount": approved,
                "ceiling_percent": max_discount,
                "margin_floor_percent": min_margin,
                "auto_approval_cap_inr": approval_cap_inr or None,
            }

        # 1. Check Product Exclusions
        excluded = merchant_policy.get("excluded_products", [])
        product_ids = cart_details.get("product_ids", [])
        if any(pid in excluded for pid in product_ids):
            logger.warning("Policy DENY: Cart contains excluded product.")
            return _decide("DENY", "REJECTED_SKU_EXCLUDED", 0.0)

        # 2. Check Margin Constraints
        cart_total = float(cart_details.get("amount", 3499.0))
        cogs = float(cart_details.get("cogs", cart_total * 0.5))

        if cart_total <= 0:
            return _decide("DENY", "REJECTED_INVALID_CART_TOTAL", 0.0)

        max_allowed_by_margin = ((cart_total - cogs) / cart_total) * 100 - min_margin

        if max_allowed_by_margin <= 0:
            logger.warning("Policy DENY: Margin too low to offer any discount.")
            return _decide("DENY", "REJECTED_MARGIN_FLOOR", 0.0)

        # 3. Calculate Final Approved Discount
        #
        # Which of the two limits actually bound is recorded rather than
        # discarded. `min(max_discount, max_allowed_by_margin)` computes
        # both and then throws away the answer to the only question a
        # merchant asks when they see a reduced offer - "was that my
        # discount cap, or my margin floor?" The old code returned the
        # single string "merchant_max_discount_or_margin_constraint" for
        # both, so REJECTED_CEILING and REJECTED_MARGIN_FLOOR were
        # indistinguishable despite being one line apart from being known.
        # A third limit, in rupees rather than percent, so it has to be
        # converted before it can be compared with the other two.
        limits = [(max_discount, "REJECTED_CEILING"), (max_allowed_by_margin, "REJECTED_MARGIN_FLOOR")]
        if approval_cap_inr > 0:
            limits.append(((approval_cap_inr / cart_total) * 100, "REJECTED_APPROVAL_THRESHOLD"))

        effective_max_discount, binding_reason = min(limits, key=lambda pair: pair[0])
        effective_max_discount = max(0.0, effective_max_discount)

        if requested_discount <= effective_max_discount:
            return _decide("ALLOW", "APPROVED", requested_discount)

        return _decide("MODIFY", binding_reason, effective_max_discount)

policy_engine = OfferPolicyEngine()
