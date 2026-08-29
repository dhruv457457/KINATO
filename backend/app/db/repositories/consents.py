"""
Append-only consent ledger. NEVER call UPDATE against this table - a
revocation is recorded as its own new row with status='revoked', not by
flipping a bit on the granting row. This is what makes "the customer said
stop and we stopped" a fact you can prove, not just an in-memory `return True`
(see the old app/services/identity_service.py, which hardcoded consent).
"""
from typing import Optional, Dict, Any
from app.db.database import get_db
from app.core.ids import new_id


def record_consent(
    merchant_id: str,
    customer_id: str,
    channel: str,
    status: str,
    source: str,
    policy_version: str = "",
    evidence: str = "",
) -> Dict[str, Any]:
    assert status in ("granted", "revoked")
    consent_id = new_id("cons")
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO consents (consent_id, merchant_id, customer_id, channel, status,
                                   source, policy_version, evidence)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (consent_id, merchant_id, customer_id, channel, status, source, policy_version, evidence),
        )
        cursor.execute("SELECT * FROM consents WHERE consent_id = %s", (consent_id,))
        return dict(cursor.fetchone())


def check_consent(merchant_id: str, customer_id: str, channel: str) -> bool:
    """The current state is whichever row is latest - never an UPDATE."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT status FROM consents
            WHERE merchant_id = %s AND customer_id = %s AND channel = %s
            ORDER BY created_at DESC LIMIT 1
            """,
            (merchant_id, customer_id, channel),
        )
        row = cursor.fetchone()
    return bool(row) and row["status"] == "granted"


def get_consent_history(merchant_id: str, customer_id: str) -> list:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM consents WHERE merchant_id = %s AND customer_id = %s
            ORDER BY created_at DESC
            """,
            (merchant_id, customer_id),
        )
        return cursor.fetchall()


def has_opted_out(merchant_id: str, customer_id: str) -> bool:
    """True if this customer has revoked consent on ANY channel.

    "Don't contact me again" is a statement about being contacted, not
    about a protocol. Someone who says it on a phone call has not asked to
    keep receiving the emails, and a per-channel check would let exactly
    that happen - most sharply for the promise-to-pay reminder, which is
    sent days after the call where they might have said it.

    Deliberately asymmetric with check_consent(): consent to CONTACT is
    per-channel and must be granted explicitly, while a refusal is read as
    broadly as possible. The safe direction differs for the two questions.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT channel, status FROM consents
            WHERE merchant_id = %s AND customer_id = %s
            ORDER BY created_at DESC
            """,
            (merchant_id, customer_id),
        )
        rows = [dict(r) for r in cursor.fetchall()]
    latest_by_channel: Dict[str, str] = {}
    for row in rows:  # newest first, so the first sighting of a channel is current
        latest_by_channel.setdefault(row["channel"], row["status"])
    return any(status == "revoked" for status in latest_by_channel.values())
