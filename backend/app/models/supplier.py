"""
================================================================================
FILE: app/models/supplier.py
MODULE: Module 1 - Data Contracts
--------------------------------------------------------------------------------
WHAT THIS FILE DOES:
Defines schemas for wholesale supplier catalog items, FIFO batch aging, and profiles.
================================================================================
"""
from typing import List
from pydantic import BaseModel


class SupplierProduct(BaseModel):
    """
    Represents a wholesale product in a supplier's warehouse.
    Tracks batch age, shelf-life, cost prices, and margin floors.
    """
    sku: str
    name: str
    category: str
    unit: str
    cost_price: float         # Wholesale procurement cost (CP)
    list_price: float         # Standard selling price (SP)
    minimum_margin_pct: float = 0.15  # Minimum 15% margin floor
    available_stock: float
    batch_age_days: int       # Days since harvest / manufacture
    shelf_life_days: int      # Total shelf life in days

    @property
    def floor_price(self) -> float:
        """Deterministic mathematical price floor: Cost Price * (1 + Min Margin)"""
        return round(self.cost_price * (1.0 + self.minimum_margin_pct), 2)

    @property
    def aging_ratio(self) -> float:
        """Ratio of batch age to total shelf life (0.0 to 1.0)"""
        if self.shelf_life_days <= 0:
            return 0.0
        return min(1.0, round(self.batch_age_days / self.shelf_life_days, 2))

    @property
    def is_aging_batch(self) -> bool:
        """True if batch has consumed >= 60% of shelf life (FIFO discount candidate)."""
        return self.aging_ratio >= 0.60


class SupplierProfile(BaseModel):
    """Represents a registered wholesale merchant in the Kinato network."""
    supplier_id: str
    name: str
    trust_score: float        # Historical fulfillment rate (0.0 to 1.0)
    distance_km: float        # Distance to buyer in kilometers
    delivery_sla_hours: float # Delivery SLA in hours
    is_razorpay_verified: bool = True
    catalog: List[SupplierProduct]
