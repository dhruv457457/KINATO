from datetime import datetime, timedelta, timezone
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
# CALLBACK_REQUESTED is terminal for THIS attempt deliberately: the
# customer asked us to try again later, so this attempt is finished and
# must stop blocking the checkout - the whole point is that a further
# attempt becomes possible (see outreach_guards' callback exemption).
TERMINAL_STATES = (
    "RECOVERED", "CALL_FAILED", "CONSENT_REVOKED", "CALLBACK_REQUESTED",
    # A guard stopped this one before dialling. Terminal for THIS attempt
    # so it stops blocking the checkout - the case becomes eligible again
    # the moment the reason expires (quiet hours end, a promise date
    # passes), which is the whole point of stopping rather than failing.
    "BLOCKED",
)


def list_active_for_checkout(checkout_id: str) -> list:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM recovery_attempts WHERE checkout_id = %s "
            "AND state NOT IN %s",
            (checkout_id, TERMINAL_STATES),
        )
        return cursor.fetchall()


def find_reusable_payment_link(
    merchant_id: str, checkout_id: str, final_amount_paise: int
) -> Optional[Dict[str, Any]]:
    """A live payment link already minted for this exact cart at this exact price.

    Every attempt used to mint a brand-new Razorpay link, including a retry
    of the same cart at the same amount. Razorpay's test mode caps an
    account at thirty links in total, forever - and this project has hit
    that wall twice, each time presenting as a broken integration rather
    than as an exhausted quota (FINDINGS #7).

    The same link for the same cart at the same price is not a workaround,
    it is the correct artifact: a customer who is sent it twice receives
    the thing they were already promised.

    Three conditions, and every one of them is load-bearing:

      merchant_id  - a link belongs to the merchant's own Razorpay account.
      checkout_id  - it is payable for one specific order.
      amount       - EXACT match, never "close enough". A link reused at the
                     wrong price charges a customer an amount nobody
                     approved, which is worse than any number of extra
                     links. This is why the comparison is on
                     final_amount_paise (integer paise) rather than on the
                     discount percent, which is a float and rounds.

    Expiry is read from the stored column, not inferred: updated_at is
    refreshed by every later update_state on the attempt.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM recovery_attempts
            WHERE merchant_id = %s
              AND checkout_id = %s
              AND final_amount_paise = %s
              AND rzp_payment_link_url IS NOT NULL
              AND rzp_payment_link_id IS NOT NULL
              AND rzp_payment_link_expires_at IS NOT NULL
              AND rzp_payment_link_expires_at > CURRENT_TIMESTAMP
            ORDER BY rzp_payment_link_expires_at DESC
            LIMIT 1
            """,
            (merchant_id, checkout_id, final_amount_paise),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


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
                COUNT(*) FILTER (WHERE state = 'BLOCKED') AS blocked_count,
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


def get_by_call_sid(call_sid: str) -> Optional[Dict[str, Any]]:
    """Find the recovery attempt a live Twilio call belongs to.

    A mid-call webhook carries the CallSid and nothing else, so this is the
    only route back to the attempt when the in-memory session is gone -
    after a restart, or on a second worker process that never handled this
    call's /voice/outbound. Newest first, because a CallSid is unique in
    practice but nothing in the schema promises it.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM recovery_attempts WHERE twilio_call_sid = %s "
            "ORDER BY created_at DESC LIMIT 1",
            (call_sid,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def list_for_customer(customer_id: str, limit: int = 10) -> list:
    """This customer's recovery attempts, newest first.

    Across every checkout, deliberately: what matters for the next
    conversation is what happened with this PERSON, not with one cart. A
    customer who broke a promise on a different order is still a customer
    who broke a promise.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM recovery_attempts WHERE customer_id = %s "
            "ORDER BY created_at DESC LIMIT %s",
            (customer_id, limit),
        )
        return [dict(r) for r in cursor.fetchall()]


# States that mean we actually reached out. CREATED alone means the attempt
# was opened and then stopped by consent or a guard before anything left the
# building, and that must not burn the customer's contact budget - the
# distinction count_calls_for_checkout has always made, kept in one place
# now that two counters depend on it.
#
# CONSENT_REVOKED is deliberately absent: it is written by the PRE-DIAL
# consent gate as well as by an opt-out mid-call, and counting a customer's
# refusal as a contact would be exactly backwards.
CONTACTED_STATES = (
    "CALLING", "PAYMENT_LINK_SENT", "RECOVERED", "CALL_FAILED",
    "PROMISED", "CALLBACK_REQUESTED",
)


def count_recent_by_channel(checkout_id: str, within_hours: int = 24) -> Dict[str, int]:
    """How many times this checkout has been contacted on each channel in
    the last `within_hours`.

    A lifetime count answers "have we bothered them too much ever"; this
    answers "have we bothered them too much today", and the second question
    is the one a customer actually experiences. Both are enforced.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT channel, COUNT(*) AS n FROM recovery_attempts
            WHERE checkout_id = %s
              AND channel IS NOT NULL
              AND state IN %s
              AND created_at > NOW() - INTERVAL '{int(within_hours)} hours'
            GROUP BY channel
            """,
            (checkout_id, CONTACTED_STATES),
        )
        return {dict(r)["channel"]: dict(r)["n"] for r in cursor.fetchall()}


