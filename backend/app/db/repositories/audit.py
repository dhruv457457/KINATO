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
            """,
            (audit_id, merchant_id, correlation_id, actor, action,
             json.dumps(args or {}), json.dumps(result or {}), decision, degraded, latency_ms),
        )
        cursor.execute("SELECT * FROM audit_log WHERE audit_id = %s", (audit_id,))
        return dict(cursor.fetchone())


def get_audit_trail_for_correlation(correlation_id: str) -> List[Dict[str, Any]]:
    """Everything that happened for one checkout/recovery attempt, in order -
    this is what backs the recovery detail drawer."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM audit_log WHERE correlation_id = %s ORDER BY created_at ASC",
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
