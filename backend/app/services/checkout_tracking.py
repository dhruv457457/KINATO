"""
Persists every `checkout.started` event to the real `checkouts` table,
regardless of whether it arrived via the ingestion API (app/api/events.py),
a Razorpay webhook (app/payments/webhooks.py), or a direct bus.publish (demo
scripts/tests). This is what gives app/gateway/sweeper.py something real to
query - a checkout only exists to be later marked abandoned/paid if a row
was actually written here first.
"""
import logging
from typing import Dict, Any
from app.gateway.event_bus import bus
from app.db.repositories import checkouts as checkouts_repo

logger = logging.getLogger(__name__)


async def handle_checkout_started(event: Dict[str, Any]):
    payload = event.get("payload", {})
    merchant_id = event.get("merchant_id")
    checkout_id = payload.get("checkout_id")

    if not checkout_id or not merchant_id:
        logger.warning("checkout.started missing checkout_id/merchant_id - cannot persist for the sweeper to see.")
        return

    if checkouts_repo.get_checkout(checkout_id):
        return  # already tracked - defensive idempotency beyond the bus's own idempotency_key

    amount = payload.get("amount")
    amount_paise = payload.get("amount_paise")
    if amount_paise is None:
        amount_paise = int(round(float(amount) * 100)) if amount is not None else 0

    checkouts_repo.create_checkout(
        merchant_id=merchant_id,
        amount_paise=amount_paise,
        customer_id=payload.get("customer_id"),
        cart_id=payload.get("cart_id", ""),
        currency=payload.get("currency", "INR"),
        line_items=payload.get("product_ids") or payload.get("line_items") or [],
        source=payload.get("source", "sdk"),
        checkout_id=checkout_id,
    )
    logger.info(f"Persisted checkout {checkout_id} for merchant {merchant_id} (amount_paise={amount_paise}).")


bus.subscribe("checkout.started", handle_checkout_started)
