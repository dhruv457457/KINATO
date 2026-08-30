"""
One row per agent tool invocation: who asked for what, what the deterministic
engine decided, whether the call was degraded (heuristic fallback used), and
how long it took. Backs the dashboard's Activity feed and the recovery
detail drawer's audit timeline.
"""
import json
from typing import Optional, Dict, Any, List
from app.db.database import get_db
from app.core.ids import new_id


def record_audit(
    actor: str,
    action: str,
    merchant_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
    args: Optional[dict] = None,
    result: Optional[dict] = None,
    decision: Optional[str] = None,
    degraded: bool = False,
    latency_ms: Optional[int] = None,
) -> Dict[str, Any]:
    audit_id = new_id("aud")
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO audit_log (audit_id, merchant_id, correlation_id, actor, action,
                                    args, result, decision, degraded, latency_ms)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (audit_id, merchant_id, correlation_id, actor, action,
             json.dumps(args or {}), json.dumps(result or {}), decision, degraded, latency_ms),
        )
        # RETURNING, not a follow-up SELECT. The row was already in hand
        # after the INSERT; asking for it again cost a second round trip,
        # and from Railway a round trip is not microseconds.
        return dict(cursor.fetchone())


def get_audit_trail_for_correlation(correlation_id: str) -> List[Dict[str, Any]]:
    """Everything that happened for one checkout/recovery attempt, in order -
    this is what backs the recovery detail drawer.

    `audit_id` breaks the tie on created_at. Audit writes are backgrounded
    now (see app/agents/audit.py), so two tools finishing inside the same
    clock tick can land with identical timestamps, and the row order was
    then whatever Postgres felt like - different on every read of the same
    trail.

    This makes that order STABLE, not causal: audit_id is a random uuid, so
    it cannot say which tool ran first. If real ties ever show up in the
    drawer, the fix is a monotonic sequence column, not a cleverer sort."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM audit_log WHERE correlation_id = %s ORDER BY created_at ASC, audit_id ASC",
            (correlation_id,),
        )
        return cursor.fetchall()


def recent_audit(merchant_id: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
    with get_db() as conn:
        cursor = conn.cursor()
        if merchant_id:
            cursor.execute(
                "SELECT * FROM audit_log WHERE merchant_id = %s ORDER BY created_at DESC LIMIT %s",
                (merchant_id, limit),
            )
        else:
            cursor.execute("SELECT * FROM audit_log ORDER BY created_at DESC LIMIT %s", (limit,))
        return cursor.fetchall()
