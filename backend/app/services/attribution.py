import logging
from typing import Dict, Any
from app.gateway.event_bus import bus
from app.db.repositories import checkouts as checkouts_repo
from app.db.repositories import recovery_attempts as recovery_attempts_repo

logger = logging.getLogger(__name__)

class AttributionService:
    """
    Subscribes to Razorpay webhooks (payment.succeeded).
    Verifies the metadata to incontrovertibly attribute revenue to Kinato.
    """

    @staticmethod
    async def handle_payment_success(event: Dict[str, Any]):
        """
        Processes a successful payment event.
        Looks specifically for `recovery_attempt_id` in the metadata notes.
        """
        payload = event.get("payload", {})
        notes = payload.get("notes", {})

        # Accept both the top-level schema (real webhook / abandonment-detector path)
        # and the legacy notes-nested schema, so every producer of payment.succeeded
        # on the bus attributes correctly.
        recovery_attempt_id = payload.get("recovery_attempt_id") or notes.get("recovery_attempt_id")
        checkout_id = payload.get("checkout_id") or notes.get("checkout_id")
        merchant_id = event.get("merchant_id")
        correlation_id = event.get("correlation_id")
        amount_paid_paise = payload.get("amount", 0)
        amount_paid = amount_paid_paise / 100.0  # Convert paise to INR

        # Mark the checkout paid regardless of attribution - this is what the
        # abandonment sweeper (app/gateway/sweeper.py) checks before firing a
        # false abandonment on a checkout that already succeeded, organic or
        # recovered. A checkout_id with no matching row (e.g. a test event,
        # or a payment not tied to a real checkout) is a safe no-op.
        if checkout_id:
            checkouts_repo.mark_paid(checkout_id, rzp_payment_id=payload.get("payment_id", ""))

        if not recovery_attempt_id:
            logger.info("Payment succeeded, but no recovery_attempt_id found. Organic revenue.")
            return

        logger.info(f"ATTRIBUTION MATCH: Payment linked to Recovery Attempt {recovery_attempt_id}")

        # Real DB update (a recovery_attempt_id with no matching row - e.g. one
        # minted in-memory before call_orchestrator persists it - is a safe no-op;
        # full recovery_attempts persistence lands with the agent rewiring).
        recovery_attempts_repo.update_state(
            recovery_attempt_id, "RECOVERED", attributed_revenue_paise=amount_paid_paise
        )

        # Emit attribution event for the dashboard
        await bus.publish(
            event_type="revenue.attributed",
            payload={
                "recovery_attempt_id": recovery_attempt_id,
                "amount": amount_paid,
                "currency": "INR",
                "checkout_id": checkout_id
            },
            correlation_id=correlation_id,
            merchant_id=merchant_id,
            idempotency_key=f"attr_v1_{recovery_attempt_id}"
        )

# Register the attribution handler to the Event Bus
bus.subscribe("payment.succeeded", AttributionService.handle_payment_success)
