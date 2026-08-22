"""
================================================================================
FILE: app/knowledge/inventory.py
MODULE: Module 1 - Dynamic Inventory Repository
--------------------------------------------------------------------------------
WHAT THIS FILE DOES:
Dynamic repository managing buyer operational profiles and real-time inventory.
Loads seed data from structured JSON fixtures and maintains dynamic state.

CAPABILITIES:
  1. Loads profile contexts from app/data/seeds/buyer_inventories.json.
  2. Dynamically calculates Days of Inventory Remaining (DIR) & Critical Triggers.
  3. Supports runtime state mutation (deducting stock upon successful order,
     updating daily burn rates).
  4. Querying items by critical status and category.
================================================================================
"""
import json
from pathlib import Path
from typing import Dict, Optional, List
from app.models.enums import BusinessProfileType
from app.models.inventory import BuyerContext, InventoryItem


class InventoryRepository:
    """
    Dynamic Repository managing Buyer Inventory state and DIR calculations.
    """
    def __init__(self, seeds_path: Optional[Path] = None):
        if seeds_path is None:
            seeds_path = Path(__file__).parent.parent / "data" / "seeds" / "buyer_inventories.json"
        self.seeds_path = seeds_path
        self._profiles: Dict[BusinessProfileType, BuyerContext] = {}
        self.reload()

    def reload(self) -> None:
        """Loads or reloads inventory state from JSON seeds."""
        if not self.seeds_path.exists():
            raise FileNotFoundError(f"Inventory seed file not found at {self.seeds_path}")
            
        with open(self.seeds_path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
            
        self._profiles = {}
        for key, context_dict in raw_data.items():
            profile_type = BusinessProfileType(key)
            self._profiles[profile_type] = BuyerContext.model_validate(context_dict)

    def get_context(self, profile_type: BusinessProfileType) -> BuyerContext:
        """Retrieves buyer context for specified business vertical."""
        return self._profiles.get(profile_type, self._profiles[BusinessProfileType.CLOUD_KITCHEN])

    def get_critical_items(self, profile_type: BusinessProfileType) -> List[InventoryItem]:
        """Returns all inventory items currently at or below critical DIR threshold."""
        context = self.get_context(profile_type)
        return [item for item in context.inventory if item.is_critical]

    def deduct_stock(self, profile_type: BusinessProfileType, sku: str, quantity: float) -> bool:
        """
        Deducts stock on hand (simulating consumption).
        """
        context = self.get_context(profile_type)
        for item in context.inventory:
            if item.sku == sku:
                item.current_stock = max(0.0, round(item.current_stock - quantity, 2))
                return True
        return False

    def replenish_stock(self, profile_type: BusinessProfileType, sku: str, quantity: float) -> bool:
        """
        Adds stock to inventory upon verified Razorpay settlement.
        """
        context = self.get_context(profile_type)
        for item in context.inventory:
            if item.sku == sku:
                item.current_stock = round(item.current_stock + quantity, 2)
                return True
        return False


# Singleton repository instance
inventory_repo = InventoryRepository()


def get_buyer_context(profile_type: BusinessProfileType) -> BuyerContext:
    """Convenience accessor function."""
    return inventory_repo.get_context(profile_type)
