"""Customer repository. Replaces identity_service.py's `# Mock DB Upsert`
comment with a real one."""
from typing import Optional, Dict, Any
from app.db.database import get_db
from app.core.ids import new_id


def upsert_by_external_id(
    merchant_id: str, external_id: str, name: str = "", email: str = "", phone: str = ""
) -> Dict[str, Any]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM customers WHERE merchant_id = %s AND external_id = %s",
            (merchant_id, external_id),
        )
        existing = cursor.fetchone()
        if existing:
            cursor.execute(
                """
                UPDATE customers SET
                    name = COALESCE(NULLIF(%s, ''), name),
                    email = COALESCE(NULLIF(%s, ''), email),
                    phone = COALESCE(NULLIF(%s, ''), phone)
                WHERE customer_id = %s
                """,
                (name, email, phone, existing["customer_id"]),
            )
            cursor.execute("SELECT * FROM customers WHERE customer_id = %s", (existing["customer_id"],))
            return dict(cursor.fetchone())

        customer_id = new_id("cust")
        cursor.execute(
            """
            INSERT INTO customers (customer_id, merchant_id, external_id, name, email, phone)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (customer_id, merchant_id, external_id, name, email, phone),
        )
        cursor.execute("SELECT * FROM customers WHERE customer_id = %s", (customer_id,))
        return dict(cursor.fetchone())


def upsert_by_contact(merchant_id: str, email: str = "", phone: str = "", name: str = "") -> Dict[str, Any]:
    """Used when a Razorpay webhook (payment.failed etc.) supplies contact
    info directly, with no prior SDK identify() call - the zero-code path."""
    with get_db() as conn:
        cursor = conn.cursor()
        if email:
            cursor.execute(
                "SELECT * FROM customers WHERE merchant_id = %s AND email = %s", (merchant_id, email)
            )
        elif phone:
            cursor.execute(
                "SELECT * FROM customers WHERE merchant_id = %s AND phone = %s", (merchant_id, phone)
            )
        else:
            cursor.execute("SELECT 1 WHERE FALSE")
        existing = cursor.fetchone()
        if existing:
            return dict(existing)

        customer_id = new_id("cust")
        cursor.execute(
            """
            INSERT INTO customers (customer_id, merchant_id, name, email, phone)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (customer_id, merchant_id, name, email, phone),
        )
        cursor.execute("SELECT * FROM customers WHERE customer_id = %s", (customer_id,))
        return dict(cursor.fetchone())


def get_customer(customer_id: str) -> Optional[Dict[str, Any]]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM customers WHERE customer_id = %s", (customer_id,))
        row = cursor.fetchone()
    return dict(row) if row else None
