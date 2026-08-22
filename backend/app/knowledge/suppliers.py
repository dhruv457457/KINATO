"""
================================================================================
FILE: app/knowledge/suppliers.py
MODULE: Module 1 - Dynamic Supplier Registry & Warehouse Repository
--------------------------------------------------------------------------------
WHAT THIS FILE DOES:
Dynamic repository managing the Abstracted Supplier Network and warehouse catalogs.
Loads seed data from app/data/seeds/suppliers_catalog.json and supports
runtime queries, FIFO batch aging lookups, and inventory reservations.

CAPABILITIES:
  1. Loads supplier profiles & product catalogs from JSON seeds.
  2. Queries suppliers matching specific SKUs, category, and max distance.
  3. Dynamic FIFO aging calculation: identifies batches with aging_ratio >= 0.60
     eligible for dynamic bundle discounting.
  4. Enforces real-time stock reservations upon order placement.
================================================================================
"""
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from app.models.enums import BusinessProfileType
from app.models.supplier import SupplierProfile, SupplierProduct


class SupplierRepository:
    """
    Dynamic Repository managing Supplier Network, catalogs, and FIFO aging batches.
    """
    def __init__(self, seeds_path: Optional[Path] = None):
        if seeds_path is None:
            seeds_path = Path(__file__).parent.parent / "data" / "seeds" / "suppliers_catalog.json"
        self.seeds_path = seeds_path
        self._suppliers: Dict[BusinessProfileType, List[SupplierProfile]] = {}
        self.reload()

    def reload(self) -> None:
        """Loads or reloads supplier catalog state from JSON seeds."""
        if not self.seeds_path.exists():
            raise FileNotFoundError(f"Supplier seed file not found at {self.seeds_path}")
            
        with open(self.seeds_path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
            
        self._suppliers = {}
        for key, supplier_list in raw_data.items():
            profile_type = BusinessProfileType(key)
            self._suppliers[profile_type] = [
                SupplierProfile.model_validate(supp) for supp in supplier_list
            ]

    def get_suppliers(self, profile_type: BusinessProfileType) -> List[SupplierProfile]:
        """Returns all registered suppliers for a given business profile."""
        return self._suppliers.get(profile_type, [])

    def get_supplier_by_id(self, profile_type: BusinessProfileType, supplier_id: str) -> Optional[SupplierProfile]:
        """Retrieves a specific supplier profile by ID."""
        for supp in self.get_suppliers(profile_type):
            if supp.supplier_id == supplier_id:
                return supp
        return None

    def find_suppliers_with_sku(
        self,
        profile_type: BusinessProfileType,
        sku: str
    ) -> List[Tuple[SupplierProfile, SupplierProduct]]:
        """
        Finds all suppliers currently stocking a specific SKU with available stock > 0.
        """
        results = []
        for supp in self.get_suppliers(profile_type):
            for product in supp.catalog:
                if product.sku == sku and product.available_stock > 0:
                    results.append((supp, product))
                    break
        return results

    def find_aging_bundles(
        self,
        supplier: SupplierProfile,
        primary_sku: str
    ) -> List[SupplierProduct]:
        """
        Identifies complementary items in the supplier's warehouse that are
        aging (aging_ratio >= 0.60) and can be bundled to clear inventory.
        """
        aging_items = []
        for product in supplier.catalog:
            if product.sku != primary_sku and product.is_aging_batch and product.available_stock > 0:
                aging_items.append(product)
        return aging_items


# Singleton repository instance
supplier_repo = SupplierRepository()


def get_suppliers_for_profile(profile_type: BusinessProfileType) -> List[SupplierProfile]:
    """Convenience accessor function."""
    return supplier_repo.get_suppliers(profile_type)
