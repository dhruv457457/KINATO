"""
================================================================================
FILE: app/core/security.py
MODULE: Module 1 - Core Foundation & Cryptography
--------------------------------------------------------------------------------
WHAT THIS FILE DOES:
Provides bank-grade cryptographic signing and verification for:
  1. Immutable A2A Proposal Digests (HMAC-SHA256)
  2. Razorpay Standard Payment Responses (both direct HMAC & Razorpay SDK utility)
  3. Razorpay Incoming Webhook Signatures (X-Razorpay-Signature verification)

PARAMETER AUDIT:
  - verify_razorpay_payment_signature:
      Requires: order_id, payment_id, signature, key_secret
      Algorithm: HMAC-SHA256(f"{order_id}|{payment_id}", key_secret) == signature
  - verify_razorpay_webhook_signature:
      Requires: raw_body (bytes), signature (str), webhook_secret (str)
      Algorithm: HMAC-SHA256(raw_body_bytes, webhook_secret) == signature
================================================================================
"""
import hmac
import hashlib
import json
from typing import Dict, Any, Union
from app.core.config import settings


def generate_proposal_hash(proposal_payload: Dict[str, Any], secret_key: str = None) -> str:
    """
    Computes a deterministic HMAC-SHA256 hash over an agreed proposal contract.
    Keys are sorted and compact JSON formatting is enforced to guarantee deterministic hashes.
    """
    key = (secret_key or settings.HMAC_SECRET_KEY).encode("utf-8")
    canonical_payload = json.dumps(proposal_payload, sort_keys=True, separators=(',', ':'))
    signature = hmac.new(key, canonical_payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return signature


def verify_proposal_hash(proposal_payload: Dict[str, Any], provided_hash: str, secret_key: str = None) -> bool:
    """
    Constant-time comparison to verify proposal integrity against tampering.
    """
    if not provided_hash:
        return False
    expected_hash = generate_proposal_hash(proposal_payload, secret_key)
    return hmac.compare_digest(expected_hash, provided_hash)


def verify_razorpay_payment_signature(
    order_id: str,
    payment_id: str,
    signature: str,
    secret_key: str = None
) -> bool:
    """
    Verifies Razorpay standard checkout payment response signature.
    Official Algorithm:
      HMAC-SHA256(f"{order_id}|{payment_id}", KEY_SECRET)
    """
    if not order_id or not payment_id or not signature:
        return False
        
    key = (secret_key or settings.RAZORPAY_KEY_SECRET).encode("utf-8")
    msg = f"{order_id}|{payment_id}".encode("utf-8")
    expected_signature = hmac.new(key, msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected_signature, signature)


def verify_razorpay_payment_dict(
    payment_response: Dict[str, str],
    secret_key: str = None
) -> bool:
    """
    Helper accepting Razorpay checkout dictionary directly:
    {
      "razorpay_order_id": "order_...",
      "razorpay_payment_id": "pay_...",
      "razorpay_signature": "..."
    }
    """
    order_id = payment_response.get("razorpay_order_id", "")
    payment_id = payment_response.get("razorpay_payment_id", "")
    signature = payment_response.get("razorpay_signature", "")
    return verify_razorpay_payment_signature(order_id, payment_id, signature, secret_key)


def verify_razorpay_webhook_signature(
    raw_body: Union[bytes, str],
    signature: str,
    secret_key: str = None
) -> bool:
    """
    Verifies incoming Razorpay webhook signature (X-Razorpay-Signature header).
    Official Algorithm:
      HMAC-SHA256(raw_request_body_bytes, WEBHOOK_SECRET)
    """
    if not signature or not raw_body:
        return False
        
    body_bytes = raw_body if isinstance(raw_body, bytes) else raw_body.encode("utf-8")
    key = (secret_key or settings.RAZORPAY_WEBHOOK_SECRET).encode("utf-8")
    expected_signature = hmac.new(key, body_bytes, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected_signature, signature)
