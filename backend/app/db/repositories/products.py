"""Catalog repository - includes cogs_paise, which the old hardcoded
`cart_details = {"amount": 3499.0, "cogs": 1500.0}` had no real backing for."""
from typing import Optional, Dict, Any, List
from app.db.database import get_db


def upsert_product(
    merchant_id: str,
    product_id: str,
    name: str,
    price_paise: int,
    cogs_paise: Optional[int] = None,
    description: str = "",
    currency: str = "INR",
    inventory_count: int = 0,
    image_url: str = "",
) -> Dict[str, Any]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO products (merchant_id, product_id, name, description, price_paise,
                                   cogs_paise, currency, inventory_count, image_url)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (merchant_id, product_id) DO UPDATE SET
                name = EXCLUDED.name, description = EXCLUDED.description,
                price_paise = EXCLUDED.price_paise, cogs_paise = EXCLUDED.cogs_paise,
                currency = EXCLUDED.currency, inventory_count = EXCLUDED.inventory_count,
                image_url = EXCLUDED.image_url
            """,
            (merchant_id, product_id, name, description, price_paise,
             cogs_paise, currency, inventory_count, image_url),
        )
        cursor.execute(
            "SELECT * FROM products WHERE merchant_id = %s AND product_id = %s",
            (merchant_id, product_id),
        )
        return dict(cursor.fetchone())


def get_product(merchant_id: str, product_id: str) -> Optional[Dict[str, Any]]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM products WHERE merchant_id = %s AND product_id = %s AND active = TRUE",
            (merchant_id, product_id),
        )
        row = cursor.fetchone()
    return dict(row) if row else None


def list_products(merchant_id: str, ai_buyer_visible_only: bool = False) -> List[Dict[str, Any]]:
    query = "SELECT * FROM products WHERE merchant_id = %s AND active = TRUE"
    if ai_buyer_visible_only:
        query += " AND visible_to_ai_buyers = TRUE"
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(query, (merchant_id,))
        return cursor.fetchall()
