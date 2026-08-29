import logging
from typing import Dict, Any
from app.gateway.event_bus import bus
from app.services.identity_service import identity_service
from app.db.database import run_db_async
from app.db.repositories import checkouts as checkouts_repo
from app.db.repositories import recovery_attempts as recovery_attempts_repo
from app.db.repositories.merchants import is_rail_degraded
from app.services import outreach_guards

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
            # QUEUED, not dropped. This used to publish `blocked` and
            # return, and nothing anywhere ever re-fired the case: the
            # sweeper only picks up checkouts still in `started`, and a
            # payment.failed case was never in that status. So every
            # customer whose payment failed during an outage was lost
            # permanently - for a reason that was our problem, not theirs.
            logger.info(f"Rail degraded for merchant {merchant_id} - queueing {checkout_id} until it clears.")
            await run_db_async(checkouts_repo.queue_for_rail_recovery, checkout_id)
            await bus.publish(
                event_type="recovery.blocked",
                payload={
                    "checkout_id": checkout_id,
                    "reason": "rail_degraded",
                    "detail": "held until Razorpay recovers, then re-evaluated from scratch",
                },
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
        #
        # Two things were wrong here, and they compounded.
        #
        # It asked only about VOICE, so a customer with an email address
        # and no phone number was refused outright rather than recovered
        # by another route. And it returned WITHOUT publishing
        # recovery.blocked - unlike the two branches directly above it -
        # so the refusal was invisible: no event, no error, nothing on the
        # dashboard. A merchant saw revenue at risk and zero attempts
        # against it, with nothing anywhere explaining why. That is the
        # same silent-failure shape as FINDINGS #3, in the path the
        # README leads with.
        channels = await identity_service.reachable_channels(merchant_id, customer_id)
        if not channels:
            logger.info(f"Eligibility Check Failed: no consented channel for customer {customer_id}.")
            await bus.publish(
                event_type="recovery.blocked",
                payload={
                    "checkout_id": checkout_id,
                    "customer_id": customer_id,
                    "reason": "no_consent",
                    "detail": "no channel has a granted consent record for this customer",
                },
                correlation_id=correlation_id,
                merchant_id=merchant_id,
            )
            return

        # Quiet hours, checked HERE rather than only before dialling.
        #
        # The pre-dial guard is correct and stays, but on its own it
        # produced this, once per sweeper pass, all night: create an
        # attempt, generate an opening line with a real LLM call, block it,
        # mark it terminal, become eligible again, repeat. Every cycle cost
        # money and wrote a row, and none of it could ever reach anyone.
        #
        # Only when voice is the ONLY way we could reach them. Calling
        # hours are a courtesy about telephones; an email channel is not
        # subject to them, and queueing an email-reachable customer because
        # the phone window shut would be inventing a restriction.
        if channels == ["voice"]:
            hours_ok, hours_reason = await run_db_async(
                outreach_guards.within_calling_hours, merchant_id
            )
            if not hours_ok:
                logger.info(f"Queueing {checkout_id} until the calling window opens: {hours_reason}")
                await run_db_async(checkouts_repo.queue_for_rail_recovery, checkout_id)
                await bus.publish(
                    event_type="recovery.blocked",
                    payload={
                        "checkout_id": checkout_id,
                        "customer_id": customer_id,
                        "reason": "quiet_hours",
                        "detail": f"{hours_reason} - held until the window opens, then re-evaluated",
                    },
                    correlation_id=correlation_id,
                    merchant_id=merchant_id,
                )
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
                # Which routes are actually open to this customer. Carried
                # so the orchestrator does not have to ask again, and so a
                # phone-less customer is visibly an email case rather than
                # a mystery.
                "consented_channels": channels,
                "state_machine_status": "ELIGIBILITY_CHECKED"
            },
            correlation_id=correlation_id,
            merchant_id=merchant_id,
            idempotency_key=f"opportunity_v1_{checkout_id}"
        )



    @staticmethod
    async def drain_rail_queue(event: Dict[str, Any]):
        """Razorpay is healthy again - reconsider what we held back.

        Deliberately re-publishes `checkout.payment_failed` rather than
        jumping straight to creating opportunities. That sends every queued
        case back through THIS function from the top, so the paid check,
        the active-attempt check, consent and every future guard all run
        again on release. Nothing is grandfathered past a stop because it
        happened to be queued when the stop was added.
        """
        if (event.get("payload") or {}).get("status") != "resolved":
            return
        merchant_id = event.get("merchant_id")
        if not merchant_id:
            return

        queued = await run_db_async(checkouts_repo.list_queued_for_rail, merchant_id)
        await run_db_async(checkouts_repo.clear_rail_queue, merchant_id)
        if not queued:
            return

        logger.info(f"Rail recovered for {merchant_id} - re-evaluating {len(queued)} queued checkout(s).")
        for checkout in queued:
            await bus.publish(
                event_type="checkout.payment_failed",
                payload={
                    "checkout_id": checkout["checkout_id"],
                    "customer_id": checkout.get("customer_id"),
                    "amount": (checkout.get("amount_paise") or 0) / 100.0,
                    "amount_paise": checkout.get("amount_paise"),
                    "currency": checkout.get("currency", "INR"),
                    "requeued_after_outage": True,
                },
                correlation_id=checkout["checkout_id"],
                merchant_id=merchant_id,
                idempotency_key=f"rail_requeue_{checkout['checkout_id']}",
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
# The other half of the rail stopping rule. `rail.degraded` was published
# to nobody, so "resolved" meant nothing and the held cases stayed held
# forever.
bus.subscribe("rail.degraded", RecoveryEligibilityService.drain_rail_queue)
