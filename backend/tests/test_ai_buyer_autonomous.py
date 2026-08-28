"""
Tests the autonomous (Path A) AI-buyer purchase flow: a merchant-authorized
spend mandate lets an AI buyer's purchase go through without per-transaction
approval, capped by a daily spend limit enforced deterministically (never by
the LLM). See app/payments/spend_mandate.py's module docstring for why this
is a Kinato-enforced cap on top of a real Razorpay Order, not an actual
NPCI/RBI UPI Autopay settlement - that requires the customer's own in-app
approval, which a headless AI agent cannot complete.
"""
import uuid
import pytest

from app.commerce.catalog import merchant_catalog
from app.commerce.mcp_server import ai_commerce_mcp
from app.payments.spend_mandate import spend_mandate_service
from app.gateway.event_bus import bus


class _FakeOrder:
    """Stands in for the real Razorpay SDK's `client.order` resource - a test
    double for test isolation (spend_mandate_service uses a single global
    Razorpay client, not the per-merchant factory payment_execution.py uses,
    so there's no real per-merchant account to call here). Same pattern as
    conftest.py's _FakePaymentLink."""
    def create(self, payload):
        return {"id": f"order_test_{uuid.uuid4().hex[:8]}"}


@pytest.fixture(autouse=True)
def _fake_razorpay_order_client(monkeypatch):
    monkeypatch.setattr(spend_mandate_service, "_client", type("C", (), {"order": _FakeOrder()})())


@pytest.fixture
def mandate():
    business_id = f"biz_{uuid.uuid4().hex[:8]}"
    result = spend_mandate_service.create_mandate(
        business_id=business_id,
        customer_email="buyer@example.com",
        customer_phone="+910000000000",
        daily_limit_inr=5000.0,
    )
    return result["mandate_id"]


async def test_autonomous_purchase_within_cap_settles_immediately(mandate):
    merchant_catalog.mutate_price("sku_lamp_01", 2499.0)
    merchant_catalog.mutate_inventory("sku_lamp_01", 10)
    quote = ai_commerce_mcp.quote("sku_lamp_01")

    result = await ai_commerce_mcp.create_purchase_intent_autonomous(quote["quote_id"], mandate)

    assert result["status"] == "SETTLED"
    assert result["daily_spent"] == pytest.approx(2499.0)

    # And it should show up as a normal payment.succeeded / attribution event on the bus.
    assert any(e["event_type"] == "payment.succeeded" for e in bus.get_recent_events(200))


async def test_autonomous_purchase_over_daily_cap_is_blocked(mandate):
    merchant_catalog.mutate_price("sku_rug_02", 3499.0)
    merchant_catalog.mutate_inventory("sku_rug_02", 10)

    # First purchase (₹3499) fits under the ₹5000 cap.
    quote1 = ai_commerce_mcp.quote("sku_rug_02")
    first = await ai_commerce_mcp.create_purchase_intent_autonomous(quote1["quote_id"], mandate)
    assert first["status"] == "SETTLED"

    # Second purchase (another ₹3499) would push total to ₹6998 > ₹5000 cap - must be blocked.
    quote2 = ai_commerce_mcp.quote("sku_rug_02")
    second = await ai_commerce_mcp.create_purchase_intent_autonomous(quote2["quote_id"], mandate)
    assert second.get("error") == "REJECTED"
    assert "limit" in second.get("reason", "").lower()


async def test_autonomous_purchase_still_revalidates_price_and_inventory(mandate):
    merchant_catalog.mutate_price("sku_lamp_01", 2499.0)
    quote = ai_commerce_mcp.quote("sku_lamp_01")
    merchant_catalog.mutate_price("sku_lamp_01", 9999.0)  # price changes after quote

    result = await ai_commerce_mcp.create_purchase_intent_autonomous(quote["quote_id"], mandate)
    assert result.get("error") == "REJECTED"
    assert result.get("reason") == "quote_price_mismatch"


async def test_revoked_mandate_blocks_further_autonomous_purchases(mandate):
    merchant_catalog.mutate_price("sku_lamp_01", 2499.0)
    merchant_catalog.mutate_inventory("sku_lamp_01", 10)
    spend_mandate_service.revoke_mandate(mandate)

    quote = ai_commerce_mcp.quote("sku_lamp_01")
    result = await ai_commerce_mcp.create_purchase_intent_autonomous(quote["quote_id"], mandate)
    assert result.get("error") == "REJECTED"
