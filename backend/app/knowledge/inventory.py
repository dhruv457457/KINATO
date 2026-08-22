"""
================================================================================
FILE: app/knowledge/inventory.py
MODULE: Module 1 - Buyer Inventory Knowledge Store
--------------------------------------------------------------------------------
WHAT THIS FILE DOES:
Provides seeded buyer inventory datasets and operational profiles for:
  1. Cloud Kitchen ("BurgerCraft Kitchen", Indiranagar)
     - Stock: Mozzarella Cheese (DIR = 1.0 day -> CRITICAL), Brioche Buns (CRITICAL), Fryer Oil.
  2. Tech Startup Pantry ("DevPulse Tech Coworking", Koramangala)
     - Stock: Arabica Dark Roast Coffee (DIR = 1.0 day -> CRITICAL), Oat Milk, Paper Cups.
  3. Retail Packaging ("SpeedyMart Express", HSR Layout)
     - Stock: Medium Shipping Boxes (DIR = 0.67 days -> CRITICAL), Tape Rolls, Bubble Wrap.

KEY FORMULAS EXERCISED:
  - Days of Inventory Remaining: DIR = current_stock / daily_burn_rate
  - Reorder Trigger: is_critical = True when DIR <= 1.5 days
  - Target Reorder Quantity: Q = (target_days * daily_burn_rate) - current_stock

KEY FUNCTIONS:
  - get_buyer_context(profile_type): Returns the BuyerContext for a chosen vertical.
================================================================================
"""
from typing import Dict
from app.models.schemas import BusinessProfileType, BuyerContext, InventoryItem


BUYER_PROFILES: Dict[BusinessProfileType, BuyerContext] = {
    # --------------------------------------------------------------------------
    # Profile 1: Cloud Kitchen (BurgerCraft Kitchen, Indiranagar, Bangalore)
    # --------------------------------------------------------------------------
    BusinessProfileType.CLOUD_KITCHEN: BuyerContext(
        business_id="buyer_kitchen_01",
        business_name="BurgerCraft Kitchen (Indiranagar)",
        profile_type=BusinessProfileType.CLOUD_KITCHEN,
        daily_budget_limit=2500.0,
        weekly_budget_limit=15000.0,
        weekly_spent_so_far=4200.0,
        preferred_supplier_ids=["supp_dairy_direct", "supp_metro_foods"],
        inventory=[
            InventoryItem(
                sku="SKU_CHEESE_MOZZ_1KG",
                name="Mozzarella Cheese Block (1kg)",
                category="Dairy & Perishables",
                current_stock=2.0,       # 2kg remaining
                unit="kg",
                daily_burn_rate=2.0,     # Burns 2kg/day -> DIR = 1.0 day (CRITICAL!)
                reorder_threshold_days=1.5,
                target_restock_days=4.0  # Target: 8kg total (Needs 6kg restock)
            ),
            InventoryItem(
                sku="SKU_BURGER_BUNS_PACK",
                name="Brioche Burger Buns (Pack of 12)",
                category="Bakery",
                current_stock=4.0,       # 4 packs remaining
                unit="packs",
                daily_burn_rate=3.0,     # Burns 3 packs/day -> DIR = 1.33 days (CRITICAL!)
                reorder_threshold_days=1.5,
                target_restock_days=4.0
            ),
            InventoryItem(
                sku="SKU_FRYER_OIL_5L",
                name="Refined Canola Fryer Oil (5L)",
                category="Oils & Condiments",
                current_stock=15.0,      # 15L remaining
                unit="litres",
                daily_burn_rate=2.5,     # Burns 2.5L/day -> DIR = 6.0 days (Safe)
                reorder_threshold_days=1.5,
                target_restock_days=5.0
            )
        ]
    ),

    # --------------------------------------------------------------------------
    # Profile 2: Tech Startup Pantry (DevPulse Tech Coworking, Koramangala)
    # --------------------------------------------------------------------------
    BusinessProfileType.TECH_PANTRY: BuyerContext(
        business_id="buyer_startup_02",
        business_name="DevPulse Tech Coworking (Koramangala)",
        profile_type=BusinessProfileType.TECH_PANTRY,
        daily_budget_limit=1500.0,
        weekly_budget_limit=8000.0,
        weekly_spent_so_far=1800.0,
        preferred_supplier_ids=["supp_beverage_hub", "supp_office_direct"],
        inventory=[
            InventoryItem(
                sku="SKU_COFFEE_BEANS_1KG",
                name="Arabica Dark Roast Coffee Beans (1kg)",
                category="Beverages & Pantry",
                current_stock=0.5,       # 0.5kg left
                unit="kg",
                daily_burn_rate=0.5,     # Burns 0.5kg/day -> DIR = 1.0 day (CRITICAL!)
                reorder_threshold_days=1.5,
                target_restock_days=5.0
            ),
            InventoryItem(
                sku="SKU_OAT_MILK_1L",
                name="Barista Oat Milk (1L)",
                category="Beverages & Pantry",
                current_stock=1.0,
                unit="litres",
                daily_burn_rate=1.0,     # DIR = 1.0 day (CRITICAL!)
                reorder_threshold_days=1.5,
                target_restock_days=4.0
            ),
            InventoryItem(
                sku="SKU_PAPER_CUPS_100",
                name="Compostable Coffee Cups (Pack of 100)",
                category="Pantry Supplies",
                current_stock=200.0,
                unit="pcs",
                daily_burn_rate=30.0,    # DIR = 6.6 days (Safe)
                reorder_threshold_days=1.5,
                target_restock_days=5.0
            )
        ]
    ),

    # --------------------------------------------------------------------------
    # Profile 3: Retail & Packaging Store (SpeedyMart Express, HSR Layout)
    # --------------------------------------------------------------------------
    BusinessProfileType.RETAIL_STORE: BuyerContext(
        business_id="buyer_store_03",
        business_name="SpeedyMart Express (HSR Layout)",
        profile_type=BusinessProfileType.RETAIL_STORE,
        daily_budget_limit=2000.0,
        weekly_budget_limit=12000.0,
        weekly_spent_so_far=3500.0,
        preferred_supplier_ids=["supp_pack_pro"],
        inventory=[
            InventoryItem(
                sku="SKU_CORRUGATED_BOX_M",
                name="Medium Shipping Boxes (Pack of 50)",
                category="Packaging Supplies",
                current_stock=10.0,
                unit="pcs",
                daily_burn_rate=15.0,    # DIR = 0.67 days (CRITICAL!)
                reorder_threshold_days=1.5,
                target_restock_days=4.0
            ),
            InventoryItem(
                sku="SKU_PACKING_TAPE_ROLL",
                name="Heavy Duty Brown Tape (Pack of 6)",
                category="Packaging Supplies",
                current_stock=1.0,
                unit="packs",
                daily_burn_rate=1.0,     # DIR = 1.0 day (CRITICAL!)
                reorder_threshold_days=1.5,
                target_restock_days=4.0
            ),
            InventoryItem(
                sku="SKU_BUBBLE_WRAP_50M",
                name="Protective Bubble Wrap Roll (50m)",
                category="Packaging Supplies",
                current_stock=3.0,
                unit="rolls",
                daily_burn_rate=0.3,     # DIR = 10 days (Safe)
                reorder_threshold_days=1.5,
                target_restock_days=4.0
            )
        ]
    )
}


def get_buyer_context(profile_type: BusinessProfileType) -> BuyerContext:
    """Returns the operational BuyerContext for the specified business vertical."""
    return BUYER_PROFILES.get(profile_type, BUYER_PROFILES[BusinessProfileType.CLOUD_KITCHEN])
