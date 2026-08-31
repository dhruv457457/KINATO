"""
Manages the RecoveryAttempt state machine and actually places the call.

Previously this and discovery_agent.py were *parallel* subscribers to the
same `recovery.opportunity.created` event - this orchestrator never waited
for the strategist's plan, so the plan was published to an event nobody
consumed, and the orchestrator dialed with nothing but a hardcoded greeting.
It also only ever published a `call.started` *event* - nothing subscribed
to that either, so no phone ever actually rang outside of manually hitting
voice_runtime.py's old /voice/call-me debug route. Both are fixed here:
this now waits (with a 6s watchdog) for `recovery.plan_ready`, and actually
calls app/services/voice_dispatch.py to dial Twilio.

_process_offer_request (the old hardcoded-cart offer path, keyed off a
separate `customer.understood` event from customer_intelligence.py) is
deleted entirely - the live call now resolves offers by the model calling
check_offer/issue_offer directly, in the same turn, via the Day 5 agent
runtime (see app/channels/voice_runtime.py). One fewer duplicate reasoning
path, and the hardcoded `cart_details = {"amount": 3499.0, "cogs": 1500.0}`
goes with it.

The opening greeting's voice block is generated HERE, before dialing, not
lazily inside voice_runtime.py's /voice/outbound webhook - both so the
greeting is ready the instant Twilio connects, and because ElevenLabs was
diagnosed to fail consistently on this machine specifically during an
active Twilio call window (see app/services/tts.py; Twilio's own Neural
voice is now the reliable primary, so this is now a minor latency win more
than a correctness fix, but there is no reason to give it back).
"""
import asyncio
import logging
from typing import Any, Dict, Optional

from app.gateway.event_bus import bus
from app.services.identity_service import identity_service
from app.services import customer_memory
from app.services import outreach_guards
from app.services.policy_engine import policy_engine
from app.services.voice_dispatch import place_outbound_call, VoiceDispatchError
from app.services.tts import voice_block as tts_voice_block
from app.db.repositories import recovery_attempts as recovery_attempts_repo
from app.db.repositories import customers as customers_repo
from app.db.database import prewarm_pool, run_db_async

logger = logging.getLogger(__name__)

PLAN_WATCHDOG_SECONDS = 6.0


