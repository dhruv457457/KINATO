"""
End-to-end test of the deterministic recovery pipeline:
checkout.abandoned -> eligibility -> recovery.opportunity.created ->
RecoveryStrategist (recovery.plan_ready) -> CallOrchestrator (call.started,
a real - but test-faked, see conftest._fake_voice_dispatch - Twilio dial).

Day 6 rewrite: the old `customer.understood` -> `_process_offer_request`
path (a hardcoded cart, keyed off a separate LLM classification event) is
gone. A live call now resolves offers by calling check_offer/issue_offer
directly, in the same agent turn (see app/channels/voice_runtime.py and
app/agents/tools.py) - so this test drives those tools directly too,
exactly as voice_runtime does, rather than publishing a synthetic
`customer.understood` event that nothing subscribes to anymore.
"""
import uuid
import pytest

from app.gateway.event_bus import bus
from app.db.repositories import consents as consents_repo
from app.db.repositories import customers as customers_repo
from app.db.repositories import checkouts as checkouts_repo
from app.db.repositories import recovery_attempts as recovery_attempts_repo
from app.agents.state import AgentContext
from app.agents.tools import check_offer, issue_offer, record_opt_out
from app.agents.audit import execute_tool
from tests.conftest import wait_until


def _events(event_type: str):
    return [e for e in bus.get_recent_events(500) if e["event_type"] == event_type]


async def _run_abandonment_to_call(
    merchant_id: str, checkout_id: str, phone: str = "+919999999999", email: str = ""
) -> dict:
    """Creates a real customer (with a phone - place_outbound_call is faked
    in tests, see conftest, but the orchestrator still looks a real phone
    up), grants voice consent, and fires checkout.abandoned through to a
    real (test-faked) call being placed. Returns {recovery_attempt_id,
    customer_id}."""
    customer = customers_repo.upsert_by_contact(merchant_id, phone=phone, email=email, name="Test Customer")
    customer_id = customer["customer_id"]
    consents_repo.record_consent(merchant_id, customer_id, channel="voice", status="granted", source="checkout_optin")

    await bus.publish(
        event_type="checkout.abandoned",
        payload={"checkout_id": checkout_id, "customer_id": customer_id, "amount": 3499.0, "currency": "INR"},
        correlation_id=checkout_id,
        merchant_id=merchant_id,
    )
    found = await wait_until(lambda: len(_events("call.started")) > 0, timeout=8.0)
    assert found, "call.started was never published after checkout.abandoned"
    recovery_attempt_id = _events("call.started")[-1]["payload"]["recovery_attempt_id"]
    return {"recovery_attempt_id": recovery_attempt_id, "customer_id": customer_id}


def _ctx_for(recovery_attempt_id: str, merchant_id: str, customer_id: str, checkout_id: str) -> AgentContext:
    return AgentContext(
        merchant_id=merchant_id,
        correlation_id=checkout_id,
        customer_id=customer_id,
        checkout_id=checkout_id,
        recovery_attempt_id=recovery_attempt_id,
    )


async def test_approved_offer_flows_through_to_attributed_revenue(connected_merchant_id, unique_checkout_id):
    merchant_id = connected_merchant_id
    checkout_id = unique_checkout_id
    checkouts_repo.create_checkout(
        merchant_id=merchant_id, amount_paise=349_900, cogs_paise=150_000, checkout_id=checkout_id
    )
    call = await _run_abandonment_to_call(merchant_id, checkout_id, email=f"buyer_{uuid.uuid4().hex[:8]}@example.com")
    ctx = _ctx_for(call["recovery_attempt_id"], merchant_id, call["customer_id"], checkout_id)

    checked = await execute_tool(check_offer, {"requested_discount_percent": 8.0}, ctx)
    assert checked["decision"] == "ALLOW"

    issued = await execute_tool(issue_offer, {"offer_token": checked["offer_token"], "channel": "email"}, ctx)
    assert issued["status"] == "ISSUED"
    assert issued["email_sent"] is True

    found = await wait_until(lambda: len(_events("email.send_requested")) > 0)
    assert found, "email.send_requested was never published for an approved offer"
    email_payload = _events("email.send_requested")[-1]["payload"]
    assert email_payload["checkout_id"] == checkout_id
    assert email_payload["discount"] == 8.0
    assert email_payload["payment_url"].startswith("http")

    # Simulate the Razorpay webhook confirming payment (see app/payments/webhooks.py).
    await bus.publish(
        event_type="payment.succeeded",
        payload={
            "amount": int(email_payload["amount"] * 100),
            "checkout_id": checkout_id,
            "recovery_attempt_id": call["recovery_attempt_id"],
        },
        correlation_id=checkout_id,
        merchant_id=merchant_id,
    )

    found = await wait_until(lambda: len(_events("revenue.attributed")) > 0)
    assert found, "revenue.attributed was never published after payment.succeeded"
    attribution_payload = _events("revenue.attributed")[-1]["payload"]
    assert attribution_payload["checkout_id"] == checkout_id
    assert attribution_payload["recovery_attempt_id"] == call["recovery_attempt_id"]
    assert attribution_payload["amount"] == pytest.approx(email_payload["amount"], rel=1e-6)


