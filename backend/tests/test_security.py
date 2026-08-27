"""
Deterministic tests for app/core/security.py's HMAC signing/verification -
the cryptographic boundary the spec requires for webhook and proposal
integrity (replaces the deleted old-domain test_hmac_tampering.py).
"""
from app.core.security import (
    generate_proposal_hash,
    verify_proposal_hash,
    verify_razorpay_payment_signature,
    verify_razorpay_webhook_signature,
)

SECRET = "test-secret-key"


def test_proposal_hash_is_deterministic():
    payload = {"amount": 100, "sku": "abc"}
    assert generate_proposal_hash(payload, SECRET) == generate_proposal_hash(payload, SECRET)


def test_proposal_hash_key_order_independent():
    a = generate_proposal_hash({"a": 1, "b": 2}, SECRET)
    b = generate_proposal_hash({"b": 2, "a": 1}, SECRET)
    assert a == b


def test_verify_proposal_hash_rejects_tampering():
    payload = {"amount": 100}
    valid_hash = generate_proposal_hash(payload, SECRET)
    assert verify_proposal_hash(payload, valid_hash, SECRET) is True

    tampered_payload = {"amount": 100000}  # attacker bumps the amount
    assert verify_proposal_hash(tampered_payload, valid_hash, SECRET) is False


def test_verify_proposal_hash_rejects_missing_hash():
    assert verify_proposal_hash({"amount": 100}, "", SECRET) is False


def test_verify_razorpay_payment_signature_valid_and_invalid():
    import hmac
    import hashlib

    order_id, payment_id = "order_abc", "pay_xyz"
    msg = f"{order_id}|{payment_id}".encode()
    valid_sig = hmac.new(SECRET.encode(), msg, hashlib.sha256).hexdigest()

    assert verify_razorpay_payment_signature(order_id, payment_id, valid_sig, SECRET) is True
    assert verify_razorpay_payment_signature(order_id, payment_id, "0" * 64, SECRET) is False
    assert verify_razorpay_payment_signature("", payment_id, valid_sig, SECRET) is False


def test_verify_razorpay_webhook_signature_valid_and_tampered():
    import hmac
    import hashlib

    body = b'{"event":"payment.captured"}'
    valid_sig = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()

    assert verify_razorpay_webhook_signature(body, valid_sig, SECRET) is True

    tampered_body = b'{"event":"payment.captured","amount":999999}'
    assert verify_razorpay_webhook_signature(tampered_body, valid_sig, SECRET) is False