class CallOrchestrator:
    # checkout_id -> Future[plan dict], while waiting for
    # RecoveryStrategist's recovery.plan_ready. Only used for the brief
    # window between opportunity.created and either plan_ready arriving or
    # the watchdog timing out - not a long-lived registry.
    _pending_plans: Dict[str, asyncio.Future] = {}

    @staticmethod
    async def handle_opportunity_created(event: Dict[str, Any]):
        payload = event.get("payload", {})
        checkout_id = payload.get("checkout_id")
        customer_id = payload.get("customer_id")
        merchant_id = event.get("merchant_id")
        correlation_id = event.get("correlation_id")

        if not checkout_id:
            logger.warning("Orchestrator: opportunity event missing checkout_id, dropping.")
            return

        future: asyncio.Future = asyncio.get_event_loop().create_future()
        CallOrchestrator._pending_plans[checkout_id] = future
        degraded = False
        try:
            plan = await asyncio.wait_for(future, timeout=PLAN_WATCHDOG_SECONDS)
        except asyncio.TimeoutError:
            logger.warning(
                f"Orchestrator: recovery.plan_ready didn't arrive within {PLAN_WATCHDOG_SECONDS}s "
                f"for checkout {checkout_id} - proceeding with a generic opening line."
            )
            plan = {"opening_line": "Hi there! I wanted to help you finish your order.", "degraded": True}
            degraded = True
        finally:
            CallOrchestrator._pending_plans.pop(checkout_id, None)

        await CallOrchestrator._start_recovery(
            checkout_id, customer_id, merchant_id, correlation_id, plan, degraded
        )

    @staticmethod
    async def handle_plan_ready(event: Dict[str, Any]):
        """Resolves the pending future from handle_opportunity_created, if
        it's still waiting. A plan that arrives after the watchdog already
        fired is stale - the call already started with the generic
        fallback line, and there is no second call to redirect."""
        payload = event.get("payload", {})
        checkout_id = payload.get("checkout_id")
        future = CallOrchestrator._pending_plans.get(checkout_id)
        if future and not future.done():
            future.set_result(payload.get("plan", {}))
        else:
            logger.info(f"Orchestrator: plan_ready for {checkout_id} arrived after the watchdog window, ignoring.")

    @staticmethod
    async def _start_recovery(
        checkout_id: str,
        customer_id: Optional[str],
        merchant_id: str,
        correlation_id: str,
        plan: Dict[str, Any],
        degraded: bool,
    ):
        recovery_attempt = recovery_attempts_repo.create_recovery_attempt(merchant_id, checkout_id, customer_id)
        recovery_attempt_id = recovery_attempt["recovery_attempt_id"]
        recovery_attempts_repo.update_state(recovery_attempt_id, "CREATED", plan=_to_json(plan))

        logger.info(f"Orchestrator creating RecoveryAttempt: {recovery_attempt_id} (State: CREATED)")

        # Final Consent Gate (Before Outreach)
        consent_granted = customer_id and await identity_service.check_consent(merchant_id, customer_id, channel="voice")
        if not consent_granted:
            logger.warning(f"Orchestrator halting {recovery_attempt_id}: CONSENT_REVOKED")
            recovery_attempts_repo.update_state(recovery_attempt_id, "CONSENT_REVOKED")
            return

        # Hard stops that must hold before we dial: already paid, quiet
        # hours, call cap. Any outreach despite one of these is a RULE BREAK
        # and is counted as such on the dashboard.
        allowed, stop_reason = outreach_guards.check_all(merchant_id, checkout_id)
        if not allowed:
            code = outreach_guards.stop_code(stop_reason)
            logger.warning(f"Orchestrator halting {recovery_attempt_id}: STOP_{code.upper()}")
            # BLOCKED, not CALL_FAILED. A guard stopping us is the system
            # working: nobody was dialled, nothing failed, and a rule the
            # merchant configured held. Recording it as a dial failure was
            # wrong three times over - it told the merchant on the
            # dashboard that these were "real dial failures (no phone on
            # file, carrier issue)", it burned the customer's contact cap
            # for a call that never happened, and it filed a compliance
            # success under the same heading as a broken phone number.
            recovery_attempts_repo.update_state(recovery_attempt_id, "BLOCKED")
            await bus.publish(
                event_type="recovery.blocked",
                payload={
                    "checkout_id": checkout_id,
                    "recovery_attempt_id": recovery_attempt_id,
                    "reason": code,
                    "detail": stop_reason,
                },
                correlation_id=correlation_id,
                merchant_id=merchant_id,
            )
            return

        recovery_attempts_repo.update_state(recovery_attempt_id, "OUTREACH_APPROVED", channel="voice")

        customer = customers_repo.get_customer(customer_id) if customer_id else None
        to_phone = (customer or {}).get("phone", "")

        opening_line = plan.get("opening_line") or "Hi there! I wanted to help you finish your order."
        # voice_block() always returns something playable (ElevenLabs or
        # Twilio's own Neural voice - see tts.py) - no failure branch needed.
        #
        # The memory brief is built here for the same reason the voice block
        # is: everything on this side of the dial has no deadline, and
        # everything after it answers inside Twilio's ~15s webhook window,
        # where an extra DB round trip is not a latency cost but a dropped
        # call. What this customer said on a previous attempt cannot change
        # while the phone is ringing, so there is nothing to gain by asking
        # later.
        memory_brief = ""
        try:
            memory_brief = await run_db_async(
                customer_memory.build_brief, customer_id, recovery_attempt_id
            )
        except Exception as e:
            # Memory is an improvement, never a precondition - a call must
            # not fail because we could not remember the last one.
            logger.warning(f"Could not build the customer memory brief (non-fatal): {e}")
        # Warm the connection pool while the phone is still ringing.
        #
        # Same argument as the voice block and the memory brief above: this
        # side of the dial has no deadline, and the other side answers inside
        # Twilio's ~15s. A call's first turns issue several queries at once,
        # and a cold connection is the single most expensive thing in this
        # system - so pay for them here, where paying costs nothing.
        await prewarm_pool()

        # Whether this merchant can actually offer instalments. Resolved
        # here, on the deadline-free side of the dial, for the same reason
        # the voice block and the memory brief are: it cannot change while
        # the phone is ringing, so asking for it mid-call would buy nothing
        # and cost a round trip inside Twilio's window.
        emi_available = False
        try:
            emi_available = bool(
                (await run_db_async(policy_engine.get_policy, merchant_id) or {}).get("emi_available")
            )
        except Exception as e:
            # False is the safe direction: the agent stays quiet about EMI
            # rather than offering instalments that may not exist.
            logger.warning(f"Could not read the EMI policy (non-fatal, assuming unavailable): {e}")

        plan = {
            **plan,
            "voice_block": await tts_voice_block(opening_line),
            "memory_brief": memory_brief,
            "emi_available": emi_available,
        }
        recovery_attempts_repo.update_state(recovery_attempt_id, "OUTREACH_APPROVED", plan=_to_json(plan))

        try:
            # NOTE: record=True is NOT enabled here - Twilio trial accounts
            # reject it outright ("trial accounts have limited parameter
            # access"), confirmed live. Once this Twilio account is
            # upgraded, recording belongs behind a merchant_policies
            # setting exposed in the dashboard, not hardcoded True/False.
            call_sid = place_outbound_call(to_phone=to_phone, recovery_attempt_id=recovery_attempt_id, record=False)
        except VoiceDispatchError as e:
            logger.error(f"Orchestrator: could not place call for {recovery_attempt_id}: {e}")
            recovery_attempts_repo.update_state(recovery_attempt_id, "CALL_FAILED")
            await bus.publish(
                event_type="recovery.call_failed",
                payload={"recovery_attempt_id": recovery_attempt_id, "checkout_id": checkout_id, "reason": str(e)},
                correlation_id=correlation_id,
                merchant_id=merchant_id,
            )
            return

        recovery_attempts_repo.update_state(recovery_attempt_id, "CALLING")
        await bus.publish(
            event_type="call.started",
            payload={"recovery_attempt_id": recovery_attempt_id, "customer_id": customer_id, "call_sid": call_sid},
            correlation_id=correlation_id,
            merchant_id=merchant_id,
            idempotency_key=f"dial_{recovery_attempt_id}",
        )


def _to_json(obj: Dict[str, Any]) -> str:
    import json

    return json.dumps(obj)


# Register handlers. Note: this no longer subscribes to `customer.understood`
# (formerly published by app/services/customer_intelligence.py, deleted -
# it had become fully orphaned dead code, unreachable from any real path,
# with no test coverage beyond its own). That event's
# next_action=="request_offer"/"opt_out" routing was the old, separate
# reasoning path this orchestrator used to act on. The live call now
# resolves both (via check_offer/issue_offer and record_opt_out) inline,
# in the same agent turn - see app/channels/voice_runtime.py.
bus.subscribe("recovery.opportunity.created", CallOrchestrator.handle_opportunity_created)
bus.subscribe("recovery.plan_ready", CallOrchestrator.handle_plan_ready)
