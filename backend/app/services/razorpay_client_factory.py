"""
Per-merchant Razorpay client factory. Replaces the single global
RAZORPAY_KEY_ID/SECRET from settings with each merchant's own encrypted,
merchant-supplied test-mode credentials - this is what makes payments
genuinely multi-tenant rather than all flowing through one shared account.
"""
import logging
import time
from typing import Optional, Dict, Any, Tuple
import razorpay

from app.core.crypto import decrypt_secret
from app.db.repositories.merchants import get_merchant

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 300
_client_cache: Dict[str, Tuple[float, razorpay.Client]] = {}


class RazorpayNotConnectedError(Exception):
    """Raised when a merchant has no (or invalid/undecryptable) Razorpay
    credentials on file yet."""


def _build_client(merchant: Dict[str, Any]) -> razorpay.Client:
    key_id = decrypt_secret(merchant.get("rzp_key_id_enc") or "")
    key_secret = decrypt_secret(merchant.get("rzp_key_secret_enc") or "")
    if not key_id or not key_secret:
        raise RazorpayNotConnectedError(
            f"Merchant {merchant['merchant_id']} has not connected a Razorpay account yet."
        )
    return razorpay.Client(auth=(key_id, key_secret))


def get_client_for_merchant(merchant_id: str) -> razorpay.Client:
    """Cached per-merchant client. Cache entries expire after
    _CACHE_TTL_SECONDS so a credential rotation is picked up without a
    restart, without re-decrypting on every single call."""
    cached = _client_cache.get(merchant_id)
    if cached and (time.time() - cached[0]) < _CACHE_TTL_SECONDS:
        return cached[1]

    merchant = get_merchant(merchant_id)
    client = _build_client(merchant)
    _client_cache[merchant_id] = (time.time(), client)
    return client


def invalidate_cache(merchant_id: str) -> None:
    _client_cache.pop(merchant_id, None)


def validate_credentials_live(key_id: str, key_secret: str) -> Tuple[bool, str]:
    """
    Makes one cheap, read-only call against the real Razorpay API to prove
    a key id/secret pair actually authenticates, before we ever encrypt and
    store it. Returns (ok, message).
    """
    try:
        client = razorpay.Client(auth=(key_id, key_secret))
        client.order.all({"count": 1})
        return True, "Credentials verified against the Razorpay API."
    except razorpay.errors.BadRequestError as e:
        return False, f"Razorpay rejected these credentials: {e}"
    except razorpay.errors.ServerError as e:
        return False, f"Razorpay API error while validating credentials: {e}"
    except Exception as e:
        # Covers auth failures the SDK surfaces as generic errors/HTTP 401s,
        # network issues, etc. - never silently treat an unverifiable key as valid.
        return False, f"Could not verify these credentials: {e}"
