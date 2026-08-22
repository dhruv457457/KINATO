"""
================================================================================
FILE: app/agents/supplier_agent.py
MODULE: Module 2 - Supplier Agent Intelligence
--------------------------------------------------------------------------------
WHAT THIS FILE DOES:
Implements the autonomous Supplier Agent representing wholesale merchants.

CORE RESPONSIBILITIES:
  1. Warehouse Catalog Matching:
     Evaluates incoming A2A-RFQ and checks available inventory and price.
  2. FIFO Batch Aging & Dynamic Bundling:
     Identifies aging warehouse batches (aging_ratio >= 0.60) and bundles them
     with the primary item to clear perishable stock before expiration.
  3. Dynamic FIFO Discount Curve:
     Calculates discount percentage based on batch age ratio:
       Discount = Max_Discount * max(0, (aging_ratio - 0.60) / (1 - 0.60))
  4. Bounded Concession & Floor Price Protection:
     Responds to Buyer Agent counter-offers by applying dynamic discounts,
     while strictly enforcing the mathematical Floor Price (Selling Price >= CP * 1.15).
================================================================================
"""
import uuid
from datetime import datetime, timezone, timedelta
from typing import List, Optional
from app.models.supplier import SupplierProfile, SupplierProduct
from app.models.a2a import A2A_RFQ, A2A_Quote, A2A_CounterOffer, QuoteLineItem
from app.knowledge.suppliers import supplier_repo


class SupplierAgent:
    """
    Autonomous Supplier Agent representing wholesale merchants.
    """
    @staticmethod
    def calculate_fifo_discount(product: SupplierProduct, max_discount_pct: float = 0.35) -> float:
        """
        Calculates dynamic aging discount based on batch aging ratio.
        Formula:
          alpha = batch_age_days / shelf_life_days
          Discount = Max_Discount * max(0, (alpha - 0.60) / (1 - 0.60))
        """
        alpha = product.aging_ratio
        if alpha < 0.60:
            return 0.0
        
        scaling_factor = (alpha - 0.60) / 0.40
        discount_pct = min(max_discount_pct, max_discount_pct * scaling_factor)
        discount_amount = round(product.list_price * discount_pct, 2)

        # Enforce floor price check even during discount calculation
        max_allowed_discount = round(product.list_price - product.floor_price, 2)
        return max(0.0, min(discount_amount, max_allowed_discount))

    @classmethod
    def generate_quote(cls, supplier: SupplierProfile, rfq: A2A_RFQ) -> Optional[A2A_Quote]:
        """
        Constructs an A2A Quote for the requested RFQ.
        Includes primary item and optional aging bundle upsell.
        """
        # 1. Locate primary requested SKU
        primary_product: Optional[SupplierProduct] = None
        for prod in supplier.catalog:
            if prod.sku == rfq.primary_sku and prod.available_stock > 0:
                primary_product = prod
                break

        if not primary_product:
            return None  # Supplier does not stock this item

        # 2. Build primary line item
        needed_qty = min(rfq.requested_qty, primary_product.available_stock)
        primary_unit_price = primary_product.list_price
        primary_total = round(primary_unit_price * needed_qty, 2)

        items = [
            QuoteLineItem(
                sku=primary_product.sku,
                name=primary_product.name,
                quantity=needed_qty,
                unit=primary_product.unit,
                unit_price=primary_unit_price,
                total_price=primary_total,
                cost_price=primary_product.cost_price,
                is_aging_upsell=False,
                batch_age_days=primary_product.batch_age_days,
                discount_applied=0.0
            )
        ]

        subtotal = primary_total
        total_discount = 0.0
        bundle_rationale = None

        # 3. Discover aging items for dynamic bundle upsell (FIFO Revenue Optimization)
        aging_candidates = supplier_repo.find_aging_bundles(supplier, primary_product.sku)
        if aging_candidates:
            aging_item = aging_candidates[0]
            aging_discount = cls.calculate_fifo_discount(aging_item)
            discounted_unit_price = round(aging_item.list_price - aging_discount, 2)
            aging_total = round(discounted_unit_price * 1.0, 2)

            # Only add bundle if total stays within reasonable range (<= RFQ budget * 1.20)
            if (subtotal + aging_total) <= (rfq.max_budget_inr * 1.20):
                items.append(
                    QuoteLineItem(
                        sku=aging_item.sku,
                        name=aging_item.name,
                        quantity=1.0,
                        unit=aging_item.unit,
                        unit_price=discounted_unit_price,
                        total_price=aging_total,
                        cost_price=aging_item.cost_price,
                        is_aging_upsell=True,
                        batch_age_days=aging_item.batch_age_days,
                        discount_applied=aging_discount
                    )
                )
                subtotal += round(aging_item.list_price * 1.0, 2)
                total_discount += aging_discount
                bundle_rationale = (
                    f"Merchant AI bundled '{aging_item.name}' (Batch age: {aging_item.batch_age_days} days) "
                    f"with a dynamic ₹{aging_discount} FIFO discount to optimize warehouse inventory."
                )

        # 4. Delivery fee calculation (₹20/km above 3km)
        delivery_fee = round(max(0.0, (supplier.distance_km - 3.0) * 20.0), 2)
        final_total = round(subtotal - total_discount + delivery_fee, 2)

        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()

        return A2A_Quote(
            quote_id=f"quot_{uuid.uuid4().hex[:8]}",
            rfq_id=rfq.rfq_id,
            supplier_id=supplier.supplier_id,
            supplier_name=supplier.name,
            items=items,
            subtotal=subtotal,
            total_discount=total_discount,
            final_total=final_total,
            delivery_fee=delivery_fee,
            delivery_sla_hours=supplier.delivery_sla_hours,
            distance_km=supplier.distance_km,
            trust_score=supplier.trust_score,
            bundle_rationale=bundle_rationale,
            expires_at_timestamp=expires_at
        )

    @classmethod
    def apply_concession(
        cls,
        supplier: SupplierProfile,
        quote: A2A_Quote,
        counter: A2A_CounterOffer
    ) -> A2A_Quote:
        """
        Evaluates a Buyer counter-offer and applies dynamic price concession.
        GUARANTEE: Concession is strictly bounded by each item's Floor Price (CP * 1.15).
        """
        gap = counter.gap_amount
        if gap <= 0:
            return quote

        # Copy quote for modification
        concession_quote = quote.model_copy(deep=True)
        discount_budget_remaining = gap

        # Try absorbing discount from aging bundle item first, then primary item
        for item in concession_quote.items:
            min_unit_price = round(item.cost_price * 1.15, 2)
            max_item_discount_per_unit = max(0.0, round(item.unit_price - min_unit_price, 2))
            max_item_discount_total = round(max_item_discount_per_unit * item.quantity, 2)

            if max_item_discount_total > 0 and discount_budget_remaining > 0:
                applied_item_discount = min(discount_budget_remaining, max_item_discount_total)
                item.unit_price = round(item.unit_price - (applied_item_discount / item.quantity), 2)
                item.total_price = round(item.unit_price * item.quantity, 2)
                item.discount_applied = round(item.discount_applied + applied_item_discount, 2)
                concession_quote.total_discount = round(concession_quote.total_discount + applied_item_discount, 2)
                discount_budget_remaining = round(discount_budget_remaining - applied_item_discount, 2)

        # Recalculate final total
        concession_quote.final_total = round(
            sum(i.total_price for i in concession_quote.items) + concession_quote.delivery_fee, 2
        )
        return concession_quote


supplier_agent = SupplierAgent()
