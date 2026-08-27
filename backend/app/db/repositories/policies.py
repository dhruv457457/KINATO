"""
Real, DB-backed merchant policy storage - replaces the in-memory
MERCHANT_POLICIES dict in app/services/policy_engine.py that only ever had
one entry ("jiva_demo") and silently coerced any unknown merchant into it.

get_policy() RAISES on an unknown merchant. There is no coercion to a
default merchant's numbers - that was the exact bug that made
multi-tenancy fake.
"""
import json
from typing import Dict, Any
from app.db.database import get_db


class PolicyNotFoundError(Exception):
    pass


_JSON_FIELDS = ("offer_ladder", "excluded_products")
_BOOL_FIELDS = ("free_shipping_allowed", "bundle_upsell_allowed")


def _deserialize(row: Dict[str, Any]) -> Dict[str, Any]:
    policy = dict(row)
    for field in _JSON_FIELDS:
        try:
            policy[field] = json.loads(policy[field]) if isinstance(policy[field], str) else policy[field]
        except (TypeError, ValueError):
            policy[field] = []
    for field in _BOOL_FIELDS:
        policy[field] = bool(policy[field])
    return policy


def get_policy(merchant_id: str) -> Dict[str, Any]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM merchant_policies WHERE merchant_id = %s", (merchant_id,))
        row = cursor.fetchone()
    if not row:
        raise PolicyNotFoundError(
            f"No policy configured for merchant {merchant_id!r}. "
            "Every merchant gets a default policy row at signup - this means "
            "the merchant_id is unknown, not that it needs a fallback."
        )
    return _deserialize(row)


def update_policy(merchant_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    if not updates:
        return get_policy(merchant_id)

    payload = dict(updates)
    for field in _JSON_FIELDS:
        if field in payload and not isinstance(payload[field], str):
            payload[field] = json.dumps(payload[field])

    set_clause = ", ".join(f"{key} = %s" for key in payload)
    values = list(payload.values()) + [merchant_id]

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"UPDATE merchant_policies SET {set_clause}, updated_at = NOW() WHERE merchant_id = %s",
            values,
        )
    return get_policy(merchant_id)
