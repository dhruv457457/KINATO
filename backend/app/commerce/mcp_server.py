import logging
import uuid
import time
from typing import Dict, Any, Optional
from app.commerce.catalog import merchant_catalog
from app.payments.spend_mandate import spend_mandate_service
from app.gateway.event_bus import bus

logger = logging.getLogger(__name__)

class AgentCommerceMCP:
    """
    Exposes a tiny, deliberate tool surface for external AI buyers (e.g., Claude).
    Never grants authority over state—acts as a trusted commerce boundary.
    """
    
    def __init__(self):
        # In-memory quote store. In production: Redis/DB with TTL.
        self._active_quotes: Dict[str, Dict[str, Any]] = {}

    def search_products(self, query: str, max_price: Optional[float] = None) -> list:
        """Tool 1: Allow AI to discover normalized products."""
        logger.info(f"[MCP] AI Buyer searching for: '{query}' (Max Price: {max_price})")
        products = merchant_catalog.search(query, max_price)
        return [p.model_dump() for p in products]

    def quote(self, product_id: str, quantity: int = 1) -> Dict[str, Any]:
        """Tool 2: Generates an immutable snapshot of a product's price."""
        logger.info(f"[MCP] AI Buyer requesting quote for {product_id} (qty: {quantity})")
        
        product = merchant_catalog.get_product(product_id)
        if not product:
            return {"error": "Product not found"}
            
        if product.inventory_count < quantity:
            return {"error": f"Insufficient inventory. Only {product.inventory_count} remaining."}

        quote_id = f"quote_{uuid.uuid4().hex[:8]}"
        quoted_amount = product.price * quantity
        expires_at = time.time() + 600 # 10 minute expiry

        quote_snapshot = {
            "quote_id": quote_id,
            "product_id": product_id,
            "quantity": quantity,
            "quoted_amount": quoted_amount,
            "currency": product.currency,
            "expires_at": expires_at,
            "snapshot": {
                "unit_price": product.price,
                "product_name": product.name
            }
        }
        
        self._active_quotes[quote_id] = quote_snapshot
        return quote_snapshot

    def _revalidate_quote(self, quote_id: str) -> Dict[str, Any]:
        """
        Shared strict revalidation for both purchase paths (payment-link and
        autonomous). Returns {"ok": True, "quote": ..., "product": ...} or
        {"ok": False, "reason": ...}. Never mutates state.
        """
        quote = self._active_quotes.get(quote_id)
        if not quote:
            logger.warning("❌ PURCHASE INTENT REJECTED: reason=quote_not_found")
            return {"ok": False, "reason": "quote_not_found"}

        if time.time() > quote["expires_at"]:
            logger.warning("❌ PURCHASE INTENT REJECTED: reason=quote_expired")
            return {"ok": False, "reason": "quote_expired"}

        current_product = merchant_catalog.get_product(quote["product_id"])
        if not current_product:
            logger.warning("❌ PURCHASE INTENT REJECTED: reason=product_unavailable")
            return {"ok": False, "reason": "product_unavailable"}

        if current_product.price != quote["snapshot"]["unit_price"]:
            logger.warning(f"❌ PURCHASE INTENT REJECTED: reason=quote_price_mismatch "
                           f"(Quoted: {quote['snapshot']['unit_price']}, Current: {current_product.price})")
            return {"ok": False, "reason": "quote_price_mismatch"}

        if current_product.inventory_count < quote["quantity"]:
            logger.warning("❌ PURCHASE INTENT REJECTED: reason=inventory_unavailable")
            return {"ok": False, "reason": "inventory_unavailable"}

        return {"ok": True, "quote": quote, "product": current_product}

    async def create_purchase_intent(self, quote_id: str, buyer_id: str = "ai_agent") -> Dict[str, Any]:
        """
        Tool 3 (Path B - human-in-the-loop checkout): AI requests to buy based
        on a Quote. Triggers STRICT REVALIDATION, then hands off to a normal
        Razorpay payment link (same rail a human recovery checkout uses).
        """
        logger.info(f"[MCP] AI Buyer requesting intent for quote {quote_id}")

        revalidation = self._revalidate_quote(quote_id)
        if not revalidation["ok"]:
            return {"error": "REJECTED", "reason": revalidation["reason"]}

        # TODO(catalog multi-tenancy): merchant_catalog (app/commerce/catalog.py)
        # is still a single process-wide in-memory catalog with no merchant_id
        # of its own - it hasn't been migrated onto the real per-merchant
        # `products` table yet (see app/db/repositories/products.py, which
        # already supports this). Until that migration lands there is no real
        # merchant to hand off a payment link to, so this fails clearly
        # instead of guessing/hardcoding one. Once migrated, this becomes:
        #
        #   ai_checkout_id = f"ai_chk_{uuid.uuid4().hex[:6]}"
        #   payment_link = await payment_execution.generate_recovery_checkout(
        #       merchant_id=quote["merchant_id"],
        #       checkout_id=ai_checkout_id,
        #       customer_id=buyer_id,
        #       recovery_attempt_id=quote_id,
        #       original_amount=quote["quoted_amount"],
        #       approved_discount_percent=0.0,  # AI buyers pay full price unless policy allows wholesale
        #   )
        #   return {"status": "APPROVED", "payment_url": payment_link["url"], "quote_id": quote_id}
        logger.info("PURCHASE INTENT REVALIDATED, but no real merchant to bill yet (see TODO).")
        return {"error": "REJECTED", "reason": "catalog_not_yet_multi_tenant"}

    async def create_purchase_intent_autonomous(
        self, quote_id: str, mandate_id: str, buyer_id: str = "ai_agent"
    ) -> Dict[str, Any]:
        """
        Tool 3b (Path A - autonomous purchase against a Kinato spend mandate):
        for AI buyers already covered by a merchant-authorized daily-spend
        mandate (see /api/commerce/mandate). Runs the exact same strict
        revalidation as the human-in-the-loop path, then records the purchase
        immediately against the mandate's pre-authorized daily cap - no
        payment link, no human approval per transaction. The mandate's own
        daily-cap check (in app/payments/spend_mandate.py) is the
        deterministic gate; the LLM never decides whether the spend is
        allowed. See that file's docstring: this is a Kinato-enforced cap on
        top of a real Razorpay Order, not an NPCI/RBI-compliant UPI Autopay
        settlement.
        """
        logger.info(f"[MCP] AI Buyer requesting AUTONOMOUS intent for quote {quote_id} via mandate {mandate_id}")

        revalidation = self._revalidate_quote(quote_id)
        if not revalidation["ok"]:
            await bus.publish(
                event_type="ai_commerce.intent_rejected",
                payload={"quote_id": quote_id, "mandate_id": mandate_id, "reason": revalidation["reason"]},
                correlation_id=quote_id,
                merchant_id="jiva_demo",
            )
            return {"error": "REJECTED", "reason": revalidation["reason"]}

        quote, product = revalidation["quote"], revalidation["product"]

        result = spend_mandate_service.execute_autonomous_payment(
            mandate_id=mandate_id,
            proposal_id=quote_id,
            amount_inr=quote["quoted_amount"],
            supplier_id="jiva_demo",
            supplier_name="Jiva Lifestyle",
            description=f"AI-buyer autonomous purchase: {product.name}",
        )

        if not result.get("success"):
            logger.warning(f"❌ AUTONOMOUS PAYMENT BLOCKED: {result.get('error')}")
            await bus.publish(
                event_type="ai_commerce.intent_rejected",
                payload={"quote_id": quote_id, "mandate_id": mandate_id, "reason": result.get("error")},
                correlation_id=quote_id,
                merchant_id="jiva_demo",
            )
            return {"error": "REJECTED", "reason": result.get("error")}

        logger.info(f"✅ AUTONOMOUS PAYMENT SETTLED: {result['message']}")
        await bus.publish(
            event_type="payment.succeeded",
            payload={
                "amount": int(quote["quoted_amount"] * 100),
                "checkout_id": f"ai_auto_{quote_id}",
                "payment_id": result["payment_id"],
                "notes": {"source": "ai_buyer_autonomous", "mandate_id": mandate_id, "buyer_id": buyer_id},
            },
            correlation_id=quote_id,
            merchant_id="jiva_demo",
        )

        return {
            "status": "SETTLED",
            "payment_id": result["payment_id"],
            "amount_inr": result["amount_inr"],
            "daily_spent": result["daily_spent"],
            "daily_limit": result["daily_limit"],
            "quote_id": quote_id,
        }

    def mock_expire_quote(self, quote_id: str):
        """Test helper to simulate expiry"""
        if quote_id in self._active_quotes:
            self._active_quotes[quote_id]["expires_at"] = time.time() - 100

ai_commerce_mcp = AgentCommerceMCP()
