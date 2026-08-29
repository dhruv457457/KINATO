"""
Deterministic tests for the merchant offer policy gate (app/services/policy_engine.py).
LLMs request discounts; this module is the only thing allowed to decide money
terms - replaces the deleted old-domain test_policy_refusal.py.
"""
import pytest
from app.services.policy_engine import OfferPolicyEngine

POLICY = {
    "max_discount_percent": 10.0,
    "minimum_margin_percent": 15.0,
    "excluded_products": ["sku_no_discount"],
}
CART = {"amount": 3499.0, "cogs": 1749.5}  # ~50% margin headroom


def test_allows_discount_within_limits():
    decision = OfferPolicyEngine.evaluate(8.0, POLICY, cart_details=CART)
    assert decision["decision"] == "ALLOW"
    assert decision["approved_discount"] == 8.0


def test_caps_discount_above_merchant_max():
    decision = OfferPolicyEngine.evaluate(50.0, POLICY, cart_details=CART)
    assert decision["decision"] == "MODIFY"
    assert decision["approved_discount"] == POLICY["max_discount_percent"]


def test_denies_excluded_product():
    decision = OfferPolicyEngine.evaluate(
        5.0, POLICY, cart_details={**CART, "product_ids": ["sku_no_discount"]}
    )
    assert decision["decision"] == "DENY"
    assert decision["reason"] == "REJECTED_SKU_EXCLUDED"
    assert decision["approved_discount"] == 0.0


def test_denies_when_margin_too_thin():
    thin_cart = {"amount": 1000.0, "cogs": 900.0}  # 10% margin < 15% minimum
    decision = OfferPolicyEngine.evaluate(5.0, POLICY, cart_details=thin_cart)
    assert decision["decision"] == "DENY"
    assert decision["reason"] == "REJECTED_MARGIN_FLOOR"


def test_denies_invalid_cart_total():
    decision = OfferPolicyEngine.evaluate(5.0, POLICY, cart_details={"amount": 0, "cogs": 0})
    assert decision["decision"] == "DENY"
    assert decision["reason"] == "REJECTED_INVALID_CART_TOTAL"


def test_get_and_update_policy_roundtrip(real_merchant_id):
    original = OfferPolicyEngine.get_policy(real_merchant_id)["max_discount_percent"]
    updated = OfferPolicyEngine.update_policy(real_merchant_id, {"max_discount_percent": 6.0})
    assert updated["max_discount_percent"] == 6.0
    assert OfferPolicyEngine.get_policy(real_merchant_id)["max_discount_percent"] == 6.0


def test_get_policy_raises_for_unknown_merchant():
    from app.db.repositories.policies import PolicyNotFoundError
    with pytest.raises(PolicyNotFoundError):
        OfferPolicyEngine.get_policy("mch_does_not_exist")


class TestWhichLimitActuallyBound:
    """The information was computed and then discarded.

    `min(max_discount, max_allowed_by_margin)` knows perfectly well which
    of the two produced the answer, and the old code returned the single
    string "merchant_max_discount_or_margin_constraint" for both - so a
    merchant looking at a reduced offer could not tell whether it was their
    discount cap or their margin floor that had bound, which is the only
    question they actually ask.
    """

    def test_the_ceiling_binding_is_named_as_the_ceiling(self):
        # ~50% margin headroom, so 10% ceiling is the tighter of the two.
        decision = OfferPolicyEngine.evaluate(40.0, POLICY, cart_details=CART)
        assert decision["decision"] == "MODIFY"
        assert decision["reason"] == "REJECTED_CEILING"
        assert decision["approved_discount"] == 10.0

    def test_the_margin_floor_binding_is_named_as_the_margin_floor(self):
        # 20% gross margin, 15% floor -> only 5% of room, tighter than the
        # 10% ceiling. Same MODIFY, entirely different explanation.
        thin = {"amount": 1000.0, "cogs": 800.0}
        decision = OfferPolicyEngine.evaluate(40.0, POLICY, cart_details=thin)
        assert decision["decision"] == "MODIFY"
        assert decision["reason"] == "REJECTED_MARGIN_FLOOR"
        assert decision["approved_discount"] == pytest.approx(5.0)

    def test_every_decision_carries_the_numbers_it_was_made_from(self):
        """A refusal a merchant cannot check is just an assertion."""
        for cart in (CART, {"amount": 1000.0, "cogs": 800.0}):
            decision = OfferPolicyEngine.evaluate(40.0, POLICY, cart_details=cart)
            assert decision["requested_discount"] == 40.0
            assert decision["ceiling_percent"] == 10.0
            assert decision["margin_floor_percent"] == 15.0

    def test_an_approval_is_labelled_too_not_left_blank(self):
        decision = OfferPolicyEngine.evaluate(8.0, POLICY, cart_details=CART)
        assert decision["reason"] == "APPROVED"
