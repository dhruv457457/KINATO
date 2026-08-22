"""
================================================================================
TEST: tests/test_hmac_tampering.py
MODULE: Module 5 - Cryptographic HMAC Tamper Detection Tests
--------------------------------------------------------------------------------
Tests that any modification to price, quantity, or SKU invalidates the proposal hash.
================================================================================
"""
from app.models.enums import BusinessProfileType, PolicyStatus
from app.models.a2a import A2A_FinalOffer, QuoteLineItem
from app.models.inventory import BuyerContext
from app.policy.engine import policy_engine
from app.core.security import generate_proposal_hash


def test_hmac_price_tamper_invalidation():
    """
    Asserts that if a malicious party modifies the proposal price after signing,
    the Policy Engine flags PolicyStatus.INVALIDATED and halts Razorpay order creation.
    """
    buyer_ctx = BuyerContext(
        business_id="test_buyer",
        business_name="Test Kitchen",
        profile_type=BusinessProfileType.CLOUD_KITCHEN,
        daily_budget_limit=5000.0,
        weekly_budget_limit=20000.0,
        weekly_spent_so_far=0.0,
        inventory=[]
    )

    items = [
        QuoteLineItem(
            sku="SKU_CHEESE_MOZZ_1KG",
            name="Mozzarella Cheese Block (1kg)",
            quantity=5.0,
            unit="kg",
            unit_price=350.0,
            total_price=1750.0,
            cost_price=280.0,
            is_aging_upsell=False
        )
    ]

    # Original legitimate payload
    legit_payload = {
        "proposal_id": "prop_tamper_test",
        "rfq_id": "rfq_legit",
        "winning_supplier_id": "supp_legit",
        "subtotal": 1750.0,
        "total_discount": 0.0,
        "final_total": 1750.0,
        "items": [it.model_dump() for it in items]
    }
    legit_hash = generate_proposal_hash(legit_payload)

    # TAMPER: Client maliciously alters total_price to ₹100.00 while keeping original hash
    tampered_items = [
        QuoteLineItem(
            sku="SKU_CHEESE_MOZZ_1KG",
            name="Mozzarella Cheese Block (1kg)",
            quantity=5.0,
            unit="kg",
            unit_price=20.0,   # Tampered!
            total_price=100.0,  # Tampered!
            cost_price=280.0,
            is_aging_upsell=False
        )
    ]

    tampered_proposal = A2A_FinalOffer(
        proposal_id="prop_tamper_test",
        rfq_id="rfq_legit",
        winning_supplier_id="supp_legit",
        winning_supplier_name="Test Supplier",
        items=tampered_items,
        subtotal=100.0,
        total_discount=0.0,
        final_total=100.0,
        negotiation_summary="Tampered proposal attempt",
        proposal_hash=legit_hash,  # Old hash does not match tampered content!
        created_at="2026-08-22T00:00:00Z",
        expires_at="2026-08-22T01:00:00Z"
    )

    evaluation = policy_engine.evaluate(tampered_proposal, buyer_ctx, check_signature=True)

    assert evaluation.status == PolicyStatus.INVALIDATED
    assert evaluation.allowed_execution is False
    assert any("Cryptographic proposal digest mismatch" in chk.details for chk in evaluation.checks)
