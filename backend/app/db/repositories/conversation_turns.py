"""
What was actually said, on both sides, durably.

Before this existed, a live call's dialogue lived in `voice_runtime`'s
module-global `CALL_SESSIONS` dict and in `agent_runtime`'s in-process
message store. Four separate problems came out of that one decision:

  * The recovery drawer showed every tool call the agent made and none of
    the conversation those calls were a response to.
  * A second worker process could not serve a turn of a call the first one
    started.
  * A restart mid-call answered the customer with "I lost track of our
    order details."
  * Every later recovery attempt for the same customer began from nothing,
    re-asking a question they had already answered.

Append-only by construction. A turn is a record of something that was said;
there is no circumstance in which the right response to it is an UPDATE.
"""
from typing import Any, Dict, List, Optional

from app.core.ids import new_id
from app.db.database import get_db

# Speakers. Kept as constants because "agent"/"customer" ends up in a UI
# and in prompt reconstruction, and a typo'd string would silently produce
# a transcript where one side vanishes.
AGENT = "agent"
CUSTOMER = "customer"


def record_turn(
    merchant_id: str,
    recovery_attempt_id: str,
    turn_index: int,
    speaker: str,
    text: str,
    customer_id: Optional[str] = None,
    channel: str = "voice",
    stt_confidence: Optional[float] = None,
    input_mode: str = "speech",
) -> Dict[str, Any]:
    """Writes one side of one exchange.

    Never raises into a live call: see `record_turn_safe`, which is what
    the voice path actually uses. This one is for callers that want to know.
    """
    assert speaker in (AGENT, CUSTOMER), f"unknown speaker: {speaker!r}"
    turn_id = new_id("turn")
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO conversation_turns (
                turn_id, merchant_id, recovery_attempt_id, customer_id, turn_index,
                speaker, text, channel, stt_confidence, input_mode
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                turn_id, merchant_id, recovery_attempt_id, customer_id, turn_index,
                speaker, text, channel, stt_confidence, input_mode,
            ),
        )
        cursor.execute("SELECT * FROM conversation_turns WHERE turn_id = %s", (turn_id,))
        return dict(cursor.fetchone())


def list_for_attempt(recovery_attempt_id: str) -> List[Dict[str, Any]]:
    """The full transcript of one recovery attempt, in order."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM conversation_turns WHERE recovery_attempt_id = %s "
            "ORDER BY turn_index ASC, created_at ASC",
            (recovery_attempt_id,),
        )
        return [dict(r) for r in cursor.fetchall()]


def next_turn_index(recovery_attempt_id: str) -> int:
    """The index the next turn should take.

    Read from the table rather than from an in-memory counter on purpose:
    the counter is the thing that does not survive the restart this table
    exists to survive.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT MAX(turn_index) AS max_index FROM conversation_turns WHERE recovery_attempt_id = %s",
            (recovery_attempt_id,),
        )
        row = cursor.fetchone()
    current = (dict(row).get("max_index") if row else None)
    return (current + 1) if current is not None else 0


def list_recent_for_customer(customer_id: str, limit: int = 40) -> List[Dict[str, Any]]:
    """This customer's most recent turns across ALL of their recovery
    attempts, newest first - the raw material for the cross-attempt brief
    in app/services/customer_memory.py."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM conversation_turns WHERE customer_id = %s "
            "ORDER BY created_at DESC LIMIT %s",
            (customer_id, limit),
        )
        return [dict(r) for r in cursor.fetchall()]
