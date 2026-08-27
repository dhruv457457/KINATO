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
    assert decision["reason"] == "product_excluded"
    assert decision["approved_discount"] == 0.0


def test_denies_when_margin_too_thin():
    thin_cart = {"amount": 1000.0, "cogs": 900.0}  # 10% margin < 15% minimum
    decision = OfferPolicyEngine.evaluate(5.0, POLICY, cart_details=thin_cart)
    assert decision["decision"] == "DENY"
    assert decision["reason"] == "margin_too_low"


def test_denies_invalid_cart_total():
    decision = OfferPolicyEngine.evaluate(5.0, POLICY, cart_details={"amount": 0, "cogs": 0})
    assert decision["decision"] == "DENY"
    assert decision["reason"] == "invalid_cart_total"


def test_get_and_update_policy_roundtrip(real_merchant_id):
    original = OfferPolicyEngine.get_policy(real_merchant_id)["max_discount_percent"]
    updated = OfferPolicyEngine.update_policy(real_merchant_id, {"max_discount_percent": 6.0})
    assert updated["max_discount_percent"] == 6.0
    assert OfferPolicyEngine.get_policy(real_merchant_id)["max_discount_percent"] == 6.0


def test_get_policy_raises_for_unknown_merchant():
    from app.db.repositories.policies import PolicyNotFoundError
    with pytest.raises(PolicyNotFoundError):
        OfferPolicyEngine.get_policy("mch_does_not_exist")
