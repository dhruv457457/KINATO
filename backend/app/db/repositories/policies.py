"""
Real, DB-backed merchant policy storage - replaces the in-memory
MERCHANT_POLICIES dict in app/services/policy_engine.py that only ever had
one entry ("jiva_demo") and silently coerced any unknown merchant into it.

get_policy() RAISES on an unknown merchant. There is no coercion to a
default merchant's numbers - that was the exact bug that made
multi-tenancy fake.
"""
import json
import time
from typing import Dict, Any, Optional, Tuple
from app.db.database import get_db

# Short-lived policy cache.
#
# On a live voice call this single-row SELECT was measured at 2365ms and
# 2795ms from Railway to Supabase, and the agent calls it (directly via
# get_policy_limits, and again inside check_offer) within one turn. That
# alone consumed most of the turn budget before check_offer could run, so
# the agent degraded and told the customer "someone will email you" at the
# exact moment they asked for a discount.
#
# A policy cannot meaningfully change mid-call, so caching it is safe. The
# TTL is deliberately short and update_policy() invalidates immediately, so
# a merchant editing their ceiling still sees it take effect at once.
_POLICY_TTL_SECONDS = 60.0
_policy_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}


def invalidate_policy_cache(merchant_id: Optional[str] = None) -> None:
    if merchant_id is None:
        _policy_cache.clear()
    else:
        _policy_cache.pop(merchant_id, None)


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


def get_policy(merchant_id: str, use_cache: bool = True) -> Dict[str, Any]:
    if use_cache:
        cached = _policy_cache.get(merchant_id)
        if cached and (time.monotonic() - cached[0]) < _POLICY_TTL_SECONDS:
            return dict(cached[1])

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
    policy = _deserialize(row)
    _policy_cache[merchant_id] = (time.monotonic(), dict(policy))
    return policy


def update_policy(merchant_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    # Any write invalidates immediately - a merchant lowering their discount
    # ceiling must not keep serving the old one from cache.
    invalidate_policy_cache(merchant_id)
    if not updates:
        return get_policy(merchant_id, use_cache=False)

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
    # Re-read past the cache: the row just changed.
    invalidate_policy_cache(merchant_id)
    return get_policy(merchant_id, use_cache=False)
