"""
================================================================================
TEST: tests/test_idempotency.py
MODULE: Module 5 - Razorpay Idempotency Key Tests
--------------------------------------------------------------------------------
Tests that repeating order creation requests with the same proposal returns the
existing order_id without creating duplicate entries in the database.
================================================================================
"""
from app.payments.razorpay_client import razorpay_rails
from app.models.enums import ExecutionMode
from app.db.database import get_db
from app.db.init_db import init_db


def test_razorpay_order_idempotency():
    """
    Asserts that multiple calls with the exact same proposal_id and supplier_id
    trigger the idempotency journal and return the exact same Razorpay order_id.
    """
    init_db()
    proposal_id = "prop_idemp_test_999"
    supplier_id = "supp_dairy_direct"
    amount_inr = 1480.0

    # Ensure parent proposal exists in database for foreign key constraint
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO proposals (
                proposal_id, rfq_id, profile_type, winning_supplier_id,
                winning_supplier_name, items_json, subtotal, total_discount,
                final_total, proposal_hash, status
            ) VALUES (?, 'rfq_test_idemp', 'CLOUD_KITCHEN', ?, 'DairyDirect Wholesalers', '[]', ?, 0.0, ?, 'hash_test_123', 'OFFER_READY')
        """, (proposal_id, supplier_id, amount_inr, amount_inr))

    # First call -> Creates order
    order_1 = razorpay_rails.create_order(
        proposal_id=proposal_id,
        amount_inr=amount_inr,
        business_id="buyer_kitchen_01",
        supplier_id=supplier_id,
        mode=ExecutionMode.ONE_CLICK_APPROVAL,
        proposal_hash="hash_test_123"
    )

    # Second call -> Must return the EXACT SAME order_id via Idempotency Journal
    order_2 = razorpay_rails.create_order(
        proposal_id=proposal_id,
        amount_inr=amount_inr,
        business_id="buyer_kitchen_01",
        supplier_id=supplier_id,
        mode=ExecutionMode.ONE_CLICK_APPROVAL,
        proposal_hash="hash_test_123"
    )

    assert order_1.order_id == order_2.order_id
    assert order_1.amount_paise == order_2.amount_paise

    # Verify only ONE entry exists in the idempotency_journal table
    with get_db() as conn:
        cursor = conn.cursor()
        idempotency_key = f"kinato_{proposal_id}_{supplier_id}"
        cursor.execute("SELECT COUNT(*) FROM idempotency_journal WHERE idempotency_key = ?", (idempotency_key,))
        count = cursor.fetchone()[0]
        assert count == 1
