"""
Razorpay webhook receiver - path-scoped per merchant
(POST /webhooks/razorpay/{merchant_id}) because the webhook secret used to
verify the signature must be resolved for that specific merchant *before*
verification can happen at all; a single global secret can't work once
payments are genuinely multi-tenant.

`payment.failed` is the PRIMARY recovery trigger (not `checkout.abandoned`):
it carries the customer's real email/contact directly from Razorpay, needs
no timer, and requires ZERO code on the merchant's site - a merchant who
never touches static/sdk/kinato.js or @kinato/react still gets real recovery
the moment Razorpay tells us a payment failed. `order.created` remains a
secondary signal that feeds the sweeper's timer-based path (for the
"customer just walked away, no failure yet" case).

Money state is never decided here - this only relays an already-
authoritative Razorpay event onto the bus.
"""
import json
import logging
import uuid
from fastapi import APIRouter, Request, HTTPException

from app.core.security import verify_razorpay_webhook_signature
from app.core.crypto import decrypt_secret
from app.db.repositories.merchants import get_merchant, MerchantNotFoundError
from app.db.repositories import checkouts as checkouts_repo
from app.db.repositories import customers as customers_repo
from app.gateway.event_bus import bus

logger = logging.getLogger(__name__)
payments_router = APIRouter()


@payments_router.post("/webhooks/razorpay/{merchant_id}")
async def razorpay_webhook(merchant_id: str, request: Request):
    try:
        merchant = get_merchant(merchant_id)
    except MerchantNotFoundError:
        raise HTTPException(status_code=404, detail="Unknown merchant.")

    webhook_secret = decrypt_secret(merchant.get("rzp_webhook_secret_enc") or "")
    if not webhook_secret:
        raise HTTPException(status_code=400, detail="This merchant has not configured a Razorpay webhook secret yet.")

    raw_body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")
    if not verify_razorpay_webhook_signature(raw_body, signature, webhook_secret):
        logger.warning(f"Rejected Razorpay webhook for merchant {merchant_id}: invalid signature.")
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    event_data = json.loads(raw_body)
    event_name = event_data.get("event", "")
    payload = event_data.get("payload", {})
    payment_entity = payload.get("payment", {}).get("entity", {})
    order_entity = payload.get("order", {}).get("entity", {})
    notes = payment_entity.get("notes") or order_entity.get("notes") or {}

    checkout_id = notes.get("checkout_id")
    recovery_attempt_id = notes.get("recovery_attempt_id")
    correlation_id = checkout_id or payment_entity.get("order_id") or order_entity.get("id") or "unknown"

    # --- PRIMARY: payment.failed - zero-code recovery trigger ---
    if event_name == "payment.failed":
        email = payment_entity.get("email", "")
        contact = payment_entity.get("contact", "")
        amount_paise = payment_entity.get("amount", 0)

        customer = None
        if email or contact:
            customer = customers_repo.upsert_by_contact(merchant_id, email=email, phone=contact)

        if not checkout_id:
            # No prior SDK/API integration ever tracked this checkout (the
            # zero-code path) - create the row now from the webhook itself,
            # so it's a real row the same as any other, not a special case.
            checkout_id = f"chk_wh_{payment_entity.get('id', uuid.uuid4().hex[:8])}"
            checkouts_repo.create_checkout(
                merchant_id, amount_paise=amount_paise,
                customer_id=customer["customer_id"] if customer else None,
                source="razorpay_webhook", checkout_id=checkout_id,
            )

        if not customer:
            logger.info(f"payment.failed for {checkout_id} carried no contactable customer - recovery blocked.")
            await bus.publish(
                event_type="recovery.blocked",
                payload={"checkout_id": checkout_id, "reason": "no_contact"},
                correlation_id=correlation_id, merchant_id=merchant_id,
            )
            return {"status": "ok", "event": event_name}

        await bus.publish(
            event_type="checkout.payment_failed",
            payload={
                "checkout_id": checkout_id,
                "customer_id": customer["customer_id"],
                "amount": amount_paise / 100.0,
                "amount_paise": amount_paise,
                "currency": payment_entity.get("currency", "INR"),
                "error_reason": payment_entity.get("error_reason"),
                "recovery_attempt_id": recovery_attempt_id,
            },
            correlation_id=correlation_id,
            merchant_id=merchant_id,
            idempotency_key=f"webhook_{payment_entity.get('id', correlation_id)}",
        )
        return {"status": "ok", "event": event_name}

    # --- SECONDARY: order.created - feeds the sweeper's timer path ---
    if event_name == "order.created":
        order_id = order_entity.get("id")
        if order_id and not checkouts_repo.get_checkout(checkout_id or ""):
            checkouts_repo.create_checkout(
                merchant_id, amount_paise=order_entity.get("amount", 0),
                source="razorpay_webhook", checkout_id=checkout_id or f"chk_wh_{order_id}",
            )
        return {"status": "ok", "event": event_name}

    # --- payment succeeded, from any of Razorpay's several event names for it ---
    if event_name in ("payment.captured", "payment.authorized", "order.paid", "payment_link.paid"):
        await bus.publish(
            event_type="payment.succeeded",
            payload={
                "amount": payment_entity.get("amount", 0),
                "payment_id": payment_entity.get("id"),
                "order_id": payment_entity.get("order_id"),
                "checkout_id": checkout_id,
                "recovery_attempt_id": recovery_attempt_id,
                "notes": notes,
            },
            correlation_id=correlation_id,
            merchant_id=merchant_id,
            idempotency_key=f"webhook_{payment_entity.get('id', correlation_id)}",
        )
        return {"status": "ok", "event": event_name}

    # --- rail health - suppresses outreach while Razorpay itself is degraded ---
    if event_name in ("payment.downtime.started", "payment.downtime.resolved"):
        await bus.publish(
            event_type="rail.degraded",
            payload={"status": "down" if event_name.endswith("started") else "resolved"},
            correlation_id=merchant_id,
            merchant_id=merchant_id,
        )
        return {"status": "ok", "event": event_name}

    return {"status": "ignored", "event": event_name}
