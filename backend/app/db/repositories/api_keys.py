"""
API key repository. Publishable keys (pk_) are meant to be public - their
security is the per-merchant origin allowlist + restricted event scope +
rate limit, not secrecy. Secret keys (sk_) are full-trust and only their hash
is ever stored, the same way GitHub/Stripe treat API tokens (not bcrypt -
these are high-entropy random tokens, not low-entropy passwords, so a fast
hash is correct and lets lookup-by-hash stay cheap).
"""
import hashlib
import secrets
from typing import Optional, Dict, Any, Tuple
from app.db.database import get_db
from app.core.ids import new_id


def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def create_key(merchant_id: str, key_type: str, mode: str = "test") -> Tuple[str, Dict[str, Any]]:
    """Returns (raw_key, row). The raw key is only ever available here -
    only its hash and prefix are persisted."""
    assert key_type in ("publishable", "secret")
    prefix = "pk" if key_type == "publishable" else "sk"
    raw_key = f"{prefix}_{mode}_{secrets.token_urlsafe(24)}"
    key_id = new_id("key")
    key_prefix = raw_key[: len(prefix) + len(mode) + 8]  # enough to recognize in a UI list

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO api_keys (key_id, merchant_id, key_type, key_prefix, key_hash)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (key_id, merchant_id, key_type, key_prefix, _hash_key(raw_key)),
        )
        cursor.execute("SELECT * FROM api_keys WHERE key_id = %s", (key_id,))
        row = dict(cursor.fetchone())

    return raw_key, row


def get_by_raw_key(raw_key: str) -> Optional[Dict[str, Any]]:
    """Looks up an api_keys row by the raw key a caller presented, and
    updates last_used_at. Returns None for an unknown or revoked key."""
    key_hash = _hash_key(raw_key)
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM api_keys WHERE key_hash = %s AND revoked_at IS NULL",
            (key_hash,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        cursor.execute(
            "UPDATE api_keys SET last_used_at = NOW() WHERE key_id = %s",
            (row["key_id"],),
        )
    return dict(row)


def revoke_key(key_id: str) -> None:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE api_keys SET revoked_at = NOW() WHERE key_id = %s",
            (key_id,),
        )


def list_keys_for_merchant(merchant_id: str) -> list:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT key_id, key_type, key_prefix, revoked_at, last_used_at, created_at "
            "FROM api_keys WHERE merchant_id = %s ORDER BY created_at DESC",
            (merchant_id,),
        )
        return cursor.fetchall()
