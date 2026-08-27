"""
Tests app/payments/webhooks.py - path-scoped per merchant, HMAC-verified
with that merchant's own (decrypted) webhook secret, and payment.failed as
the zero-code primary recovery trigger.
"""
import hashlib
import hmac
import json

from fastapi.testclient import TestClient
from app.main import app
from app.core.crypto import encrypt_secret
from app.db.repositories import merchants as merchants_repo
from app.db.repositories import checkouts as checkouts_repo
from app.gateway.event_bus import bus

client = TestClient(app)
WEBHOOK_SECRET = "test_webhook_secret_123"


def _events(event_type: str):
    return [e for e in bus.get_recent_events(500) if e["event_type"] == event_type]


def _sign(body: bytes, secret: str = WEBHOOK_SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _with_webhook_secret(merchant_id: str, secret: str = WEBHOOK_SECRET):
    merchants_repo.set_razorpay_credentials(
        merchant_id, key_id_enc="", key_secret_enc="", webhook_secret_enc=encrypt_secret(secret)
    )


def test_unknown_merchant_returns_404():
    r = client.post("/webhooks/razorpay/mch_does_not_exist", content=b"{}", headers={"X-Razorpay-Signature": "x"})
    assert r.status_code == 404


def test_merchant_without_webhook_secret_configured_returns_400(real_merchant_id):
    r = client.post(f"/webhooks/razorpay/{real_merchant_id}", content=b"{}", headers={"X-Razorpay-Signature": "x"})
    assert r.status_code == 400


def test_invalid_signature_is_rejected(real_merchant_id):
    _with_webhook_secret(real_merchant_id)
    body = json.dumps({"event": "payment.failed", "payload": {}}).encode()
    r = client.post(
        f"/webhooks/razorpay/{real_merchant_id}", content=body,
        headers={"X-Razorpay-Signature": "wrong_signature"},
    )
    assert r.status_code == 400


def test_payment_failed_with_contact_triggers_zero_code_recovery(real_merchant_id):
    """The whole point: a merchant with ZERO SDK/API integration still gets
    a real, contactable recovery trigger straight from Razorpay."""
    _with_webhook_secret(real_merchant_id)
    body_dict = {
        "event": "payment.failed",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_test_failed_1",
                    "amount": 349900,
                    "email": "shopper@example.com",
                    "contact": "+919999999998",
                    "error_reason": "insufficient_funds",
                    "notes": {},
                }
            }
        },
    }
    body = json.dumps(body_dict).encode()
    r = client.post(
        f"/webhooks/razorpay/{real_merchant_id}", content=body,
        headers={"X-Razorpay-Signature": _sign(body)},
    )
    assert r.status_code == 200

    failed_events = _events("checkout.payment_failed")
    assert len(failed_events) == 1
    payload = failed_events[0]["payload"]
    assert payload["amount_paise"] == 349900
    assert payload["customer_id"] is not None

    checkout = checkouts_repo.get_checkout(payload["checkout_id"])
    assert checkout is not None, "a real checkout row must exist even with zero prior SDK integration"
    assert checkout["source"] == "razorpay_webhook"


def test_payment_failed_with_no_contact_info_blocks_recovery_honestly(real_merchant_id):
    """No email/phone in the webhook payload means no one to call - this
    must be surfaced as a real, visible 'blocked' event, not silently dropped
    or (worse) faked with a placeholder contact."""
    _with_webhook_secret(real_merchant_id)
    body = json.dumps({
        "event": "payment.failed",
        "payload": {"payment": {"entity": {"id": "pay_test_2", "amount": 100000, "notes": {}}}},
    }).encode()
    r = client.post(
        f"/webhooks/razorpay/{real_merchant_id}", content=body,
        headers={"X-Razorpay-Signature": _sign(body)},
    )
    assert r.status_code == 200
    assert len(_events("checkout.payment_failed")) == 0
    assert len(_events("recovery.blocked")) == 1
    assert _events("recovery.blocked")[0]["payload"]["reason"] == "no_contact"


async def test_payment_captured_marks_checkout_paid(real_merchant_id, unique_checkout_id):
    import asyncio

    _with_webhook_secret(real_merchant_id)
    checkouts_repo.create_checkout(real_merchant_id, amount_paise=200000, checkout_id=unique_checkout_id)

    body = json.dumps({
        "event": "payment.captured",
        "payload": {"payment": {"entity": {
            "id": "pay_test_captured", "amount": 200000,
            "notes": {"checkout_id": unique_checkout_id},
        }}},
    }).encode()
    r = client.post(
        f"/webhooks/razorpay/{real_merchant_id}", content=body,
        headers={"X-Razorpay-Signature": _sign(body)},
    )
    assert r.status_code == 200
    assert len(_events("payment.succeeded")) == 1

    await asyncio.sleep(0.3)  # let attribution.py's fire-and-forget subscriber run
    checkout = checkouts_repo.get_checkout(unique_checkout_id)
    assert checkout["status"] == "paid"


def test_downtime_events_publish_rail_degraded(real_merchant_id):
    _with_webhook_secret(real_merchant_id)
    body = json.dumps({"event": "payment.downtime.started", "payload": {}}).encode()
    r = client.post(
        f"/webhooks/razorpay/{real_merchant_id}", content=body,
        headers={"X-Razorpay-Signature": _sign(body)},
    )
    assert r.status_code == 200
    assert any(e["payload"]["status"] == "down" for e in _events("rail.degraded"))
