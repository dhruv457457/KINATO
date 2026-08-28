"""
Checkout repository - the real cart a policy decision must be evaluated
against. Replaces call_orchestrator.py's hardcoded
`cart_details = {"amount": 3499.0, "cogs": 1500.0}`.
"""
import json
from typing import Optional, Dict, Any, List
from app.db.database import get_db
from app.core.ids import new_id


def create_checkout(
    merchant_id: str,
    amount_paise: int,
    customer_id: Optional[str] = None,
    cart_id: str = "",
    currency: str = "INR",
    cogs_paise: Optional[int] = None,
    line_items: Optional[list] = None,
    source: str = "sdk",
    checkout_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Idempotent by checkout_id.

    Razorpay retries a webhook on any non-2xx and fires several events for
    the same payment, all of which derive the SAME checkout_id
    (chk_wh_<payment_id>). Without ON CONFLICT the second delivery raised
    UniqueViolation -> 500 -> Razorpay retried -> 500 again: a self-
    reinforcing loop that also meant the webhook was never acknowledged.
    Re-creating an existing checkout now simply returns the existing row.
    """
    checkout_id = checkout_id or new_id("chk")
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO checkouts (checkout_id, merchant_id, customer_id, cart_id, amount_paise,
                                    currency, cogs_paise, line_items, source)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (checkout_id) DO NOTHING
            """,
            (checkout_id, merchant_id, customer_id, cart_id, amount_paise, currency,
             cogs_paise, json.dumps(line_items or []), source),
        )
        cursor.execute("SELECT * FROM checkouts WHERE checkout_id = %s", (checkout_id,))
        return dict(cursor.fetchone())


def get_checkout(checkout_id: str) -> Optional[Dict[str, Any]]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM checkouts WHERE checkout_id = %s", (checkout_id,))
        row = cursor.fetchone()
    return dict(row) if row else None


def mark_abandoned(checkout_id: str) -> None:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE checkouts SET status = 'abandoned', abandoned_at = NOW() "
            "WHERE checkout_id = %s AND status = 'started'",
            (checkout_id,),
        )


def mark_paid(checkout_id: str, rzp_payment_id: str = "") -> None:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE checkouts SET status = 'paid', paid_at = NOW(), rzp_payment_id = %s "
            "WHERE checkout_id = %s",
            (rzp_payment_id, checkout_id),
        )


def is_paid(checkout_id: str) -> bool:
    checkout = get_checkout(checkout_id)
    return bool(checkout) and checkout["status"] == "paid"


def list_stale_started(older_than_seconds_expr: str, limit: int = 200) -> List[Dict[str, Any]]:
    """Used by the abandonment sweeper. `older_than_seconds_expr` is a raw
    dialect-appropriate interval expression built by the caller (the sweeper
    knows dialect() and constructs it), since the window is per-merchant
    (merchant_policies.abandonment_window_seconds)."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT c.* FROM checkouts c
            JOIN merchant_policies p ON p.merchant_id = c.merchant_id
            WHERE c.status = 'started' AND {older_than_seconds_expr}
            LIMIT %s
            """,
            (limit,),
        )
        return cursor.fetchall()
