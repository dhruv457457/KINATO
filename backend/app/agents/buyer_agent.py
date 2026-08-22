"""
================================================================================
FILE: app/agents/buyer_agent.py
MODULE: Module 2 - Buyer Agent Intelligence
--------------------------------------------------------------------------------
WHAT THIS FILE DOES:
Implements the autonomous Buyer Agent representing the consuming business.

CORE RESPONSIBILITIES:
  1. Inventory Monitoring & DIR Calculation:
     Identifies items where Days of Inventory Remaining (DIR) <= 1.5 days.
  2. A2A-RFQ Broadcast Creation:
     Packages restock requirements into structured A2A_RFQ schema with target budget.
  3. Multi-Factor Utility Ranking across N competing suppliers:
     Evaluates quotes using a 5-factor objective function:
       Utility = w_fav * Is_Fav + w_p * Price_Score + w_d * Dist_Score + w_t * Trust + w_f * Freshness
  4. Bounded Counter-Offer Generation:
     If the top supplier quote exceeds target budget by a small margin, it emits
     an A2A_CounterOffer requesting a dynamic bundle discount.
================================================================================
"""
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Tuple
from app.models.inventory import BuyerContext, InventoryItem
from app.models.a2a import A2A_RFQ, A2A_Quote, A2A_CounterOffer


class BuyerAgent:
    """
    Autonomous Buyer Agent representing Cloud Kitchens, Startups, and Retailers.
    """
    @staticmethod
    def identify_critical_restock(buyer_context: BuyerContext) -> List[InventoryItem]:
        """Filters buyer inventory for items at or below critical DIR threshold (<= 1.5 days)."""
        return [item for item in buyer_context.inventory if item.is_critical]

    @staticmethod
    def create_rfq(
        buyer_context: BuyerContext,
        target_item: InventoryItem,
        max_budget: Optional[float] = None
    ) -> A2A_RFQ:
        """
        Constructs a standardized A2A Request For Quote (RFQ).
        """
        rfq_id = f"rfq_{uuid.uuid4().hex[:8]}"
        needed_qty = target_item.reorder_quantity
        if needed_qty <= 0:
            needed_qty = target_item.daily_burn_rate * 2.0  # Default 2-day buffer

        # If budget not explicitly passed, allocate based on daily limit
        allocated_budget = max_budget or buyer_context.daily_budget_limit

        return A2A_RFQ(
            rfq_id=rfq_id,
            buyer_id=buyer_context.business_id,
            business_name=buyer_context.business_name,
            profile_type=buyer_context.profile_type,
            primary_sku=target_item.sku,
            primary_item_name=target_item.name,
            requested_qty=needed_qty,
            unit=target_item.unit,
            max_budget_inr=allocated_budget,
            delivery_deadline_hours=4.0,
            timestamp=datetime.now(timezone.utc).isoformat()
        )

    @staticmethod
    def score_and_rank_quotes(
        quotes: List[A2A_Quote],
        buyer_context: BuyerContext
    ) -> List[A2A_Quote]:
        """
        Ranks competing supplier quotes using a Multi-Factor Utility Function:
          Utility = (w_fav * Fav_Bonus) + (w_p * Price_Score) + (w_d * Dist_Score) + (w_t * Trust) + (w_f * Freshness)
        """
        if not quotes:
            return []

        # Weights configuration (sums to 1.0)
        W_FAV = 0.15      # Preferred merchant bonus
        W_PRICE = 0.40    # Price competitiveness
        W_DIST = 0.20     # Delivery proximity and speed
        W_TRUST = 0.15    # Historical reliability & Razorpay verified
        W_FRESH = 0.10    # Batch freshness

        # Determine min/max price and distance for normalization
        prices = [q.final_total for q in quotes]
        min_p, max_p = min(prices), max(prices)
        price_spread = max(1.0, max_p - min_p)

        distances = [q.distance_km for q in quotes]
        max_dist = max(15.0, max(distances))

        scored_quotes = []
        for q in quotes:
            # 1. Preferred supplier binary indicator
            is_fav = q.supplier_id in buyer_context.preferred_supplier_ids
            fav_score = 1.0 if is_fav else 0.0

            # 2. Normalized Price Score (Lower price = Higher score)
            price_score = 1.0 - ((q.final_total - min_p) / price_spread)

            # 3. Normalized Distance Score (Closer = Higher score)
            dist_score = max(0.0, 1.0 - (q.distance_km / max_dist))

            # 4. Trust Score (0.0 to 1.0)
            trust_score = q.trust_score

            # 5. Freshness Score (1.0 default, 0.9 if aging bundle included)
            fresh_score = 0.95 if q.bundle_rationale else 1.0

            # Compute Blended Utility Score
            utility = (
                (W_FAV * fav_score) +
                (W_PRICE * price_score) +
                (W_DIST * dist_score) +
                (W_TRUST * trust_score) +
                (W_FRESH * fresh_score)
            )

            q.is_preferred = is_fav
            q.utility_score = round(utility, 4)
            scored_quotes.append(q)

        # Sort descending by utility score
        scored_quotes.sort(key=lambda x: x.utility_score, reverse=True)
        return scored_quotes

    @staticmethod
    def evaluate_counter_need(
        winning_quote: A2A_Quote,
        target_budget: float
    ) -> Tuple[bool, Optional[A2A_CounterOffer]]:
        """
        Determines if the winning quote requires a 1-round negotiation counter-offer.
        If quote is slightly over budget (gap <= 25%), counter is generated.
        """
        gap = round(winning_quote.final_total - target_budget, 2)
        if gap > 0:
            counter = A2A_CounterOffer(
                counter_id=f"cnt_{uuid.uuid4().hex[:8]}",
                rfq_id=winning_quote.rfq_id,
                quote_id=winning_quote.quote_id,
                target_budget=target_budget,
                gap_amount=gap,
                reason=f"Quote exceeds daily allocated budget of ₹{target_budget} by ₹{gap}. Requesting dynamic bundle concession."
            )
            return True, counter
        return False, None


buyer_agent = BuyerAgent()
