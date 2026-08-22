"""
================================================================================
FILE: app/knowledge/suppliers.py
MODULE: Module 1 - SQLite-Backed Supplier Registry
--------------------------------------------------------------------------------
WHAT THIS FILE DOES:
Dynamic SQLite-backed repository managing the Abstracted Supplier Network,
live warehouse catalogs, and FIFO batch aging lookups.

CAPABILITIES:
  1. Queries supplier catalogs and product availability directly from SQLite.
  2. Queries suppliers matching specific SKUs, categories, and max distance.
  3. Dynamic FIFO aging calculation: identifies batches with aging_ratio >= 0.60
     eligible for dynamic bundle discounting.
================================================================================
"""
from typing import List, Optional, Tuple
from app.db.database import get_db
from app.db.init_db import init_db
from app.models.enums import BusinessProfileType
from app.models.supplier import SupplierProfile, SupplierProduct


class SupplierRepository:
    """
    SQLite-backed Repository managing Supplier Network and warehouse catalogs.
    """
    def __init__(self):
        init_db()

    def get_suppliers(self, profile_type: BusinessProfileType) -> List[SupplierProfile]:
        """Returns all registered suppliers for a given business profile from SQLite."""
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT supplier_id, supplier_name, profile_type, trust_score, distance_km,
                       delivery_sla_hours, is_razorpay_verified, sku, product_name,
                       category, unit, cost_price, list_price, minimum_margin_pct,
                       available_stock, batch_age_days, shelf_life_days
                FROM supplier_catalog
                WHERE profile_type = ?
            """, (profile_type.value,))
            rows = cursor.fetchall()

            suppliers_dict = {}
            for r in rows:
                sid = r["supplier_id"]
                if sid not in suppliers_dict:
                    suppliers_dict[sid] = {
                        "supplier_id": sid,
                        "name": r["supplier_name"],
                        "trust_score": r["trust_score"],
                        "distance_km": r["distance_km"],
                        "delivery_sla_hours": r["delivery_sla_hours"],
                        "is_razorpay_verified": bool(r["is_razorpay_verified"]),
                        "catalog": []
                    }
                suppliers_dict[sid]["catalog"].append(SupplierProduct(
                    sku=r["sku"],
                    name=r["product_name"],
                    category=r["category"],
                    unit=r["unit"],
                    cost_price=r["cost_price"],
                    list_price=r["list_price"],
                    minimum_margin_pct=r["minimum_margin_pct"],
                    available_stock=r["available_stock"],
                    batch_age_days=r["batch_age_days"],
                    shelf_life_days=r["shelf_life_days"]
                ))

            return [SupplierProfile.model_validate(supp) for supp in suppliers_dict.values()]

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