def count_outreach_for_checkout(checkout_id: str) -> int:
    """Every outreach attempt on this checkout, on any channel, ever.

    count_calls_for_checkout counts only calls. Once email became a real
    channel, a lifetime cap that counted calls alone would let a customer
    be contacted twice by phone and then indefinitely by email while the
    cap reported itself as holding.

    Counted by STATE, not by whether a channel happens to be recorded. The
    first version of this filtered on `channel IS NOT NULL`, which quietly
    stopped counting every attempt that never got as far as choosing one -
    and the scoreboard immediately reported a rule break, because a
    checkout already at its cap was waved through. The states are the
    record of what actually happened; the channel column is a label on it.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) AS n FROM recovery_attempts "
            "WHERE checkout_id = %s AND state IN %s",
            (checkout_id, CONTACTED_STATES),
        )
        row = cursor.fetchone()
        return dict(row)["n"] if row else 0


def callback_requested(checkout_id: str) -> bool:
    """Did the customer themselves ask us to call back?

    The one thing that may lift the outreach cap, and it must be a stored
    fact rather than an inference - "they asked us to" is the entire
    difference between a follow-up and a nuisance call.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM recovery_attempts WHERE checkout_id = %s "
            "AND callback_requested_at IS NOT NULL LIMIT 1",
            (checkout_id,),
        )
        return cursor.fetchone() is not None


def daily_series(merchant_id: str, days: int = 14) -> list:
    """Recovered money per day, for the last `days` days.

    Returns one row per day INCLUDING days with nothing, because a gap in a
    chart and a zero in a chart say different things and only one of them
    is true. Days are generated in Python rather than with a SQL series
    join: the schema is written once and dialect-translated (see
    app/db/database.py), and generate_series does not exist in SQLite.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT CAST(updated_at AS DATE) AS day,
                   COUNT(*) AS recovered_count,
                   COALESCE(SUM(attributed_revenue_paise), 0) AS recovered_paise
            FROM recovery_attempts
            WHERE merchant_id = %s
              AND state = 'RECOVERED'
              AND updated_at > NOW() - INTERVAL '{int(days)} days'
            GROUP BY CAST(updated_at AS DATE)
            """,
            (merchant_id,),
        )
        by_day = {}
        for r in cursor.fetchall():
            d = dict(r)
            by_day[str(d["day"])] = {
                "recovered_count": int(d["recovered_count"] or 0),
                "recovered_paise": int(d["recovered_paise"] or 0),
            }

    today = datetime.now(timezone.utc).date()
    out = []
    for offset in range(days - 1, -1, -1):
        day = today - timedelta(days=offset)
        found = by_day.get(str(day), {"recovered_count": 0, "recovered_paise": 0})
        out.append({"day": str(day), **found})
    return out


def channel_breakdown(merchant_id: str) -> Dict[str, Any]:
    """How recoveries were actually reached, and how they ended.

    Outreach is not one thing. A merchant looking at a single recovery
    count cannot tell a phone operation from an email one, and the two have
    completely different costs.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT COALESCE(channel, 'unknown') AS channel,
                   COUNT(*) AS attempts,
                   COUNT(*) FILTER (WHERE state = 'RECOVERED') AS recovered
            FROM recovery_attempts
            WHERE merchant_id = %s AND channel IS NOT NULL
            GROUP BY COALESCE(channel, 'unknown')
            """,
            (merchant_id,),
        )
        by_channel = [
            {k: (int(v) if isinstance(v, (Decimal, int)) and k != "channel" else v)
             for k, v in dict(r).items()}
            for r in cursor.fetchall()
        ]

        cursor.execute(
            """
            SELECT state, COUNT(*) AS n FROM recovery_attempts
            WHERE merchant_id = %s GROUP BY state
            """,
            (merchant_id,),
        )
        by_state = {dict(r)["state"]: int(dict(r)["n"]) for r in cursor.fetchall()}

    return {"by_channel": by_channel, "by_state": by_state}


def get_call_context(recovery_attempt_id: str) -> Optional[Dict[str, Any]]:
    """Everything a live call needs to open, in ONE round trip.

    Loading this as four separate reads - attempt, checkout, customer,
    merchant - cost four sequential round trips, and each is 2-2.8s from
    Railway to Supabase (see policies.py). Twilio hangs up on the customer
    after about 15 seconds, so four reads plus any bookkeeping put the
    opening webhook straight through that ceiling and the caller heard "we
    cannot reach your server".

    Joined rather than parallelised on purpose. FINDINGS #8 measured
    concurrent reads against this database as 80% SLOWER, because
    overlapping round trips cost less than the extra connections they
    force. One query avoids the question entirely: it is fewer round trips
    AND fewer connections.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT ra.*,
                   c.line_items        AS checkout_line_items,
                   c.failure_class     AS checkout_failure_class,
                   c.amount_paise      AS checkout_amount_paise,
                   cu.name             AS customer_name,
                   m.name              AS merchant_name
            FROM recovery_attempts ra
            LEFT JOIN checkouts  c  ON c.checkout_id  = ra.checkout_id
            LEFT JOIN customers  cu ON cu.customer_id = ra.customer_id
            LEFT JOIN merchants  m  ON m.merchant_id  = ra.merchant_id
            WHERE ra.recovery_attempt_id = %s
            """,
            (recovery_attempt_id,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None
