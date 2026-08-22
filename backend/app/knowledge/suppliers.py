"""
================================================================================
FILE: app/knowledge/suppliers.py
MODULE: Module 1 - Multi-Supplier Warehouse Network
--------------------------------------------------------------------------------
WHAT THIS FILE DOES:
Represents the Abstracted Wholesale Merchant Network in Bangalore.
Stores multiple competing supplier profiles per business vertical.

For each supplier, it tracks:
  1. Real-time Warehouse Inventory & Available Stock.
  2. Wholesale Cost Price (CP) & Standard List Price (SP).
  3. Batch Age (days) & Total Shelf Life (days) for FIFO revenue optimization.
  4. Geolocation distance (km) and Delivery SLA (hours) to Indiranagar / Koramangala.
  5. Trust Score (0.0 to 1.0) and Razorpay Verified merchant badge.

FIFO AGING CANDIDATE DESIGN:
  - DairyDirect Wholesalers: Has Chipotle Burger Sauce (Batch age: 18/25 days = 72% aged).
    This allows the supplier agent to offer a dynamic bundle discount that clears aging
    stock while strictly respecting the Floor Price (CP * 1.15).

KEY FUNCTIONS:
  - get_suppliers_for_profile(profile_type): Returns list of competing suppliers for vertical.
================================================================================
"""
from typing import List, Dict
from app.models.schemas import BusinessProfileType, SupplierProfile, SupplierProduct


