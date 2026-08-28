from decimal import Decimal
from typing import Optional, Dict, Any
from app.db.database import get_db
from app.core.ids import new_id


def create_recovery_attempt(
    merchant_id: str, checkout_id: str, customer_id: Optional[str] = None
) -> Dict[str, Any]:
    recovery_attempt_id = new_id("rec")
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO recovery_attempts (recovery_attempt_id, merchant_id, checkout_id, customer_id)
            VALUES (%s, %s, %s, %s)
            """,
            (recovery_attempt_id, merchant_id, checkout_id, customer_id),
        )
        cursor.execute(
            "SELECT * FROM recovery_attempts WHERE recovery_attempt_id = %s", (recovery_attempt_id,)
        )
        return dict(cursor.fetchone())


def get_recovery_attempt(recovery_attempt_id: str) -> Optional[Dict[str, Any]]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM recovery_attempts WHERE recovery_attempt_id = %s", (recovery_attempt_id,)
        )
        row = cursor.fetchone()
    return dict(row) if row else None


def update_state(recovery_attempt_id: str, state: str, **fields) -> None:
    set_clauses = ["state = %s", "updated_at = NOW()"]
    values = [state]
    for key, value in fields.items():
        set_clauses.append(f"{key} = %s")
        values.append(value)
    values.append(recovery_attempt_id)
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"UPDATE recovery_attempts SET {', '.join(set_clauses)} WHERE recovery_attempt_id = %s",
            values,
        )


# The states an attempt can END in. Anything else counts as still in
# flight, and an in-flight attempt BLOCKS a new recovery for that checkout
# (see recovery_eligibility._check_active_recovery).
#
# This list previously read ('RECOVERED', 'FAILED', 'OPTED_OUT') - but
# nothing in the codebase ever writes 'FAILED' or 'OPTED_OUT'. The real
# states are CALL_FAILED and CONSENT_REVOKED, so a correctly-failed call
# never counted as finished and permanently blocked that checkout from
# being retried.
TERMINAL_STATES = ("RECOVERED", "CALL_FAILED", "CONSENT_REVOKED")


def list_active_for_checkout(checkout_id: str) -> list:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM recovery_attempts WHERE checkout_id = %s "
            "AND state NOT IN %s",
            (checkout_id, TERMINAL_STATES),
        )
        return cursor.fetchall()


def expire_stale_calls(older_than_minutes: int = 10) -> list:
    """Closes out attempts stuck mid-call.

    A call can end without any completion signal reaching us at all: Twilio
    exceeds its webhook deadline and hangs up, the customer drops, the
    network dies mid-turn. Nothing then moves the attempt out of CALLING, so
    it sits in flight forever - and because an in-flight attempt blocks new
    recovery for that checkout, a single dropped call meant that cart could
    NEVER be recovered again. Seven such rows accumulated during live
    testing, the oldest stuck for over an hour.

    Returns the attempts it closed so the caller can publish
    recovery.call_failed for each.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE recovery_attempts
            SET state = 'CALL_FAILED', updated_at = NOW()
            WHERE state IN ('CALLING', 'OUTREACH_APPROVED')
              AND updated_at < NOW() - (%s || ' minutes')::interval
            RETURNING recovery_attempt_id, merchant_id, checkout_id
            """,
            (str(older_than_minutes),),
        )
        return [dict(r) for r in cursor.fetchall()]


def list_for_merchant(merchant_id: str, limit: int = 100) -> list:
    """Real recovery attempts for the dashboard's Recoveries table - joined
    with checkout amount and customer name/phone so the table doesn't need
    N+1 lookups. Replaces the old dashboard's reliance on the in-memory,
    unscoped, restart-losing event bus log."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                ra.recovery_attempt_id, ra.checkout_id, ra.customer_id, ra.state, ra.channel,
                ra.approved_discount_percent, ra.final_amount_paise, ra.attributed_revenue_paise,
                ra.rzp_payment_link_id, ra.created_at, ra.updated_at,
                c.amount_paise AS cart_amount_paise, c.currency,
                cu.name AS customer_name, cu.phone AS customer_phone, cu.email AS customer_email
            FROM recovery_attempts ra
            LEFT JOIN checkouts c ON c.checkout_id = ra.checkout_id
            LEFT JOIN customers cu ON cu.customer_id = ra.customer_id
            WHERE ra.merchant_id = %s
            ORDER BY ra.created_at DESC
            LIMIT %s
            """,
            (merchant_id, limit),
        )
        return cursor.fetchall()


def summary_stats(merchant_id: str) -> Dict[str, Any]:
    """Real, DB-backed KPIs for the Overview page - every number here comes
    from an actual row, not the event bus. A merchant with zero activity
    gets real zeros/None, never a fabricated benchmark."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                COUNT(*) AS total_attempts,
                COUNT(*) FILTER (WHERE state = 'RECOVERED') AS recovered_count,
                COALESCE(SUM(attributed_revenue_paise) FILTER (WHERE state = 'RECOVERED'), 0) AS recovered_paise,
                COUNT(*) FILTER (WHERE state IN ('CALLING', 'OUTREACH_APPROVED', 'PAYMENT_LINK_SENT')) AS active_count,
                COUNT(*) FILTER (WHERE state = 'CONSENT_REVOKED') AS opted_out_count,
                COUNT(*) FILTER (WHERE state = 'CALL_FAILED') AS call_failed_count,
                COUNT(*) FILTER (WHERE state = 'PROMISED') AS promised_count,
                COALESCE(SUM(promised_amount_paise) FILTER (WHERE state = 'PROMISED'), 0) AS promised_paise
            FROM recovery_attempts
            WHERE merchant_id = %s
            """,
            (merchant_id,),
        )
        row = dict(cursor.fetchone())

        cursor.execute(
            """
            SELECT COALESCE(SUM(amount_paise), 0) AS at_risk_paise, COUNT(*) AS abandoned_count
            FROM checkouts
            WHERE merchant_id = %s AND status = 'abandoned'
            """,
            (merchant_id,),
        )
        risk_row = dict(cursor.fetchone())

    # Postgres SUM()/COUNT() return Decimal, which FastAPI serializes as a
    # JSON *string* ("10576500") rather than a number. Every consumer then
    # either coerces it by luck (JS `paise / 100` happens to work on a
    # string) or gets a type that contradicts the declared API contract.
    # Money is integer paise in this system, so cast at the repo boundary
    # and hand every caller a real int.
    row = {k: (int(v) if isinstance(v, Decimal) else v) for k, v in row.items()}
    at_risk = risk_row["at_risk_paise"]
    abandoned = risk_row["abandoned_count"]

    total = row["total_attempts"]
    row["recovery_rate_pct"] = round(100 * row["recovered_count"] / total, 1) if total else None
    row["revenue_at_risk_paise"] = int(at_risk) if isinstance(at_risk, Decimal) else at_risk
    row["abandoned_count"] = int(abandoned) if isinstance(abandoned, Decimal) else abandoned
    return row


def count_calls_for_checkout(checkout_id: str) -> int:
    """How many times this checkout has already been dialled.

    Counts attempts that reached at least the dialling stage - CREATED
    alone means the attempt was opened but consent or a guard stopped it
    before any call, and that should not burn the customer's call budget.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT COUNT(*) AS n FROM recovery_attempts
            WHERE checkout_id = %s
              AND state IN ('CALLING', 'PAYMENT_LINK_SENT', 'RECOVERED', 'CALL_FAILED')
            """,
            (checkout_id,),
        )
        return int(dict(cursor.fetchone())["n"])


def list_lapsed_promises(limit: int = 50) -> list:
    """Promises whose date has passed while the checkout is still unpaid.

    A promise pauses outreach - that is the whole point of recording it. But
    a promise that lapses silently is just a lost sale, so once the date
    passes we allow exactly ONE reminder. promise_reminded_at is what makes
    it one and not a campaign: a customer who committed and then didn't pay
    has already been chased once, and chasing them repeatedly is how a
    recovery tool becomes the thing merchants get complaints about.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT ra.recovery_attempt_id, ra.merchant_id, ra.checkout_id, ra.customer_id,
                   ra.promised_at, ra.promised_amount_paise
            FROM recovery_attempts ra
            JOIN checkouts c ON c.checkout_id = ra.checkout_id
            WHERE ra.state = 'PROMISED'
              AND ra.promised_at IS NOT NULL
              AND ra.promised_at < NOW()
              AND ra.promise_reminded_at IS NULL
              AND c.status != 'paid'
            LIMIT %s
            """,
            (limit,),
        )
        return [dict(r) for r in cursor.fetchall()]


def mark_promise_reminded(recovery_attempt_id: str) -> None:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE recovery_attempts SET promise_reminded_at = NOW(), updated_at = NOW() "
            "WHERE recovery_attempt_id = %s",
            (recovery_attempt_id,),
        )


def active_promise_for_checkout(checkout_id: str) -> Optional[Dict[str, Any]]:
    """A promise whose date has NOT yet passed. While one exists, outreach
    for that checkout is paused - that is what makes recording a promise a
    stopping rule rather than a note in a database."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT recovery_attempt_id, promised_at FROM recovery_attempts
            WHERE checkout_id = %s AND state = 'PROMISED'
              AND promised_at IS NOT NULL AND promised_at > NOW()
            LIMIT 1
            """,
            (checkout_id,),
        )
        row = cursor.fetchone()
    return dict(row) if row else None
