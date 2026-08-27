"""Repository for the merchants table - the root of tenancy. No service
should ever inline SQL against this table; go through here so tenancy rules
(e.g. "unknown merchant is an error, not a silent default") stay centralized."""
import json
from typing import Optional, Dict, Any
from app.db.database import get_db
from app.core.ids import new_id


class MerchantNotFoundError(Exception):
    pass


def create_merchant(
    name: str, email: str, password_hash: str, store_url: str = "", merchant_id: Optional[str] = None
) -> Dict[str, Any]:
    """`merchant_id` is normally auto-generated; it's only ever passed
    explicitly by backend/scripts/seed_demo_merchant.py, to keep the
    existing "jiva_demo" literal id that ~40 call sites still reference
    while that migrates to real per-request auth (see plan Day 2/4)."""
    merchant_id = merchant_id or new_id("mch")
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO merchants (merchant_id, name, email, password_hash, store_url)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (merchant_id, name, email, password_hash, store_url),
        )
        # Every merchant gets a default policy row immediately - there is no
        # "unconfigured" state where a policy lookup would need to fall back
        # to someone else's numbers.
        cursor.execute(
            "INSERT INTO merchant_policies (merchant_id) VALUES (%s)",
            (merchant_id,),
        )
    return get_merchant(merchant_id)


def get_merchant(merchant_id: str) -> Dict[str, Any]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM merchants WHERE merchant_id = %s", (merchant_id,))
        row = cursor.fetchone()
    if not row:
        raise MerchantNotFoundError(f"No merchant with id {merchant_id!r}")
    return dict(row)


def get_merchant_by_email(email: str) -> Optional[Dict[str, Any]]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM merchants WHERE email = %s", (email,))
        row = cursor.fetchone()
    return dict(row) if row else None


def set_onboarding_step(merchant_id: str, step: str) -> None:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE merchants SET onboarding_step = %s WHERE merchant_id = %s",
            (step, merchant_id),
        )


def set_razorpay_credentials(
    merchant_id: str, key_id_enc: str, key_secret_enc: str, webhook_secret_enc: str
) -> None:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE merchants
            SET rzp_key_id_enc = %s, rzp_key_secret_enc = %s, rzp_webhook_secret_enc = %s
            WHERE merchant_id = %s
            """,
            (key_id_enc, key_secret_enc, webhook_secret_enc, merchant_id),
        )


def set_allowed_origins(merchant_id: str, origins: list) -> None:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE merchants SET allowed_origins = %s WHERE merchant_id = %s",
            (json.dumps(origins), merchant_id),
        )


def get_allowed_origins(merchant_id: str) -> list:
    merchant = get_merchant(merchant_id)
    try:
        return json.loads(merchant.get("allowed_origins") or "[]")
    except (TypeError, ValueError):
        return []
