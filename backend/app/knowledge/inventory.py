"""
================================================================================
FILE: app/knowledge/inventory.py
MODULE: Module 1 - SQLite-Backed Inventory Repository
--------------------------------------------------------------------------------
WHAT THIS FILE DOES:
Dynamic SQLite-backed repository managing buyer operational profiles, live stock,
and Days of Inventory Remaining (DIR) calculations.

CAPABILITIES:
  1. Queries buyer inventory directly from SQLite database.
  2. Dynamically calculates Days of Inventory Remaining (DIR) and Restock Urgency.
  3. Executes atomic SQL transactions for stock deductions and replenishment.
================================================================================
"""
from typing import List, Optional
from app.db.database import get_db
from app.db.init_db import init_db
from app.models.enums import BusinessProfileType
from app.models.inventory import BuyerContext, InventoryItem


class InventoryRepository:
    """
    SQLite-backed Repository managing Buyer Inventory state and DIR calculations.
    """
    def __init__(self):
        # Ensure database tables and initial seeds exist
        init_db()

    def get_context(self, profile_type: BusinessProfileType) -> BuyerContext:
        """Retrieves buyer context and live inventory directly from SQLite."""
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT business_id, business_name, profile_type, daily_budget_limit,
                       weekly_budget_limit, weekly_spent_so_far, sku, name, category,
                       current_stock, unit, daily_burn_rate, reorder_threshold_days,
                       target_restock_days
                FROM buyer_inventory
                WHERE profile_type = ?
            """, (profile_type.value,))
            rows = cursor.fetchall()

            if not rows:
                # Fallback to cloud kitchen if empty
                cursor.execute("""
                    SELECT business_id, business_name, profile_type, daily_budget_limit,
                           weekly_budget_limit, weekly_spent_so_far, sku, name, category,
                           current_stock, unit, daily_burn_rate, reorder_threshold_days,
                           target_restock_days
                    FROM buyer_inventory
                    WHERE profile_type = 'CLOUD_KITCHEN'
                """)
                rows = cursor.fetchall()

            first = rows[0]
            items = []
            for r in rows:
                items.append(InventoryItem(
                    sku=r["sku"],
                    name=r["name"],
                    category=r["category"],
                    current_stock=r["current_stock"],
                    unit=r["unit"],
                    daily_burn_rate=r["daily_burn_rate"],
                    reorder_threshold_days=r["reorder_threshold_days"],
                    target_restock_days=r["target_restock_days"]
                ))

            # Preferred suppliers lookup
            preferred_ids = ["supp_dairy_direct", "supp_metro_foods"] if profile_type == BusinessProfileType.CLOUD_KITCHEN else ["supp_beverage_hub"]

            return BuyerContext(
                business_id=first["business_id"],
                business_name=first["business_name"],
                profile_type=BusinessProfileType(first["profile_type"]),
                daily_budget_limit=first["daily_budget_limit"],
                weekly_budget_limit=first["weekly_budget_limit"],
                weekly_spent_so_far=first["weekly_spent_so_far"],
                preferred_supplier_ids=preferred_ids,
                inventory=items
            )

    def get_critical_items(self, profile_type: BusinessProfileType) -> List[InventoryItem]:
        """Returns all inventory items currently at or below critical DIR threshold."""
        context = self.get_context(profile_type)
        return [item for item in context.inventory if item.is_critical]

    def deduct_stock(self, profile_type: BusinessProfileType, sku: str, quantity: float) -> bool:
        """
        Deducts stock on hand in SQLite database (simulating usage/consumption).
        """
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE buyer_inventory
                SET current_stock = MAX(0.0, ROUND(current_stock - ?, 2))
                WHERE profile_type = ? AND sku = ?
            """, (quantity, profile_type.value, sku))
            return cursor.rowcount > 0

    def replenish_stock(self, profile_type: BusinessProfileType, sku: str, quantity: float) -> bool:
        """
        Adds stock to SQLite inventory upon verified Razorpay settlement.
        """
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE buyer_inventory
                SET current_stock = ROUND(current_stock + ?, 2)
                WHERE profile_type = ? AND sku = ?
            """, (quantity, profile_type.value, sku))
            return cursor.rowcount > 0


# Singleton repository instance
inventory_repo = InventoryRepository()


def get_buyer_context(profile_type: BusinessProfileType) -> BuyerContext:
    """Convenience accessor function."""
    return inventory_repo.get_context(profile_type)