SUPPLIERS_DATABASE: Dict[BusinessProfileType, List[SupplierProfile]] = {
    # --------------------------------------------------------------------------
    # 1. Cloud Kitchen Wholesalers (Bangalore)
    # --------------------------------------------------------------------------
    BusinessProfileType.CLOUD_KITCHEN: [
        SupplierProfile(
            supplier_id="supp_dairy_direct",
            name="DairyDirect Wholesalers",
            trust_score=0.98,
            distance_km=3.2,
            delivery_sla_hours=1.5,
            is_razorpay_verified=True,
            catalog=[
                SupplierProduct(
                    sku="SKU_CHEESE_MOZZ_1KG",
                    name="Mozzarella Cheese Block (1kg)",
                    category="Dairy & Perishables",
                    unit="kg",
                    cost_price=280.0,
                    list_price=350.0,
                    available_stock=50.0,
                    batch_age_days=3,
                    shelf_life_days=30
                ),
                SupplierProduct(
                    sku="SKU_BURGER_BUNS_PACK",
                    name="Brioche Burger Buns (Pack of 12)",
                    category="Bakery",
                    unit="packs",
                    cost_price=120.0,
                    list_price=160.0,
                    available_stock=40.0,
                    batch_age_days=1,
                    shelf_life_days=7
                ),
                # Aging Batch Candidate for Dynamic Bundle Upsell
                SupplierProduct(
                    sku="SKU_CHIPOTLE_SAUCE_1KG",
                    name="Gourmet Chipotle Burger Sauce (1kg)",
                    category="Condiments",
                    unit="bottles",
                    cost_price=140.0,
                    list_price=260.0,
                    available_stock=25.0,
                    batch_age_days=18,    # 18/25 days = 72% aged -> High FIFO Discount candidate!
                    shelf_life_days=25
                )
            ]
        ),
        SupplierProfile(
            supplier_id="supp_metro_foods",
            name="Metro Foodservice Hub",
            trust_score=0.94,
            distance_km=6.8,
            delivery_sla_hours=3.0,
            is_razorpay_verified=True,
            catalog=[
                SupplierProduct(
                    sku="SKU_CHEESE_MOZZ_1KG",
                    name="Mozzarella Cheese Block (1kg)",
                    category="Dairy & Perishables",
                    unit="kg",
                    cost_price=290.0,
                    list_price=340.0,     # Slightly cheaper list price
                    available_stock=100.0,
                    batch_age_days=5,
                    shelf_life_days=30
                ),
                SupplierProduct(
                    sku="SKU_BURGER_BUNS_PACK",
                    name="Brioche Burger Buns (Pack of 12)",
                    category="Bakery",
                    unit="packs",
                    cost_price=125.0,
                    list_price=165.0,
                    available_stock=80.0,
                    batch_age_days=2,
                    shelf_life_days=7
                ),
                SupplierProduct(
                    sku="SKU_CHIPOTLE_SAUCE_1KG",
                    name="Gourmet Chipotle Burger Sauce (1kg)",
                    category="Condiments",
                    unit="bottles",
                    cost_price=150.0,
                    list_price=270.0,
                    available_stock=30.0,
                    batch_age_days=6,     # Fresh batch, no heavy discount
                    shelf_life_days=25
                )
            ]
        ),
        SupplierProfile(
            supplier_id="supp_fresh_farms",
            name="FarmLink Supply Direct",
            trust_score=0.88,
            distance_km=14.5,
            delivery_sla_hours=5.0,
            is_razorpay_verified=False,
            catalog=[
                SupplierProduct(
                    sku="SKU_CHEESE_MOZZ_1KG",
                    name="Mozzarella Cheese Block (1kg)",
                    category="Dairy & Perishables",
                    unit="kg",
                    cost_price=310.0,
                    list_price=380.0,
                    available_stock=20.0,
                    batch_age_days=10,
                    shelf_life_days=30
                )
            ]
        )
    ],

    # --------------------------------------------------------------------------
    # 2. Tech Startup Pantry Wholesalers (Bangalore)
    # --------------------------------------------------------------------------
    BusinessProfileType.TECH_PANTRY: [
        SupplierProfile(
            supplier_id="supp_beverage_hub",
            name="BeanCraft Roasters & Pantry Hub",
            trust_score=0.99,
            distance_km=2.5,
            delivery_sla_hours=2.0,
            is_razorpay_verified=True,
            catalog=[
                SupplierProduct(
                    sku="SKU_COFFEE_BEANS_1KG",
                    name="Arabica Dark Roast Coffee Beans (1kg)",
                    category="Beverages & Pantry",
                    unit="kg",
                    cost_price=550.0,
                    list_price=750.0,
                    available_stock=30.0,
                    batch_age_days=5,
                    shelf_life_days=90
                ),
                SupplierProduct(
                    sku="SKU_OAT_MILK_1L",
                    name="Barista Oat Milk (1L)",
                    category="Beverages & Pantry",
                    unit="litres",
                    cost_price=180.0,
                    list_price=250.0,
                    available_stock=50.0,
                    batch_age_days=12,
                    shelf_life_days=60
                ),
                # Aging Batch Healthy Snack Bars
                SupplierProduct(
                    sku="SKU_PROTEIN_BARS_BOX",
                    name="Almond Crunch Protein Bars (Box of 12)",
                    category="Pantry Snacks",
                    unit="box",
                    cost_price=220.0,
                    list_price=420.0,
                    available_stock=18.0,
                    batch_age_days=35,    # 35/45 days = 77% aged -> Aging Discount candidate!
                    shelf_life_days=45
                )
            ]
        ),
        SupplierProfile(
            supplier_id="supp_office_direct",
            name="PrimeSupply Corporate Hub",
            trust_score=0.93,
            distance_km=7.0,
            delivery_sla_hours=4.0,
            is_razorpay_verified=True,
            catalog=[
                SupplierProduct(
                    sku="SKU_COFFEE_BEANS_1KG",
                    name="Arabica Dark Roast Coffee Beans (1kg)",
                    category="Beverages & Pantry",
                    unit="kg",
                    cost_price=580.0,
                    list_price=780.0,
                    available_stock=60.0,
                    batch_age_days=10,
                    shelf_life_days=90
                ),
                SupplierProduct(
                    sku="SKU_OAT_MILK_1L",
                    name="Barista Oat Milk (1L)",
                    category="Beverages & Pantry",
                    unit="litres",
                    cost_price=190.0,
                    list_price=260.0,
                    available_stock=40.0,
                    batch_age_days=15,
                    shelf_life_days=60
                )
            ]
        )
    ],

    # --------------------------------------------------------------------------
    # 3. Retail Packaging Wholesalers (Bangalore)
    # --------------------------------------------------------------------------
    BusinessProfileType.RETAIL_STORE: [
        SupplierProfile(
            supplier_id="supp_pack_pro",
            name="PackPro Industrial Solutions",
            trust_score=0.97,
            distance_km=4.1,
            delivery_sla_hours=2.5,
            is_razorpay_verified=True,
            catalog=[
                SupplierProduct(
                    sku="SKU_CORRUGATED_BOX_M",
                    name="Medium Shipping Boxes (Pack of 50)",
                    category="Packaging Supplies",
                    unit="pcs",
                    cost_price=600.0,
                    list_price=850.0,
                    available_stock=120.0,
                    batch_age_days=15,
                    shelf_life_days=365
                ),
                SupplierProduct(
                    sku="SKU_PACKING_TAPE_ROLL",
                    name="Heavy Duty Brown Tape (Pack of 6)",
                    category="Packaging Supplies",
                    unit="packs",
                    cost_price=180.0,
                    list_price=280.0,
                    available_stock=90.0,
                    batch_age_days=20,
                    shelf_life_days=365
                )
            ]
        )
    ]
}


def get_suppliers_for_profile(profile_type: BusinessProfileType) -> List[SupplierProfile]:
    """Returns the list of competing registered wholesale merchants for a vertical."""
    return SUPPLIERS_DATABASE.get(profile_type, [])