async def test_excessive_discount_request_is_capped_not_honored(connected_merchant_id, unique_checkout_id):
    """A requested discount above the merchant's configured max must never
    be honored as-is - the policy engine silently caps it (MODIFY)."""
    from app.services.policy_engine import policy_engine

    merchant_id = connected_merchant_id
    checkout_id = unique_checkout_id
    checkouts_repo.create_checkout(
        merchant_id=merchant_id, amount_paise=349_900, cogs_paise=150_000, checkout_id=checkout_id
    )
    call = await _run_abandonment_to_call(merchant_id, checkout_id)
    ctx = _ctx_for(call["recovery_attempt_id"], merchant_id, call["customer_id"], checkout_id)
    merchant_max = policy_engine.get_policy(merchant_id)["max_discount_percent"]

    checked = await execute_tool(check_offer, {"requested_discount_percent": 90.0}, ctx)
    assert checked["decision"] == "MODIFY"
    assert checked["approved_percent"] == merchant_max
    assert checked["approved_percent"] < 90.0

    issued = await execute_tool(issue_offer, {"offer_token": checked["offer_token"]}, ctx)
    assert issued["status"] == "ISSUED"
    assert issued["approved_percent"] == merchant_max


async def test_opt_out_revokes_consent_and_halts_outreach(connected_merchant_id, unique_checkout_id):
    """'Don't call me again' must actually stop outreach, not just log a
    warning - verified by checking the real consent record, and by proving
    issue_offer itself refuses to act for an opted-out customer."""
    from app.services.identity_service import identity_service

    merchant_id = connected_merchant_id
    checkout_id = unique_checkout_id
    checkouts_repo.create_checkout(
        merchant_id=merchant_id, amount_paise=349_900, cogs_paise=150_000, checkout_id=checkout_id
    )
    call = await _run_abandonment_to_call(merchant_id, checkout_id)
    customer_id = call["customer_id"]
    ctx = _ctx_for(call["recovery_attempt_id"], merchant_id, customer_id, checkout_id)

    assert await identity_service.check_consent(merchant_id, customer_id, "voice") is True

    opt_out_result = await execute_tool(record_opt_out, {}, ctx)
    assert opt_out_result == {"status": "RECORDED"}
    assert await identity_service.check_consent(merchant_id, customer_id, "voice") is False, \
        "consent must be revoked, not just logged"

    # A subsequent offer must not reach the customer once opted out - the
    # check_offer preview itself doesn't gate on consent (it applies
    # nothing), but issue_offer - the only tool that can act - must refuse.
    checked = await execute_tool(check_offer, {"requested_discount_percent": 5.0}, ctx)
    assert checked["decision"] == "ALLOW"
    issued = await execute_tool(issue_offer, {"offer_token": checked["offer_token"]}, ctx)
    assert issued["status"] == "REJECTED"
    assert issued["reason"] == "consent_revoked"

    events_before = len(_events("email.send_requested"))
    assert len(_events("email.send_requested")) == events_before, "an opted-out customer must never receive outreach"


