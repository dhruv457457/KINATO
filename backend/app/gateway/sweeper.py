"""
Abandonment sweeper - the durable, restart-safe replacement for an in-memory
`asyncio.sleep(window)` per checkout. Runs on a fixed interval, queries the
`checkouts` table (the real source of truth, updated by
attribution.py/webhooks.py whenever a payment actually succeeds) for
still-`started` checkouts older than their merchant's configured
`abandonment_window_seconds`, and fires `checkout.abandoned` for each.

Why a sweeper instead of a task-per-checkout: the old approach loses every
pending timer on a process restart (that checkout is then silently never
recovered), and doesn't work at all across multiple worker processes. A
30-second poll against an indexed query (idx_checkouts_status_started) is
cheap and correct under both constraints.
"""
import asyncio
import logging
from typing import Dict, Any

from app.db.database import get_db, dialect
from app.gateway.event_bus import bus
from app.db.repositories import recovery_attempts as recovery_attempts_repo

logger = logging.getLogger(__name__)

SWEEP_INTERVAL_SECONDS = 30

# How long an attempt may sit in CALLING before we treat the call as dead.
# Generous enough that a genuinely long conversation is never cut short,
# short enough that a dropped call doesn't block that cart for hours.
STALE_CALL_MINUTES = 10


def _stale_started_checkouts() -> list:
    """One real, dialect-aware query. Postgres gets FOR UPDATE SKIP LOCKED so
    multiple worker processes never double-process the same row; SQLite
    (single-process, no real row locking) just skips that clause."""
    with get_db() as conn:
        cursor = conn.cursor()
        if dialect() == "postgres":
            cursor.execute("""
                SELECT c.* FROM checkouts c
                JOIN merchant_policies p ON p.merchant_id = c.merchant_id
                WHERE c.status = 'started'
                  AND c.started_at < NOW() - (p.abandonment_window_seconds || ' seconds')::interval
                LIMIT 200
                FOR UPDATE OF c SKIP LOCKED
            """)
        else:
            cursor.execute("""
                SELECT c.* FROM checkouts c
                JOIN merchant_policies p ON p.merchant_id = c.merchant_id
                WHERE c.status = 'started'
                  AND c.started_at < datetime('now', '-' || p.abandonment_window_seconds || ' seconds')
                LIMIT 200
            """)
        return cursor.fetchall()


async def sweep_once() -> int:
    """Runs one sweep pass. Returns how many checkouts were marked abandoned.
    Exposed as its own function (not just the loop) so tests can call it
    directly instead of waiting on a real 30s timer."""
    stale = await asyncio.to_thread(_stale_started_checkouts)
    fired = 0

    for checkout in stale:
        checkout_id = checkout["checkout_id"]
        merchant_id = checkout["merchant_id"]

        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE checkouts SET status = 'abandoned', abandoned_at = NOW() "
                "WHERE checkout_id = %s AND status = 'started'",
                (checkout_id,),
            )
            claimed = cursor.rowcount > 0

        if not claimed:
            continue  # another worker/sweep pass already claimed this row

        await bus.publish(
            event_type="checkout.abandoned",
            payload={
                "checkout_id": checkout_id,
                "cart_id": checkout.get("cart_id"),
                "customer_id": checkout.get("customer_id"),
                "merchant_id": merchant_id,
                "amount": checkout["amount_paise"] / 100.0,
                "amount_paise": checkout["amount_paise"],
                "currency": checkout.get("currency", "INR"),
            },
            correlation_id=checkout_id,
            merchant_id=merchant_id,
            idempotency_key=f"abandonment_v2_{checkout_id}",
        )
        fired += 1
        logger.info(f"Sweeper: checkout {checkout_id} abandoned (merchant {merchant_id}).")

    await _expire_stale_calls()
    return fired


async def _expire_stale_calls():
    """Close out attempts stuck mid-call.

    A call can end with no completion signal ever reaching us - Twilio gives
    up waiting for TwiML and hangs up, the customer drops, the network dies
    between turns. Nothing then moves the attempt out of CALLING, and since
    an in-flight attempt blocks new recovery for that checkout, one dropped
    call meant that cart could never be recovered again.
    """
    try:
        closed = recovery_attempts_repo.expire_stale_calls(STALE_CALL_MINUTES)
    except Exception as e:
        logger.warning(f"Sweeper: could not expire stale calls: {e}")
        return

    for attempt in closed:
        logger.info(
            f"Sweeper: recovery {attempt['recovery_attempt_id']} was stuck mid-call for over "
            f"{STALE_CALL_MINUTES}m - marking CALL_FAILED so the checkout can be retried."
        )
        await bus.publish(
            event_type="recovery.call_failed",
            payload={
                "recovery_attempt_id": attempt["recovery_attempt_id"],
                "checkout_id": attempt["checkout_id"],
                "reason": "call_never_completed",
            },
            correlation_id=attempt["checkout_id"],
            merchant_id=attempt["merchant_id"],
            idempotency_key=f"stale_call_{attempt['recovery_attempt_id']}",
        )


async def run_sweeper_loop(interval_seconds: int = SWEEP_INTERVAL_SECONDS):
    """Long-running loop started from FastAPI's lifespan. A single failed
    sweep pass is logged and retried on the next tick, never crashes the loop."""
    logger.info(f"Abandonment sweeper started (interval={interval_seconds}s).")
    while True:
        try:
            await sweep_once()
        except Exception as e:
            logger.error(f"Sweeper pass failed (will retry next tick): {e}", exc_info=True)
        await asyncio.sleep(interval_seconds)
