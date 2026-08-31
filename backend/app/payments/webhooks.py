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
from app.db.repositories.merchants import (
    get_merchant, set_rail_degraded, is_rail_degraded, MerchantNotFoundError,
)
from app.services.failure_diagnosis import diagnose
from app.services.identity_service import identity_service
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
    # The payment LINK's own entity. This is where a `payment_link.paid`
    # event carries its metadata, and it was never read.
    #
    # payment_execution puts recovery_attempt_id in the link's `notes` AND
    # in its `reference_id`, so everything attribution needs was being sent
    # correctly - it just arrived under a key nothing looked at. A customer
    # who actually paid a recovery link left the attempt sitting at
    # PAYMENT_LINK_SENT forever, and the dashboard reported the money as
    # never recovered. Confirmed live: link plink_TWQ6dr4mx3R7gI paid in
    # full, attempt still "Offer sent".
    #
    # Link notes are NOT copied onto the payment entity by Razorpay, which
    # is why the payment-first lookup below silently found nothing.
    link_entity = payload.get("payment_link", {}).get("entity", {})
    notes = (
        payment_entity.get("notes")
        or link_entity.get("notes")
        or order_entity.get("notes")
        or {}
    )

    checkout_id = notes.get("checkout_id")
    # reference_id as a fallback: it is set to the recovery_attempt_id at
    # link creation, so attribution survives even a link whose notes were
    # stripped or never stored.
    recovery_attempt_id = notes.get("recovery_attempt_id") or link_entity.get("reference_id")
    correlation_id = (
        checkout_id
        or payment_entity.get("order_id")
        or order_entity.get("id")
        or link_entity.get("id")
        or "unknown"
    )

    # --- PRIMARY: payment.failed - zero-code recovery trigger ---
    if event_name == "payment.failed":
        email = payment_entity.get("email", "")
        contact = payment_entity.get("contact", "")
        amount_paise = payment_entity.get("amount", 0)

        # Everything Razorpay tells us about WHY it failed. Previously only
        # error_reason was lifted, it travelled no further than the event
        # payload, and nothing read it - so a bank timeout, an abandoned
        # 3DS step and a stolen-card block all produced the identical sales
        # call. See app/services/failure_diagnosis.py for what these turn
        # into, and why that classification lives in code rather than in
        # the agent's prompt.
        failure = {
            "error_code": payment_entity.get("error_code"),
            "error_reason": payment_entity.get("error_reason"),
            "error_description": payment_entity.get("error_description"),
            "error_source": payment_entity.get("error_source"),
            "error_step": payment_entity.get("error_step"),
            "method": payment_entity.get("method"),
        }
        diagnosis = diagnose(failure, rail_degraded=is_rail_degraded(merchant_id))

        customer = None
        if email or contact:
            customer = customers_repo.upsert_by_contact(merchant_id, email=email, phone=contact)
            # Without this the zero-code path - the one the README leads
            # with - recovered nothing at all. The customer was created,
            # no consent was recorded, and the eligibility gate refused
            # them silently. See identity_service.grant_transactional_consent
            # for why this is defensible and for the revocation it will
            # never overwrite.
            await identity_service.grant_transactional_consent(
                merchant_id, customer["customer_id"], email=email, phone=contact
            )

        if not checkout_id:
            # Before opening a new one, look for a cart this merchant
            # already tracked for the same Razorpay order. The SDK records
            # it under the order id well before any payment fails, and only
            # consulting notes.checkout_id meant the webhook never found it
            # - so one abandoned cart became two checkouts, two recovery
            # attempts, and two phone calls to one person about one order.
            existing = checkouts_repo.find_by_order_id(
                merchant_id, payment_entity.get("order_id") or ""
            )
            if existing:
                checkout_id = existing["checkout_id"]
                logger.info(
                    f"payment.failed matched an existing checkout {checkout_id} "
                    f"via order {payment_entity.get('order_id')!r} - not opening a second one."
                )

        if not checkout_id:
            # Genuinely untracked: nothing on the storefront ever told us
            # about this cart (the zero-code path). Create the row now from
            # the webhook itself, so it is a real row like any other.
            checkout_id = f"chk_wh_{payment_entity.get('id', uuid.uuid4().hex[:8])}"
            checkouts_repo.create_checkout(
                merchant_id, amount_paise=amount_paise,
                customer_id=customer["customer_id"] if customer else None,
                source="razorpay_webhook", checkout_id=checkout_id,
            )

        checkouts_repo.record_failure(checkout_id, failure, diagnosis.failure_class)
        logger.info(
            f"payment.failed for {checkout_id} classified as {diagnosis.failure_class} "
            f"(code={failure['error_code']!r}, step={failure['error_step']!r})."
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
                "failure": failure,
                "failure_class": diagnosis.failure_class,
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
                # amount_paid off the link is the fallback, not decoration:
                # attribution writes this straight into
                # attributed_revenue_paise, so a missing payment entity on a
                # payment_link.paid would record a real recovery worth zero
                # rupees - which is worse than not recording it, because the
                # dashboard would then report the recovery rate correctly and
                # the revenue wrongly.
                "amount": payment_entity.get("amount") or link_entity.get("amount_paid") or 0,
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

    # --- rail health - a real stopping rule, not just an event nobody
    # reads: persists durably so RecoveryEligibilityService can refuse new
    # outreach for this merchant while Razorpay itself is degraded (never
    # call/email a customer over a failure that might just be an outage,
    # not a real decline). Durable rather than in-memory so it survives a
    # Railway restart landing mid-outage.
    if event_name in ("payment.downtime.started", "payment.downtime.resolved"):
        degraded = event_name.endswith("started")
        set_rail_degraded(merchant_id, degraded)
        await bus.publish(
            event_type="rail.degraded",
            payload={"status": "down" if degraded else "resolved"},
            correlation_id=merchant_id,
            merchant_id=merchant_id,
        )
        return {"status": "ok", "event": event_name}

    return {"status": "ignored", "event": event_name}
