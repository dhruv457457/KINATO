"""
The two-phase money gate. An LLM tool call can only ever reference one of
these rows by its opaque token; the actual amount an `issue_offer` call acts
on is whatever this row says, computed by the deterministic policy engine -
never what the model argues for in its own tool-call arguments.
"""
import uuid
from typing import Optional, Dict, Any
from app.db.database import get_db
from app.core.ids import new_id


def create_offer_token(
    merchant_id: str,
    decision: str,
    reason: str,
    base_amount_paise: int,
    final_amount_paise: int,
    requested_percent: Optional[float] = None,
    approved_percent: Optional[float] = None,
    checkout_id: Optional[str] = None,
    recovery_attempt_id: Optional[str] = None,
    expires_in_seconds: int = 900,
) -> Optional[Dict[str, Any]]:
    """Mint one spendable offer. None if the checkout was already paid.

    Two things changed here for latency, neither of which weakens the gate:

    RETURNING replaces the follow-up SELECT. The row was already in hand
    after the INSERT; asking the database for it a second time cost a whole
    round trip, and from Railway a round trip is not microseconds.

    And when a checkout_id is given, the INSERT is CONDITIONAL on that
    checkout still being unpaid - `INSERT ... SELECT ... WHERE`. Zero rows
    back means the cart was paid, and the caller turns that into
    REJECTED_ALREADY_PAID. This is stronger than the read-then-check it
    replaces, not merely faster: previously the paid check and the mint were
    two separate statements, so a payment landing between them produced a
    spendable discount for a cart that had just been paid for. Now the check
    and the mint are the same statement, and that window does not exist.
    """
    offer_token = new_id("off")
    expires_expr = (
        "CURRENT_TIMESTAMP + (%s || ' seconds')::interval" if _is_postgres()
        else "datetime('now', '+' || %s || ' seconds')"
    )
    columns = """offer_token, merchant_id, checkout_id, recovery_attempt_id,
                 requested_percent, approved_percent, decision, reason,
                 base_amount_paise, final_amount_paise, expires_at"""
    values = (offer_token, merchant_id, checkout_id, recovery_attempt_id, requested_percent,
              approved_percent, decision, reason, base_amount_paise, final_amount_paise,
              expires_in_seconds)

    with get_db() as conn:
        cursor = conn.cursor()
        if checkout_id:
            cursor.execute(
                f"""
                INSERT INTO offer_tokens ({columns})
                SELECT %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, {expires_expr}
                FROM checkouts c
                WHERE c.checkout_id = %s
                  AND COALESCE(c.status, '') <> 'paid'
                  AND c.paid_at IS NULL
                RETURNING *
                """,
                values + (checkout_id,),
            )
        else:
            cursor.execute(
                f"""
                INSERT INTO offer_tokens ({columns})
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, {expires_expr})
                RETURNING *
                """,
                values,
            )
        row = cursor.fetchone()
        # None means the WHERE matched nothing: the cart is paid, or gone.
        # No token exists, so there is nothing spendable to leak.
        return dict(row) if row else None


def _is_postgres() -> bool:
    from app.db.database import dialect
    return dialect() == "postgres"


def get_offer_token(offer_token: str) -> Optional[Dict[str, Any]]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM offer_tokens WHERE offer_token = %s", (offer_token,))
        row = cursor.fetchone()
    return dict(row) if row else None


def consume_offer_token(offer_token: str, merchant_id: str, checkout_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Validates and atomically consumes an offer token. Raises ValueError with
    a specific reason on any failure - the caller (issue_offer tool) turns
    that into a REJECTED tool result, never a silent fallback amount.
    """
    token = get_offer_token(offer_token)
    if not token:
        raise ValueError("offer_token_not_found")
    if token["merchant_id"] != merchant_id:
        raise ValueError("offer_token_merchant_mismatch")
    if checkout_id is not None and token["checkout_id"] not in (None, checkout_id):
        raise ValueError("offer_token_checkout_mismatch")
    if token["consumed_at"] is not None:
        raise ValueError("offer_token_already_consumed")
    if token["decision"] == "DENY":
        raise ValueError("offer_token_was_denied")

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE offer_tokens SET consumed_at = NOW()
            WHERE offer_token = %s AND consumed_at IS NULL AND expires_at > NOW()
            """,
            (offer_token,),
        )
        if cursor.rowcount == 0:
            raise ValueError("offer_token_expired")
        cursor.execute("SELECT * FROM offer_tokens WHERE offer_token = %s", (offer_token,))
        return dict(cursor.fetchone())
