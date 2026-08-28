"""
Durable copy of the event bus. The bus (app/gateway/event_bus.py) stays
in-memory for hot-path speed and is what services actually subscribe to;
this table is what survives a restart, and its idempotency_key UNIQUE
constraint is the durable version of the bus's old unbounded in-memory
_processed_idempotency_keys set.
"""
import json
from typing import Optional, List, Dict, Any
from app.db.database import get_db
from app.core.ids import new_id


def persist_event(
    event_type: str,
    payload: dict,
    merchant_id: Optional[str],
    correlation_id: Optional[str],
    idempotency_key: Optional[str] = None,
) -> bool:
    """Returns False if idempotency_key was already seen (row not inserted),
    True otherwise. Never raises - a persistence failure must not take down
    the in-memory bus publish it's shadowing."""
    event_id = new_id("evt")
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO events (event_id, merchant_id, correlation_id, event_type, payload, idempotency_key)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (idempotency_key) DO NOTHING
                """,
                (event_id, merchant_id, correlation_id, event_type, json.dumps(payload), idempotency_key),
            )
            return cursor.rowcount > 0
    except Exception:
        return False


def count_blocked_reasons(merchant_id: str, limit: int = 500) -> Dict[str, int]:
    """Counts why recovery attempts never got off the ground, per reason.

    These are real `recovery.blocked` events - a payment failed (so there IS
    money on the table) but Kinato deliberately did not reach out, because
    there was no contact info on file, or Razorpay's own rail was down.
    Surfacing this is what turns a silent no-op into something the merchant
    can act on.

    Grouping happens in Python rather than SQL because `payload` is a TEXT
    column holding JSON, and the JSON operators differ between Postgres and
    SQLite - see app/db/database.py's dialect note on why this schema is
    written once and translated, not duplicated.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT payload FROM events
            WHERE merchant_id = %s AND event_type = 'recovery.blocked'
            ORDER BY created_at DESC LIMIT %s
            """,
            (merchant_id, limit),
        )
        rows = cursor.fetchall()

    counts: Dict[str, int] = {}
    for row in rows:
        payload = row["payload"]
        try:
            payload = json.loads(payload) if isinstance(payload, str) else payload
        except (TypeError, ValueError):
            continue
        reason = (payload or {}).get("reason") or "unknown"
        counts[reason] = counts.get(reason, 0) + 1
    return counts


def recent_events(merchant_id: Optional[str] = None, limit: int = 300) -> List[Dict[str, Any]]:
    with get_db() as conn:
        cursor = conn.cursor()
        if merchant_id:
            cursor.execute(
                "SELECT * FROM events WHERE merchant_id = %s ORDER BY created_at DESC LIMIT %s",
                (merchant_id, limit),
            )
        else:
            cursor.execute("SELECT * FROM events ORDER BY created_at DESC LIMIT %s", (limit,))
        rows = cursor.fetchall()
    for row in rows:
        try:
            row["payload"] = json.loads(row["payload"])
        except (TypeError, ValueError):
            pass
    return rows
