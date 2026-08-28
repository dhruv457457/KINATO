import logging
from typing import Dict, Any
from app.gateway.event_bus import bus
from app.services.identity_service import identity_service
from app.db.repositories import checkouts as checkouts_repo
from app.db.repositories import recovery_attempts as recovery_attempts_repo
from app.db.repositories.merchants import is_rail_degraded

logger = logging.getLogger(__name__)

class RecoveryEligibilityService:
    """
    Subscribes to both checkout.abandoned (sweeper-driven, timer-based) and
    checkout.payment_failed (webhook-driven, the zero-code primary trigger -
    see app/payments/webhooks.py). Both carry the same shape (checkout_id,
    customer_id, amount, currency), so one handler serves both.
    Acts as the final safeguard before generating a Recovery Opportunity.
    Performs the double race-condition check and verifies identity/consent.
    """

    @staticmethod
    async def evaluate_abandonment(event: Dict[str, Any]):
        payload = event.get("payload", {})
        checkout_id = payload.get("checkout_id")
        customer_id = payload.get("customer_id")
        merchant_id = event.get("merchant_id")
        correlation_id = event.get("correlation_id")

        if not checkout_id or not customer_id:
            logger.warning("Eligibility Check Failed: Missing checkout_id or customer_id in snapshot.")
            return

        logger.info(f"Evaluating recovery eligibility for checkout {checkout_id}")

        # 0. Stopping rule: Razorpay's own rail is down for this merchant
        # (set by a real payment.downtime.started webhook - see
        # app/payments/webhooks.py). Don't call or email a customer over a
        # failure that might just be an outage, not a real decline; the
        # sweeper/webhook will re-fire once the rail recovers or the
        # customer's own next attempt succeeds.
        if merchant_id and is_rail_degraded(merchant_id):
            logger.info(f"Eligibility Check Failed: Razorpay rail is degraded for merchant {merchant_id} - holding outreach.")
            await bus.publish(
                event_type="recovery.blocked",
                payload={"checkout_id": checkout_id, "reason": "rail_degraded"},
                correlation_id=correlation_id,
                merchant_id=merchant_id,
            )
            return

        # 1. Double Race-Condition Protection: Is it STILL unpaid?
        # Even though AbandonmentDetector checked, we check again at the exact moment of opportunity generation.
        is_paid = await RecoveryEligibilityService._check_db_if_paid(checkout_id)
        if is_paid:
            logger.info(f"Eligibility Check Failed: Checkout {checkout_id} was paid between abandonment and eligibility check.")
            return

        # 2. Is there already an active recovery attempt for this checkout?
        is_active = await RecoveryEligibilityService._check_active_recovery(checkout_id)
        if is_active:
            logger.info(f"Eligibility Check Failed: Active recovery already exists for {checkout_id}.")
            return

        # 3. Identity/Consent Gate Check (Before Outreach)
        consent_granted = await identity_service.check_consent(merchant_id, customer_id, channel="voice")
        if not consent_granted:
            logger.info(f"Eligibility Check Failed: No voice consent for customer {customer_id}.")
            return

        logger.info(f"Eligibility Check Passed. Generating Recovery Opportunity for {checkout_id}.")
        
        # Emit Opportunity Created (this triggers the Call Orchestrator)
        await bus.publish(
            event_type="recovery.opportunity.created",
            payload={
                "checkout_id": checkout_id,
                "customer_id": customer_id,
                "amount": payload.get("amount"),
                "currency": payload.get("currency"),
                "state_machine_status": "ELIGIBILITY_CHECKED"
            },
            correlation_id=correlation_id,
            merchant_id=merchant_id,
            idempotency_key=f"opportunity_v1_{checkout_id}"
        )

    @staticmethod
    async def _check_db_if_paid(checkout_id: str) -> bool:
        """Authoritative re-check against the real checkouts row (updated by
        attribution.py the moment a payment.succeeded webhook lands)."""
        return checkouts_repo.is_paid(checkout_id)

    @staticmethod
    async def _check_active_recovery(checkout_id: str) -> bool:
        """Prevents a second recovery attempt from being opened for a checkout that
        already has one in flight (e.g. if checkout.abandoned were ever re-fired)."""
        return len(recovery_attempts_repo.list_active_for_checkout(checkout_id)) > 0

# Register subscriber - both the timer-based (sweeper) and webhook-driven
# (payment.failed, zero-code) abandonment signals feed the same eligibility gate.
bus.subscribe("checkout.abandoned", RecoveryEligibilityService.evaluate_abandonment)
bus.subscribe("checkout.payment_failed", RecoveryEligibilityService.evaluate_abandonment)
