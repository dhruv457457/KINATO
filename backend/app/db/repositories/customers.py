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


def resolve_customer_id(merchant_id: str, identifier: str) -> Optional[str]:
    """Maps whatever a storefront called the customer onto our real
    customer_id.

    The SDK's identify() takes an `externalId` chosen by the merchant - very
    often an email. That same string then arrives on checkout.started, and
    it was previously written straight into checkouts.customer_id. The
    result: consent is recorded against the real id (cust_...), the checkout
    points at "someone@example.com", the consent lookup finds nothing, and
    recovery is silently blocked for a customer who HAD granted consent.
    That failure is invisible - no error anywhere, just a recovery that
    never happens.

    Accepts a real customer_id, an external_id, or an email/phone, and
    returns the real customer_id (or None if this merchant has no such
    customer).
    """
    if not identifier:
        return None
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT customer_id FROM customers
            WHERE merchant_id = %s
              AND (customer_id = %s OR external_id = %s OR email = %s OR phone = %s)
            LIMIT 1
            """,
            (merchant_id, identifier, identifier, identifier, identifier),
        )
        row = cursor.fetchone()
    return dict(row)["customer_id"] if row else None


def get_customer(customer_id: str) -> Optional[Dict[str, Any]]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM customers WHERE customer_id = %s", (customer_id,))
        row = cursor.fetchone()
    return dict(row) if row else None


def list_for_merchant(merchant_id: str, limit: int = 200) -> list:
    """Real customers for the dashboard's Customers page, with each one's
    current voice-channel consent status - a correlated subquery picking
    the latest consents row (portable across Postgres/SQLite, unlike
    DISTINCT ON), matching the same "latest row wins" rule
    consents.check_consent() itself uses."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                cu.*,
                (SELECT status FROM consents
                 WHERE customer_id = cu.customer_id AND channel = 'voice'
                 ORDER BY created_at DESC LIMIT 1) AS voice_consent_status
            FROM customers cu
            WHERE cu.merchant_id = %s
            ORDER BY cu.created_at DESC
            LIMIT %s
            """,
            (merchant_id, limit),
        )
        return cursor.fetchall()
