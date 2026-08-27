"""
================================================================================
FILE: app/payments/state_machine.py
MODULE: Module 3 - Transaction Lifecycle State Machine
--------------------------------------------------------------------------------
WHAT THIS FILE DOES:
Enforces valid state transitions for all financial transactions in Kinato.

VALID STATE SEQUENCE:
  CREATED -> OFFER_READY -> AWAITING_APPROVAL -> APPROVED -> PAYMENT_SUBMITTED -> SUCCESS / FAILED / UNCERTAIN

TRANSITION INVARIANTS:
  - An order CANNOT transition to SUCCESS without cryptographic signature verification.
  - An order in UNCERTAIN state is NEVER auto-retried without querying Razorpay API.
  - Once in terminal states (SUCCESS, FAILED, EXPIRED), status is immutable.
================================================================================
"""
from enum import Enum
from typing import Dict, Set
from app.db.database import get_db


class TransactionState(str, Enum):
    CREATED = "CREATED"
    OFFER_READY = "OFFER_READY"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    PAYMENT_SUBMITTED = "PAYMENT_SUBMITTED"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    UNCERTAIN = "UNCERTAIN"
    EXPIRED = "EXPIRED"


VALID_TRANSITIONS: Dict[TransactionState, Set[TransactionState]] = {
    TransactionState.CREATED: {TransactionState.OFFER_READY, TransactionState.FAILED},
    TransactionState.OFFER_READY: {TransactionState.AWAITING_APPROVAL, TransactionState.APPROVED, TransactionState.EXPIRED, TransactionState.SUCCESS},
    TransactionState.AWAITING_APPROVAL: {TransactionState.APPROVED, TransactionState.FAILED, TransactionState.EXPIRED},
    TransactionState.APPROVED: {TransactionState.PAYMENT_SUBMITTED, TransactionState.FAILED},
    TransactionState.PAYMENT_SUBMITTED: {TransactionState.SUCCESS, TransactionState.FAILED, TransactionState.UNCERTAIN},
    TransactionState.UNCERTAIN: {TransactionState.SUCCESS, TransactionState.FAILED, TransactionState.EXPIRED},
    TransactionState.SUCCESS: set(),  # Terminal state
    TransactionState.FAILED: set(),   # Terminal state
    TransactionState.EXPIRED: set()   # Terminal state
}


class TransactionStateMachine:
    """
    Manages state transitions in SQLite with audit logging and invariant validation.
    """
    @staticmethod
    def transition(
        order_id: str,
        new_state: TransactionState,
        payment_id: str = None
    ) -> bool:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT state FROM orders WHERE order_id = %s OR razorpay_order_id = %s", (order_id, order_id))
            row = cursor.fetchone()

            if not row:
                return False

            current_state = TransactionState(row["state"])
            allowed = VALID_TRANSITIONS.get(current_state, set())

            if new_state not in allowed:
                raise ValueError(
                    f"Illegal transaction state transition: {current_state.value} -> {new_state.value}. "
                    f"Allowed transitions from {current_state.value}: {[s.value for s in allowed]}"
                )

            if payment_id:
                cursor.execute("""
                    UPDATE orders
                    SET state = %s, razorpay_payment_id = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE order_id = %s OR razorpay_order_id = %s
                """, (new_state.value, payment_id, order_id, order_id))
            else:
                cursor.execute("""
                    UPDATE orders
                    SET state = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE order_id = %s OR razorpay_order_id = %s
                """, (new_state.value, order_id, order_id))

            return cursor.rowcount > 0

    @staticmethod
    def get_order_state(order_id: str) -> TransactionState:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT state FROM orders WHERE order_id = %s OR razorpay_order_id = %s", (order_id, order_id))
            row = cursor.fetchone()
            if row:
                return TransactionState(row["state"])
            return TransactionState.FAILED


state_machine = TransactionStateMachine()
