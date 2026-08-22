"""
================================================================================
FILE: app/models/inventory.py
MODULE: Module 1 - Data Contracts
--------------------------------------------------------------------------------
WHAT THIS FILE DOES:
Defines schemas for buyer inventory monitoring, consumption rates, and DIR math.
================================================================================
"""
from typing import List
from pydantic import BaseModel, Field
from app.models.enums import BusinessProfileType


class InventoryItem(BaseModel):
    """
    Represents an inventory item tracked by the consuming business.
    Calculates Days of Inventory Remaining (DIR) and Restock Urgency.
    """
    sku: str = Field(description="Unique SKU identifier")
    name: str = Field(description="Product name")
    category: str = Field(description="Inventory category")
    current_stock: float = Field(description="Current units in storage")
    unit: str = Field(description="Measurement unit (kg, litres, packs, pcs)")
    daily_burn_rate: float = Field(description="Average units consumed per day")
    reorder_threshold_days: float = Field(default=1.5, description="DIR threshold to trigger RFQ")
    target_restock_days: float = Field(default=4.0, description="Target buffer days to replenish")

    @property
    def days_remaining(self) -> float:
        """DIR = Current Stock / Daily Burn Rate"""
        if self.daily_burn_rate <= 0:
            return 999.0
        return round(self.current_stock / self.daily_burn_rate, 2)

    @property
    def is_critical(self) -> bool:
        """Returns True if stock will deplete in <= reorder_threshold_days."""
        return self.days_remaining <= self.reorder_threshold_days

    @property
    def reorder_quantity(self) -> float:
        """Computes quantity needed to reach target_restock_days buffer."""
        target_stock = self.target_restock_days * self.daily_burn_rate
        needed = target_stock - self.current_stock
        return max(0.0, round(needed, 2))


class BuyerContext(BaseModel):
    """Represents the operational context, budgets, and inventory of a buyer."""
    business_id: str
    business_name: str
    profile_type: BusinessProfileType
    daily_budget_limit: float
    weekly_budget_limit: float
    weekly_spent_so_far: float
    preferred_supplier_ids: List[str] = Field(default_factory=list)
    inventory: List[InventoryItem]
