"""
================================================================================
TEST: tests/test_policy_refusal.py
MODULE: Module 5 - Automated Policy Gate & Guardrail Tests
--------------------------------------------------------------------------------
Tests deterministic refusal invariants:
  1. Merchant Floor Price Breach (Selling Price < Cost Price * 1.15) must be BLOCKED.
  2. Buyer Cashflow Overrun (Total > Daily Limit) must be BLOCKED with suggestions.
================================================================================
"""
from app.models.enums import BusinessProfileType, PolicyStatus
from app.models.a2a import A2A_FinalOffer, QuoteLineItem
from app.models.inventory import BuyerContext
from app.policy.engine import policy_engine
from app.core.security import generate_proposal_hash


def test_merchant_floor_price_refusal():
    """
    Asserts that if an item is priced below Cost Price + 15% margin,
    the Policy Engine strictly REFUSES the transaction to prevent merchant losses.
    """
    buyer_ctx = BuyerContext(
        business_id="test_buyer",
        business_name="Test Kitchen",
        profile_type=BusinessProfileType.CLOUD_KITCHEN,
        daily_budget_limit=3000.0,
        weekly_budget_limit=15000.0,
        weekly_spent_so_far=0.0,
        inventory=[]
    )

    # Cost Price: 280. Floor Price: 280 * 1.15 = 322. Offered Price: 300 (VIOLATION!)
    violating_items = [
        QuoteLineItem(
            sku="SKU_CHEESE_MOZZ_1KG",
            name="Mozzarella Cheese Block (1kg)",
            quantity=5.0,
            unit="kg",
            unit_price=300.0,
            total_price=1500.0,
            cost_price=280.0,
            is_aging_upsell=False
        )
    ]

    payload = {
        "proposal_id": "prop_test_floor",
        "rfq_id": "rfq_test",
        "winning_supplier_id": "supp_test",
        "subtotal": 1500.0,
        "total_discount": 0.0,
        "final_total": 1500.0,
        "items": [it.model_dump() for it in violating_items]
    }
    prop_hash = generate_proposal_hash(payload)

    proposal = A2A_FinalOffer(
        proposal_id="prop_test_floor",
        rfq_id="rfq_test",
        winning_supplier_id="supp_test",
        winning_supplier_name="Test Supplier",
        items=violating_items,
        subtotal=1500.0,
        total_discount=0.0,
        final_total=1500.0,
        negotiation_summary="Floor violation test",
        proposal_hash=prop_hash,
        created_at="2026-08-22T00:00:00Z",
        expires_at="2026-08-22T01:00:00Z"
    )

    evaluation = policy_engine.evaluate(proposal, buyer_ctx, check_signature=True)

    assert evaluation.status == PolicyStatus.BLOCKED
    assert evaluation.allowed_execution is False
    assert any("violates wholesale cost floor" in chk.details for chk in evaluation.checks)


def test_buyer_cashflow_limit_refusal():
    """
    Asserts that if an order exceeds the buyer's daily liquidity limit,
    the Policy Engine BLOCKS order creation and provides an actionable suggestion.
    """
    buyer_ctx = BuyerContext(
        business_id="test_buyer",
        business_name="Test Kitchen",
        profile_type=BusinessProfileType.CLOUD_KITCHEN,
        daily_budget_limit=2000.0,  # Daily limit: ₹2,000
        weekly_budget_limit=10000.0,
        weekly_spent_so_far=0.0,
        inventory=[]
    )

    items = [
        QuoteLineItem(
            sku="SKU_CHEESE_MOZZ_1KG",
            name="Mozzarella Cheese Block (1kg)",
            quantity=8.0,
            unit="kg",
            unit_price=350.0,
            total_price=2800.0,  # ₹2,800 > ₹2,000 daily budget!
            cost_price=280.0,
            is_aging_upsell=False
        )
    ]

    payload = {
        "proposal_id": "prop_test_budget",
        "rfq_id": "rfq_test",
        "winning_supplier_id": "supp_test",
        "subtotal": 2800.0,
        "total_discount": 0.0,
        "final_total": 2800.0,
        "items": [it.model_dump() for it in items]
    }
    prop_hash = generate_proposal_hash(payload)

    proposal = A2A_FinalOffer(
        proposal_id="prop_test_budget",
        rfq_id="rfq_test",
        winning_supplier_id="supp_test",
        winning_supplier_name="Test Supplier",
        items=items,
        subtotal=2800.0,
        total_discount=0.0,
        final_total=2800.0,
        negotiation_summary="Budget overrun test",
        proposal_hash=prop_hash,
        created_at="2026-08-22T00:00:00Z",
        expires_at="2026-08-22T01:00:00Z"
    )

    evaluation = policy_engine.evaluate(proposal, buyer_ctx, check_signature=True)

    assert evaluation.status == PolicyStatus.BLOCKED
    assert evaluation.allowed_execution is False
    assert evaluation.actionable_suggestion is not None
    assert "exceeds daily" in evaluation.summary_reason.lower()
