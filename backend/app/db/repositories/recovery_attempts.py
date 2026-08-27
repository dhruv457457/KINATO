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


def list_active_for_checkout(checkout_id: str) -> list:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM recovery_attempts WHERE checkout_id = %s "
            "AND state NOT IN ('RECOVERED', 'FAILED', 'OPTED_OUT')",
            (checkout_id,),
        )
        return cursor.fetchall()
