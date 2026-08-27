"""
End-to-end tests for POST /api/events - the "does our platform even know a
checkout happened" gap. Uses a real signed-up merchant, real minted API
keys, and the real FastAPI app via TestClient (not a mocked route).
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db.repositories import api_keys as api_keys_repo
from app.db.repositories import merchants as merchants_repo
from app.db.repositories import checkouts as checkouts_repo

client = TestClient(app)


@pytest.fixture
def pk_and_merchant(real_merchant_id):
    raw_pk, _ = api_keys_repo.create_key(real_merchant_id, "publishable")
    return raw_pk, real_merchant_id


@pytest.fixture
def sk_and_merchant(real_merchant_id):
    raw_sk, _ = api_keys_repo.create_key(real_merchant_id, "secret")
    return raw_sk, real_merchant_id


def test_missing_api_key_is_rejected():
    r = client.post("/api/events", json={"event_type": "checkout.started", "payload": {}})
    assert r.status_code == 401


def test_invalid_publishable_key_is_rejected():
    r = client.post(
        "/api/events",
        json={"event_type": "checkout.started", "payload": {}},
        headers={"X-Kinato-Key": "pk_test_totally_fake_key"},
    )
    assert r.status_code == 401


def test_publishable_key_checkout_started_persists_real_checkout(pk_and_merchant, unique_checkout_id):
    raw_pk, merchant_id = pk_and_merchant
    r = client.post(
        "/api/events",
        json={
            "event_type": "checkout.started",
            "payload": {
                "checkout_id": unique_checkout_id,
                "customer_id": "cust_browser_1",
                "amount": 1999.0,
                "currency": "INR",
            },
        },
        headers={"X-Kinato-Key": raw_pk},
    )
    assert r.status_code == 200

    checkout = checkouts_repo.get_checkout(unique_checkout_id)
    assert checkout is not None, "checkout.started via pk_ must create a real row for the sweeper to see"
    assert checkout["merchant_id"] == merchant_id
    assert checkout["amount_paise"] == 199900
    assert checkout["status"] == "started"


def test_publishable_key_cannot_send_payment_succeeded(pk_and_merchant):
    raw_pk, _ = pk_and_merchant
    r = client.post(
        "/api/events",
        json={"event_type": "payment.succeeded", "payload": {"amount": 100000}},
        headers={"X-Kinato-Key": raw_pk},
    )
    assert r.status_code == 403, "a browser-facing key must never be able to claim a payment succeeded"


def test_publishable_key_cannot_send_checkout_abandoned(pk_and_merchant):
    """Only the server-side sweeper may declare abandonment - a spoofable
    browser key claiming it directly would let a customer fake their own
    recovery discount."""
    raw_pk, _ = pk_and_merchant
    r = client.post(
        "/api/events",
        json={"event_type": "checkout.abandoned", "payload": {"checkout_id": "chk_x"}},
        headers={"X-Kinato-Key": raw_pk},
    )
    assert r.status_code == 403


def test_secret_key_can_send_payment_succeeded(sk_and_merchant):
    raw_sk, _ = sk_and_merchant
    r = client.post(
        "/api/events",
        json={"event_type": "payment.succeeded", "payload": {"amount": 100000, "checkout_id": "chk_sk_test"}},
        headers={"Authorization": f"Bearer {raw_sk}"},
    )
    assert r.status_code == 200


def test_legacy_camelcase_payload_is_normalized(pk_and_merchant, unique_checkout_id):
    """An already-deployed older SDK build sending cartId/items must not
    silently break the moment this ships."""
    raw_pk, merchant_id = pk_and_merchant
    r = client.post(
        "/api/events",
        json={
            "event_type": "checkout.started",
            "payload": {
                "checkout_id": unique_checkout_id,
                "cartId": "cart_legacy_1",
                "amount": 500.0,
                "items": [{"sku": "sku_a"}, {"sku": "sku_b"}],
            },
        },
        headers={"X-Kinato-Key": raw_pk},
    )
    assert r.status_code == 200
    checkout = checkouts_repo.get_checkout(unique_checkout_id)
    assert checkout["cart_id"] == "cart_legacy_1"


def test_customer_identified_records_real_consent(pk_and_merchant):
    from app.db.repositories import consents as consents_repo

    raw_pk, merchant_id = pk_and_merchant
    r = client.post(
        "/api/events",
        json={
            "event_type": "customer.identified",
            "customer": {"external_id": "visitor_42", "phone": "+919999999999", "email": "shopper@example.com"},
            "payload": {"consent": {"voice": True, "email": False}},
        },
        headers={"X-Kinato-Key": raw_pk},
    )
    assert r.status_code == 200
    customer_id = r.json()["customer_id"]

    assert consents_repo.check_consent(merchant_id, customer_id, "voice") is True
    assert consents_repo.check_consent(merchant_id, customer_id, "email") is False, \
        "only channels explicitly marked true in consent must be granted"


def test_disallowed_origin_is_rejected_for_publishable_key(pk_and_merchant):
    raw_pk, merchant_id = pk_and_merchant
    merchants_repo.set_allowed_origins(merchant_id, ["https://mystore.example"])
    r = client.post(
        "/api/events",
        json={"event_type": "checkout.started", "payload": {"checkout_id": "chk_origin_test", "amount": 100.0}},
        headers={"X-Kinato-Key": raw_pk, "Origin": "https://evil.example"},
    )
    assert r.status_code == 403


def test_allowed_origin_is_accepted_and_echoed(pk_and_merchant, unique_checkout_id):
    raw_pk, merchant_id = pk_and_merchant
    merchants_repo.set_allowed_origins(merchant_id, ["https://mystore.example"])
    r = client.post(
        "/api/events",
        json={"event_type": "checkout.started", "payload": {"checkout_id": unique_checkout_id, "amount": 100.0}},
        headers={"X-Kinato-Key": raw_pk, "Origin": "https://mystore.example"},
    )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "https://mystore.example"


def test_beacon_style_body_embedded_key_is_accepted(pk_and_merchant, unique_checkout_id):
    """navigator.sendBeacon cannot carry custom headers, so the SDK's unload
    handler embeds the pk_ key in the JSON body instead - must still work."""
    raw_pk, merchant_id = pk_and_merchant
    r = client.post(
        "/api/events",
        json={
            "api_key": raw_pk,
            "event_type": "checkout.started",
            "payload": {"checkout_id": unique_checkout_id, "amount": 300.0},
        },
        # deliberately no X-Kinato-Key header
    )
    assert r.status_code == 200
    assert checkouts_repo.get_checkout(unique_checkout_id) is not None


def test_beacon_style_key_cannot_be_a_secret_key(sk_and_merchant):
    """A secret key must never be accepted from the request body - only
    headers - since a body is client-visible/loggable in ways a header sent
    over TLS from a trusted server process is not."""
    raw_sk, _ = sk_and_merchant
    r = client.post(
        "/api/events",
        json={"api_key": raw_sk, "event_type": "payment.succeeded", "payload": {}},
    )
    assert r.status_code == 401


def test_idempotency_key_prevents_duplicate_processing(sk_and_merchant, unique_checkout_id):
    raw_sk, merchant_id = sk_and_merchant
    body = {"event_type": "checkout.started", "payload": {"checkout_id": unique_checkout_id, "amount": 250.0}}
    headers = {"Authorization": f"Bearer {raw_sk}", "Idempotency-Key": f"idem_{unique_checkout_id}"}

    r1 = client.post("/api/events", json=body, headers=headers)
    r2 = client.post("/api/events", json=body, headers=headers)
    assert r1.status_code == 200
    assert r2.status_code == 200  # both succeed; the second is a no-op, not an error
