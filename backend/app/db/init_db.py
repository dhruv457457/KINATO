"""
================================================================================
FILE: app/db/init_db.py
MODULE: Module 1 - SQLite Database Initialization & Seeder
--------------------------------------------------------------------------------
WHAT THIS FILE DOES:
Creates the SQLite tables with strict constraints, indexes, and foreign keys:
  1. buyer_inventory: Live on-hand stock and daily burn rates per business.
  2. supplier_catalog: Live supplier warehouse inventory, cost prices, and batch ages.
  3. proposals: Stored A2A negotiated proposals and their HMAC signatures.
  4. orders: Transaction State Machine (OFFER_READY, APPROVED, SUCCESS, UNCERTAIN, FAILED).
  5. idempotency_journal: Deduplication table for Razorpay Orders API calls.
  6. proof_receipts: Cryptographic proof of intent and settlement audit records.

Auto-seeds the database from JSON fixtures if the tables are empty on startup.
================================================================================
"""
import json
from pathlib import Path
from app.db.database import get_db, DB_PATH


def init_db(force_reseed: bool = False) -> None:
    """Initializes the SQLite schema and seeds initial data from JSON fixtures."""
    seeds_dir = Path(__file__).parent.parent / "data" / "seeds"
    buyer_seeds_path = seeds_dir / "buyer_inventories.json"
    supplier_seeds_path = seeds_dir / "suppliers_catalog.json"

    with get_db() as conn:
        cursor = conn.cursor()

        # ----------------------------------------------------------------------
        # 1. DDL Table Definitions
        # ----------------------------------------------------------------------
        cursor.executescript("""
        CREATE TABLE IF NOT EXISTS buyer_inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id TEXT NOT NULL,
            business_name TEXT NOT NULL,
            profile_type TEXT NOT NULL,
            sku TEXT NOT NULL,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            current_stock REAL NOT NULL,
            unit TEXT NOT NULL,
            daily_burn_rate REAL NOT NULL,
            reorder_threshold_days REAL NOT NULL DEFAULT 1.5,
            target_restock_days REAL NOT NULL DEFAULT 4.0,
            daily_budget_limit REAL NOT NULL,
            weekly_budget_limit REAL NOT NULL,
            weekly_spent_so_far REAL NOT NULL DEFAULT 0.0,
            UNIQUE(business_id, sku)
        );

        CREATE TABLE IF NOT EXISTS supplier_catalog (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            supplier_id TEXT NOT NULL,
            supplier_name TEXT NOT NULL,
            profile_type TEXT NOT NULL,
            trust_score REAL NOT NULL,
            distance_km REAL NOT NULL,
            delivery_sla_hours REAL NOT NULL,
            is_razorpay_verified INTEGER NOT NULL DEFAULT 1,
            sku TEXT NOT NULL,
            product_name TEXT NOT NULL,
            category TEXT NOT NULL,
            unit TEXT NOT NULL,
            cost_price REAL NOT NULL,
            list_price REAL NOT NULL,
            minimum_margin_pct REAL NOT NULL DEFAULT 0.15,
            available_stock REAL NOT NULL,
            batch_age_days INTEGER NOT NULL,
            shelf_life_days INTEGER NOT NULL,
            UNIQUE(supplier_id, sku)
        );

        CREATE TABLE IF NOT EXISTS proposals (
            proposal_id TEXT PRIMARY KEY,
            rfq_id TEXT NOT NULL,
            profile_type TEXT NOT NULL,
            winning_supplier_id TEXT NOT NULL,
            winning_supplier_name TEXT NOT NULL,
            items_json TEXT NOT NULL,
            subtotal REAL NOT NULL,
            total_discount REAL NOT NULL,
            final_total REAL NOT NULL,
            proposal_hash TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'CREATED',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS orders (
            order_id TEXT PRIMARY KEY,
            proposal_id TEXT NOT NULL,
            business_id TEXT NOT NULL,
            supplier_id TEXT NOT NULL,
            amount_inr REAL NOT NULL,
            amount_paise INTEGER NOT NULL,
            currency TEXT NOT NULL DEFAULT 'INR',
            razorpay_order_id TEXT,
            razorpay_payment_id TEXT,
            state TEXT NOT NULL DEFAULT 'CREATED',
            mode TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (proposal_id) REFERENCES proposals(proposal_id)
        );

        CREATE TABLE IF NOT EXISTS idempotency_journal (
            idempotency_key TEXT PRIMARY KEY,
            razorpay_order_id TEXT NOT NULL,
            proposal_id TEXT NOT NULL,
            amount_paise INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS proof_receipts (
            receipt_id TEXT PRIMARY KEY,
            proposal_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            business_name TEXT NOT NULL,
            supplier_name TEXT NOT NULL,
            items_json TEXT NOT NULL,
            total_amount_inr REAL NOT NULL,
            payment_method TEXT NOT NULL,
            razorpay_order_id TEXT NOT NULL,
            razorpay_payment_id TEXT NOT NULL,
            policy_hash TEXT NOT NULL,
            signature_verified INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'SUCCESS',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_buyer_inv_profile ON buyer_inventory(profile_type);
        CREATE INDEX IF NOT EXISTS idx_supp_cat_profile ON supplier_catalog(profile_type);
        CREATE INDEX IF NOT EXISTS idx_orders_proposal ON orders(proposal_id);
        """)

        # ----------------------------------------------------------------------
        # 2. Seeding Buyer Inventory
        # ----------------------------------------------------------------------
        cursor.execute("SELECT COUNT(*) FROM buyer_inventory")
        buyer_count = cursor.fetchone()[0]

        if buyer_count == 0 or force_reseed:
            if force_reseed:
                cursor.execute("DELETE FROM buyer_inventory")

            if buyer_seeds_path.exists():
                with open(buyer_seeds_path, "r", encoding="utf-8") as f:
                    buyer_seeds = json.load(f)

                for profile_type_key, ctx in buyer_seeds.items():
                    for item in ctx["inventory"]:
                        cursor.execute("""
                        INSERT OR REPLACE INTO buyer_inventory (
                            business_id, business_name, profile_type, sku, name, category,
                            current_stock, unit, daily_burn_rate, reorder_threshold_days,
                            target_restock_days, daily_budget_limit, weekly_budget_limit, weekly_spent_so_far
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            ctx["business_id"], ctx["business_name"], ctx["profile_type"],
                            item["sku"], item["name"], item["category"], item["current_stock"],
                            item["unit"], item["daily_burn_rate"], item["reorder_threshold_days"],
                            item["target_restock_days"], ctx["daily_budget_limit"],
                            ctx["weekly_budget_limit"], ctx["weekly_spent_so_far"]
                        ))

        # ----------------------------------------------------------------------
        # 3. Seeding Supplier Catalog
        # ----------------------------------------------------------------------
        cursor.execute("SELECT COUNT(*) FROM supplier_catalog")
        supp_count = cursor.fetchone()[0]

        if supp_count == 0 or force_reseed:
            if force_reseed:
                cursor.execute("DELETE FROM supplier_catalog")

            if supplier_seeds_path.exists():
                with open(supplier_seeds_path, "r", encoding="utf-8") as f:
                    supp_seeds = json.load(f)

                for profile_type_key, suppliers in supp_seeds.items():
                    for supp in suppliers:
                        for prod in supp["catalog"]:
                            cursor.execute("""
                            INSERT OR REPLACE INTO supplier_catalog (
                                supplier_id, supplier_name, profile_type, trust_score, distance_km,
                                delivery_sla_hours, is_razorpay_verified, sku, product_name,
                                category, unit, cost_price, list_price, minimum_margin_pct,
                                available_stock, batch_age_days, shelf_life_days
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """, (
                                supp["supplier_id"], supp["name"], profile_type_key,
                                supp["trust_score"], supp["distance_km"], supp["delivery_sla_hours"],
                                1 if supp.get("is_razorpay_verified", True) else 0,
                                prod["sku"], prod["name"], prod["category"], prod["unit"],
                                prod["cost_price"], prod["list_price"], prod.get("minimum_margin_pct", 0.15),
                                prod["available_stock"], prod["batch_age_days"], prod["shelf_life_days"]
                            ))


if __name__ == "__main__":
    init_db()
    print("Database initialized and seeded successfully at:", DB_PATH)
