"""
================================================================================
FILE: app/core/security.py
MODULE: Module 1 - Core Foundation & Cryptography
--------------------------------------------------------------------------------
WHAT THIS FILE DOES:
Provides cryptographic integrity and verification mechanisms for the Kinato
Agentic Commerce Protocol.

It implements:
  1. Proposal HMAC Digest Signing (Tamper-Proof Offer Contracts):
     Computes HMAC-SHA256 over agreed A2A proposals (SKU, Price, Merchant, Nonce).
     Guarantees that if a malicious client alters a price by even 1 paisa, the hash
     breaks and the Policy Engine invalidates the transaction before Razorpay executes.

  2. Razorpay Standard Checkout Signature Verification:
     Implements official Razorpay HMAC verification:
     Expected = HMAC-SHA256(order_id + "|" + payment_id, RAZORPAY_KEY_SECRET)
     Guarantees mathematically that payment was authorized on Razorpay servers.

  3. Razorpay Webhook Signature Verification:
     Verifies X-Razorpay-Signature over the raw request body using WEBHOOK_SECRET.

KEY FUNCTIONS:
  - generate_proposal_hash(payload): Deterministic JSON serialization + HMAC-SHA256.
  - verify_proposal_hash(payload, hash): Constant-time HMAC comparison.
  - verify_razorpay_payment_signature(order_id, payment_id, signature): Razorpay auth.
  - verify_razorpay_webhook_signature(raw_body, signature): Webhook payload auth.
================================================================================
"""
import hmac
import hashlib
import json
from typing import Dict, Any
from app.core.config import settings


def generate_proposal_hash(proposal_payload: Dict[str, Any], secret_key: str = None) -> str:
    """
    Computes a deterministic HMAC-SHA256 hash over an immutable proposal payload.
    
    Why sorting keys is essential:
      Different languages/engines may order JSON keys differently. By sorting keys
      and removing whitespace (separators=(',', ':')), we ensure identical string
      digests across Python, TypeScript, and database stores.
    """
    key = (secret_key or settings.HMAC_SECRET_KEY).encode("utf-8")
    canonical_payload = json.dumps(proposal_payload, sort_keys=True, separators=(',', ':'))
    signature = hmac.new(key, canonical_payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return signature


def verify_proposal_hash(proposal_payload: Dict[str, Any], provided_hash: str, secret_key: str = None) -> bool:
    """
    Constant-time comparison to verify proposal integrity against tampering.
    Uses hmac.compare_digest to prevent side-channel timing attacks.
    """
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
    Mathematical Formula:
      Expected Signature = HMAC-SHA256(order_id + "|" + payment_id, RAZORPAY_KEY_SECRET)
    """
    key = (secret_key or settings.RAZORPAY_KEY_SECRET).encode("utf-8")
    msg = f"{order_id}|{payment_id}".encode("utf-8")
    expected_signature = hmac.new(key, msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected_signature, signature)


def verify_razorpay_webhook_signature(
    raw_body: bytes,
    signature: str,
    secret_key: str = None
) -> bool:
    """
    Verifies incoming Razorpay webhook signature from 'X-Razorpay-Signature' header.
    Mathematical Formula:
      Expected Signature = HMAC-SHA256(raw_request_body_bytes, RAZORPAY_WEBHOOK_SECRET)
    """
    key = (secret_key or settings.RAZORPAY_WEBHOOK_SECRET).encode("utf-8")
    expected_signature = hmac.new(key, raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected_signature, signature)
