"""
Symmetric encryption for merchant-supplied secrets at rest (Razorpay key
secret, webhook secret). Fernet (AES-128-CBC + HMAC) via a single KEK from
env - deliberately not per-merchant key derivation or a KMS; that's real
added complexity this project doesn't need yet (see plan: "no KMS").
"""
from typing import Optional
from cryptography.fernet import Fernet, InvalidToken
from app.core.config import settings


class EncryptionNotConfiguredError(Exception):
    """Raised when FERNET_KEY is unset - see app/core/config.py's comment
    on why there is deliberately no hardcoded fallback key."""


def _get_fernet() -> Fernet:
    if not settings.FERNET_KEY:
        raise EncryptionNotConfiguredError(
            "FERNET_KEY is not set in backend/.env - Razorpay credentials "
            "cannot be encrypted/decrypted until it is. Generate one with: "
            'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
        )
    try:
        return Fernet(settings.FERNET_KEY.encode("utf-8"))
    except (ValueError, TypeError) as e:
        raise EncryptionNotConfiguredError(f"FERNET_KEY is set but not a valid Fernet key: {e}")


def encrypt_secret(plaintext: str) -> str:
    if not plaintext:
        return ""
    return _get_fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(ciphertext: str) -> Optional[str]:
    """Returns None (not an exception) on a corrupt/tampered/wrong-key
    ciphertext - callers should treat that as "credentials unavailable",
    not crash a request."""
    if not ciphertext:
        return None
    try:
        return _get_fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError):
        return None