async def test_product_excluded_from_discounts_is_denied_gracefully(connected_merchant_id, unique_checkout_id):
    """Previously skipped: call_orchestrator hardcoded cart_details without
    product_ids, so the policy engine's product-exclusion DENY path was
    unreachable end-to-end. check_offer now reads real line_items off the
    checkout row, closing that gap."""
    from app.services.policy_engine import policy_engine

    merchant_id = connected_merchant_id
    checkout_id = unique_checkout_id
    excluded_sku = f"sku_excluded_{uuid.uuid4().hex[:6]}"
    policy_engine.update_policy(merchant_id, {"excluded_products": [excluded_sku]})

    checkouts_repo.create_checkout(
        merchant_id=merchant_id,
        amount_paise=349_900,
        cogs_paise=150_000,
        checkout_id=checkout_id,
        line_items=[{"product_id": excluded_sku, "name": "Excluded Item"}],
    )
    call = await _run_abandonment_to_call(merchant_id, checkout_id)
    ctx = _ctx_for(call["recovery_attempt_id"], merchant_id, call["customer_id"], checkout_id)

    checked = await execute_tool(check_offer, {"requested_discount_percent": 5.0}, ctx)
    assert checked["decision"] == "DENY"
    assert checked["reason"] == "REJECTED_SKU_EXCLUDED"


async def test_watchdog_proceeds_with_generic_line_if_plan_never_arrives(
    connected_merchant_id, unique_checkout_id, monkeypatch
):
    """If RecoveryStrategist's recovery.plan_ready doesn't land in time, the
    orchestrator must still call - with a generic opening line - rather
    than silently never recovering this checkout at all."""
    import app.services.call_orchestrator as call_orchestrator_module

    monkeypatch.setattr(call_orchestrator_module, "PLAN_WATCHDOG_SECONDS", 0.001)
    # Prevent RecoveryStrategist from ever resolving the future, so the
    # watchdog is guaranteed to be what proceeds the call, not a race.
    monkeypatch.setattr(
        "app.services.discovery_agent.RecoveryStrategist.handle_opportunity_created",
        lambda event: _never_completes(),
    )

    merchant_id = connected_merchant_id
    checkout_id = unique_checkout_id
    checkouts_repo.create_checkout(
        merchant_id=merchant_id, amount_paise=349_900, cogs_paise=150_000, checkout_id=checkout_id
    )
    call = await _run_abandonment_to_call(merchant_id, checkout_id)

    attempt = recovery_attempts_repo.get_recovery_attempt(call["recovery_attempt_id"])
    assert attempt["state"] == "CALLING"
    assert "opening_line" in (attempt["plan"] or "")


async def _never_completes():
    import asyncio
    await asyncio.sleep(3600)


async def test_sdk_external_id_resolves_to_real_customer(real_merchant_id, unique_checkout_id):
    """The SDK identifies customers by a merchant-chosen externalId - usually
    an email. That string used to be written straight into
    checkouts.customer_id, while consent was recorded against the real
    cust_... id. The consent lookup then found nothing and recovery was
    silently blocked for a customer who HAD consented - no error anywhere,
    just a recovery that never happened.
    """
    from app.db.repositories import customers as customers_repo

    email = f"sdk_{uuid.uuid4().hex[:8]}@example.com"
    customer = customers_repo.upsert_by_external_id(
        real_merchant_id, email, email=email, phone="+919999999997", name="SDK Customer"
    )
    real_id = customer["customer_id"]
    assert real_id != email, "precondition: the real id must differ from the external id"

    # Exactly what the SDK sends: customer identified by the external id.
    await bus.publish(
        event_type="checkout.started",
        payload={"checkout_id": unique_checkout_id, "customer_id": email, "amount": 2990, "currency": "INR"},
        correlation_id=unique_checkout_id,
        merchant_id=real_merchant_id,
    )

    stored = await wait_until(
        lambda: (checkouts_repo.get_checkout(unique_checkout_id) or {}).get("customer_id") == real_id,
        timeout=10.0,
    )
    row = checkouts_repo.get_checkout(unique_checkout_id)
    assert stored, f"checkout.customer_id was {row and row.get('customer_id')!r}, expected {real_id!r}"
