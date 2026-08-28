"""
Two real correctness gaps found and fixed together, both bearing directly on
the buildathon brief's "compliant escalation, stopping rules, and an audit
trail" bar:

1. Durable webhook idempotency (app/gateway/event_bus.py): the bus used to
   dedup only against an in-memory set() that forgets every key on a
   restart. A Railway redeploy landing between two Razorpay webhook retries
   could otherwise double-fire payment.succeeded and double-count the exact
   revenue number the batch recovery report claims to measure. Now dedup is
   claimed atomically against the `events` table's UNIQUE idempotency_key
   constraint before any subscriber runs.

2. A real stopping rule (app/services/recovery_eligibility.py): a
   payment.downtime.started webhook used to publish `rail.degraded` to no
   subscriber at all - the "suppresses outreach while degraded" claim in
   the webhook's own comment was false. Now it's a durable per-merchant flag
   (merchants.rail_degraded_at) checked before generating any new recovery
   opportunity.
"""
import uuid
import pytest

from app.gateway.event_bus import bus
from app.db.repositories import merchants as merchants_repo
from app.db.repositories import customers as customers_repo
from app.db.repositories import consents as consents_repo
from tests.conftest import wait_until


def _events(event_type: str):
    return [e for e in bus.get_recent_events(500) if e["event_type"] == event_type]


async def test_durable_idempotency_survives_in_memory_reset(real_merchant_id):
    """Simulates a process restart between two deliveries of the same
    idempotency_key by clearing the in-memory set mid-test - the durable
    (DB-backed) check must still catch the duplicate."""
    bus.persist = True
    try:
        key = f"webhook_test_{uuid.uuid4().hex[:10]}"

        await bus.publish(
            event_type="payment.succeeded",
            payload={"amount": 100},
            correlation_id="corr_1",
            merchant_id=real_merchant_id,
            idempotency_key=key,
        )
        first_count = len(_events("payment.succeeded"))
        assert first_count >= 1

        # Simulate a restart: the in-memory dedup set is gone, but the
        # durable claim in the `events` table is not.
        bus._processed_idempotency_keys.clear()

        await bus.publish(
            event_type="payment.succeeded",
            payload={"amount": 100},
            correlation_id="corr_1",
            merchant_id=real_merchant_id,
            idempotency_key=key,
        )
        second_count = len(_events("payment.succeeded"))
        assert second_count == first_count, "duplicate webhook delivery was processed twice after a simulated restart"
    finally:
        bus.persist = False


async def test_rail_degraded_blocks_new_recovery_opportunity(real_merchant_id):
    from app.services.recovery_eligibility import RecoveryEligibilityService

    customer = customers_repo.upsert_by_contact(real_merchant_id, phone="+919999999998", name="Rail Test Customer")
    consents_repo.record_consent(real_merchant_id, customer["customer_id"], channel="voice", status="granted", source="checkout_optin")

    merchants_repo.set_rail_degraded(real_merchant_id, True)
    try:
        assert merchants_repo.is_rail_degraded(real_merchant_id)

        await RecoveryEligibilityService.evaluate_abandonment({
            "payload": {"checkout_id": f"chk_rail_{uuid.uuid4().hex[:8]}", "customer_id": customer["customer_id"], "amount": 1000.0, "currency": "INR"},
            "merchant_id": real_merchant_id,
            "correlation_id": "corr_rail",
        })

        assert not any(
            e["payload"].get("customer_id") == customer["customer_id"]
            for e in _events("recovery.opportunity.created")
        ), "a recovery opportunity was created while the rail was marked degraded"
        assert any(
            e["payload"].get("reason") == "rail_degraded" for e in _events("recovery.blocked")
        )
    finally:
        merchants_repo.set_rail_degraded(real_merchant_id, False)
        assert not merchants_repo.is_rail_degraded(real_merchant_id)


async def test_blocked_reasons_are_aggregated_for_the_dashboard(real_merchant_id):
    """`recovery.blocked` events are what tell a merchant WHY money on the
    table was never chased. They're published (webhooks.py for no_contact,
    recovery_eligibility.py for rail_degraded) but were previously written
    to the events table and never read back by anything."""
    from app.db.repositories import events as events_repo

    bus.persist = True
    try:
        for reason in ("no_contact", "no_contact", "rail_degraded"):
            await bus.publish(
                event_type="recovery.blocked",
                payload={"checkout_id": f"chk_{uuid.uuid4().hex[:8]}", "reason": reason},
                correlation_id="corr_blocked",
                merchant_id=real_merchant_id,
            )
        # publish() persists fire-and-forget for events carrying no
        # idempotency key, so poll until those writes land rather than
        # sleeping a fixed amount - against a real remote Postgres the
        # round trips are slow enough that any fixed sleep is a flake
        # waiting to happen (a 1.5s version of this failed intermittently
        # on the third write).
        landed = await wait_until(
            lambda: events_repo.count_blocked_reasons(real_merchant_id).get("rail_degraded") == 1,
            timeout=15.0,
        )
        counts = events_repo.count_blocked_reasons(real_merchant_id)
        assert landed, f"blocked events never persisted: {counts}"
        assert counts.get("no_contact") == 2, counts
        assert counts.get("rail_degraded") == 1, counts
    finally:
        bus.persist = False


async def test_summary_stats_returns_real_ints_not_decimals(real_merchant_id):
    """Postgres SUM()/COUNT() hand back Decimal, which FastAPI serializes as
    a JSON *string* - so the dashboard's declared `number` contract was a
    lie that only worked because JS coerces "10576500" / 100. Money is
    integer paise here; the repo must return ints."""
    from decimal import Decimal
    from app.db.repositories import recovery_attempts as ra_repo

    stats = ra_repo.summary_stats(real_merchant_id)
    for key in ("recovered_paise", "revenue_at_risk_paise", "total_attempts", "recovered_count", "abandoned_count"):
        assert not isinstance(stats[key], Decimal), f"{key} leaked a Decimal to the API boundary"
        assert isinstance(stats[key], int), f"{key} is {type(stats[key]).__name__}, expected int"
