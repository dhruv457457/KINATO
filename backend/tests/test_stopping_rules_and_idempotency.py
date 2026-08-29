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
from datetime import datetime

import pytest

from app.services import outreach_guards

from app.gateway.event_bus import bus
from app.db.repositories import merchants as merchants_repo
from app.db.repositories import customers as customers_repo
from app.db.repositories import consents as consents_repo
from app.db.repositories import checkouts as checkouts_repo
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


async def test_stuck_calls_are_expired_and_stop_blocking_retries(real_merchant_id, unique_checkout_id):
    """A call can end with no completion signal ever reaching us: Twilio gives
    up waiting for TwiML and hangs up, the customer drops, the network dies
    between turns. Nothing then moves the attempt out of CALLING.

    That matters far more than a stale dashboard number: an in-flight attempt
    BLOCKS new recovery for that checkout, so one dropped call meant the cart
    could never be recovered again. Seven such rows accumulated during live
    testing, the oldest stuck for over an hour.
    """
    from app.db.database import get_db
    from app.db.repositories import recovery_attempts as ra_repo

    checkouts_repo.create_checkout(
        real_merchant_id, amount_paise=299000, checkout_id=unique_checkout_id, source="test"
    )
    attempt = ra_repo.create_recovery_attempt(
        merchant_id=real_merchant_id, checkout_id=unique_checkout_id, customer_id=None
    )
    ra_repo.update_state(attempt["recovery_attempt_id"], "CALLING")

    # While it looks in-flight, the checkout is correctly protected from a
    # second concurrent attempt.
    assert len(ra_repo.list_active_for_checkout(unique_checkout_id)) == 1

    # Backdate it past the staleness threshold, as a dropped call would be.
    with get_db() as conn:
        conn.cursor().execute(
            "UPDATE recovery_attempts SET updated_at = NOW() - INTERVAL '30 minutes' "
            "WHERE recovery_attempt_id = %s",
            (attempt["recovery_attempt_id"],),
        )

    closed_ids = {a["recovery_attempt_id"] for a in ra_repo.expire_stale_calls(older_than_minutes=10)}
    assert attempt["recovery_attempt_id"] in closed_ids

    reloaded = ra_repo.get_recovery_attempt(attempt["recovery_attempt_id"])
    assert reloaded["state"] == "CALL_FAILED"
    assert ra_repo.list_active_for_checkout(unique_checkout_id) == [], (
        "a dead call must stop blocking recovery for its checkout"
    )


def test_terminal_states_match_what_the_code_actually_writes():
    """The terminal list once read ('RECOVERED','FAILED','OPTED_OUT') while
    the code writes CALL_FAILED and CONSENT_REVOKED - so even a correctly
    failed call counted as in-flight and blocked its checkout forever."""
    from app.db.repositories.recovery_attempts import TERMINAL_STATES

    assert "CALL_FAILED" in TERMINAL_STATES
    assert "CONSENT_REVOKED" in TERMINAL_STATES
    assert "RECOVERED" in TERMINAL_STATES
    for phantom in ("FAILED", "OPTED_OUT"):
        assert phantom not in TERMINAL_STATES, f"{phantom} is never written by any code path"


class TestAlreadyPaidAtTheMoneyGate:
    """"Already paid" was checked before dialling and once per conversation
    turn, but never inside check_offer - so the one place that actually
    mints spendable offers was the one place that never asked.

    On a live voice call the per-turn check closes that in practice. It
    does nothing for any other caller of the agent, and "whoever calls this
    happens to check first" is not a property of the tool.
    """

    async def test_check_offer_refuses_on_a_paid_checkout(
        self, connected_merchant_id, unique_checkout_id
    ):
        from app.agents import tools as tools_module
        from app.agents.state import AgentContext
        from app.db.repositories import checkouts as checkouts_repo

        checkouts_repo.create_checkout(
            merchant_id=connected_merchant_id,
            amount_paise=249_900,
            cogs_paise=100_000,
            checkout_id=unique_checkout_id,
            line_items=[{"product_id": "sku_1", "name": "Woven Table Runner"}],
        )
        ctx = AgentContext(
            merchant_id=connected_merchant_id,
            correlation_id="test",
            checkout_id=unique_checkout_id,
        )

        # Unpaid: a normal offer.
        assert (await tools_module._check_offer(ctx, 5, "price"))["decision"] in ("ALLOW", "MODIFY")

        # Paid mid-conversation, exactly as a customer paying on another
        # device would look.
        checkouts_repo.mark_paid(unique_checkout_id, rzp_payment_id="pay_test_already")

        result = await tools_module._check_offer(ctx, 5, "price")
        assert result["decision"] == "DENY"
        assert result["reason"] == "REJECTED_ALREADY_PAID"

    async def test_full_price_is_refused_on_a_paid_checkout_too(
        self, connected_merchant_id, unique_checkout_id
    ):
        """Full price is exempt from the discount gates, deliberately - but
        not from this one. Sending anyone a link for something they have
        already paid for is the single most damaging thing this agent could
        do, and it does not become less damaging at 0% off."""
        from app.agents import tools as tools_module
        from app.agents.state import AgentContext
        from app.db.repositories import checkouts as checkouts_repo

        checkouts_repo.create_checkout(
            merchant_id=connected_merchant_id,
            amount_paise=99_900,
            cogs_paise=40_000,
            checkout_id=unique_checkout_id,
        )
        checkouts_repo.mark_paid(unique_checkout_id, rzp_payment_id="pay_test_already_2")

        ctx = AgentContext(
            merchant_id=connected_merchant_id,
            correlation_id="test",
            checkout_id=unique_checkout_id,
        )
        result = await tools_module._check_offer(ctx, 0, "ready to buy")
        assert result["decision"] == "DENY"
        assert result["reason"] == "REJECTED_ALREADY_PAID"


class TestAnOutageDefersRecoveryRatherThanDestroyingIt:
    """The rail check published `recovery.blocked` and returned - and
    nothing anywhere ever re-fired the case. The sweeper only picks up
    checkouts still in `started`, and a payment.failed case was never in
    that status, so every customer whose payment failed during a Razorpay
    outage was lost permanently. For a reason that was our problem, not
    theirs.
    """

    async def test_a_case_held_during_an_outage_is_queued_not_dropped(
        self, real_merchant_id
    ):
        from app.db.repositories import checkouts as checkouts_repo
        from app.db.repositories import customers as customers_repo
        from app.db.repositories.merchants import set_rail_degraded
        from app.services.identity_service import identity_service
        from tests.conftest import wait_until

        customer = customers_repo.upsert_by_contact(
            real_merchant_id, email="outage@example.com", phone="+919000000099"
        )
        await identity_service.grant_transactional_consent(
            real_merchant_id, customer["customer_id"],
            email="outage@example.com", phone="+919000000099",
        )
        checkout = checkouts_repo.create_checkout(
            real_merchant_id, amount_paise=200_000, customer_id=customer["customer_id"]
        )
        cid = checkout["checkout_id"]

        set_rail_degraded(real_merchant_id, True)
        try:
            await bus.publish(
                event_type="checkout.payment_failed",
                payload={"checkout_id": cid, "customer_id": customer["customer_id"], "amount": 2000.0},
                correlation_id=cid,
                merchant_id=real_merchant_id,
            )
            await wait_until(
                lambda: any(
                    e["event_type"] == "recovery.blocked"
                    and e["payload"].get("reason") == "rail_degraded"
                    for e in bus._event_log
                )
            )
            assert checkouts_repo.get_checkout(cid)["recovery_queued_at"] is not None, (
                "a case held for an outage must be recorded so it can be picked up again"
            )
        finally:
            set_rail_degraded(real_merchant_id, False)

    async def test_the_queue_drains_when_the_outage_clears(self, real_merchant_id):
        """And it drains back through the FULL eligibility gate, so nothing
        is grandfathered past a stop just because it was queued."""
        from app.db.repositories import checkouts as checkouts_repo
        from app.db.repositories import customers as customers_repo
        from tests.conftest import wait_until

        customer = customers_repo.upsert_by_contact(
            real_merchant_id, email="drain@example.com", phone="+919000000098"
        )
        checkout = checkouts_repo.create_checkout(
            real_merchant_id, amount_paise=300_000, customer_id=customer["customer_id"]
        )
        cid = checkout["checkout_id"]
        checkouts_repo.queue_for_rail_recovery(cid)

        await bus.publish(
            event_type="rail.degraded",
            payload={"status": "resolved"},
            correlation_id=real_merchant_id,
            merchant_id=real_merchant_id,
        )

        requeued = await wait_until(
            lambda: any(
                e["event_type"] == "checkout.payment_failed"
                and e["payload"].get("checkout_id") == cid
                and e["payload"].get("requeued_after_outage")
                for e in bus._event_log
            )
        )
        assert requeued, "cases held for an outage must be reconsidered once it clears"
        assert checkouts_repo.get_checkout(cid)["recovery_queued_at"] is None

    async def test_a_case_paid_during_the_outage_is_not_chased_afterwards(
        self, real_merchant_id
    ):
        from app.db.repositories import checkouts as checkouts_repo

        checkout = checkouts_repo.create_checkout(real_merchant_id, amount_paise=300_000)
        cid = checkout["checkout_id"]
        checkouts_repo.queue_for_rail_recovery(cid)
        checkouts_repo.mark_paid(cid, rzp_payment_id="pay_during_outage")

        queued = checkouts_repo.list_queued_for_rail(real_merchant_id)
        assert cid not in [c["checkout_id"] for c in queued], (
            "someone who paid while we were holding back must not be called about it"
        )


class TestQuietHoursHoldsInsteadOfChurning:
    """From the deployed logs: all night, once per sweeper pass, the
    pipeline built a recovery attempt, spent a real LLM call generating an
    opening line, blocked it on quiet hours before dialling, marked it
    terminal, became eligible again, and started over. Every guard was
    correct. The behaviour was still wrong.
    """

    @pytest.mark.real_clock
    async def test_a_cart_is_held_rather_than_attempted_outside_calling_hours(
        self, real_merchant_id, monkeypatch
    ):
        from app.db.repositories import checkouts as checkouts_repo
        from app.db.repositories import customers as customers_repo
        from app.db.repositories import policies as policies_repo
        from app.services.identity_service import identity_service
        from tests.conftest import wait_until

        # A window that is definitely shut, whatever time the suite runs.
        now = datetime.now(outreach_guards.IST)
        shut = (now.hour + 2) % 24
        policies_repo.update_policy(
            real_merchant_id, {"calling_start_hour": shut, "calling_end_hour": (shut + 1) % 24}
        )

        customer = customers_repo.upsert_by_contact(real_merchant_id, phone="+919000000200")
        await identity_service.grant_transactional_consent(
            real_merchant_id, customer["customer_id"], phone="+919000000200"
        )
        checkout = checkouts_repo.create_checkout(
            real_merchant_id, amount_paise=250_000, customer_id=customer["customer_id"]
        )
        cid = checkout["checkout_id"]

        await bus.publish(
            event_type="checkout.payment_failed",
            payload={"checkout_id": cid, "customer_id": customer["customer_id"], "amount": 2500.0},
            correlation_id=cid,
            merchant_id=real_merchant_id,
        )

        held = await wait_until(
            lambda: any(
                e["event_type"] == "recovery.blocked"
                and e["payload"].get("reason") == "quiet_hours"
                for e in bus._event_log
            )
        )
        assert held, "a cart outside the calling window must be held, with a visible reason"
        assert checkouts_repo.get_checkout(cid)["recovery_queued_at"] is not None

        # And crucially: no attempt, so no LLM call and no row per pass.
        assert not any(
            e["event_type"] == "recovery.opportunity.created" for e in bus._event_log
        ), "holding means not starting the work, not starting it and then stopping it"

    @pytest.mark.real_clock
    async def test_the_sweeper_releases_it_once_the_window_opens(
        self, real_merchant_id
    ):
        from app.db.repositories import checkouts as checkouts_repo
        from app.db.repositories import policies as policies_repo
        from app.gateway import sweeper
        from tests.conftest import wait_until

        # Window wide open.
        policies_repo.update_policy(
            real_merchant_id, {"calling_start_hour": 0, "calling_end_hour": 23}
        )
        checkout = checkouts_repo.create_checkout(real_merchant_id, amount_paise=300_000)
        cid = checkout["checkout_id"]
        checkouts_repo.queue_for_rail_recovery(cid)

        await sweeper._drain_recovery_queue()

        released = await wait_until(
            lambda: any(
                e["event_type"] == "checkout.payment_failed"
                and e["payload"].get("checkout_id") == cid
                and e["payload"].get("released_from_queue")
                for e in bus._event_log
            )
        )
        assert released, "a held cart must be reconsidered once the window opens"
        assert checkouts_repo.get_checkout(cid)["recovery_queued_at"] is None

    @pytest.mark.real_clock
    async def test_it_stays_held_while_the_window_is_still_shut(self, real_merchant_id):
        from app.db.repositories import checkouts as checkouts_repo
        from app.db.repositories import policies as policies_repo
        from app.gateway import sweeper

        now = datetime.now(outreach_guards.IST)
        shut = (now.hour + 2) % 24
        policies_repo.update_policy(
            real_merchant_id, {"calling_start_hour": shut, "calling_end_hour": (shut + 1) % 24}
        )
        checkout = checkouts_repo.create_checkout(real_merchant_id, amount_paise=300_000)
        cid = checkout["checkout_id"]
        checkouts_repo.queue_for_rail_recovery(cid)

        await sweeper._drain_recovery_queue()

        assert checkouts_repo.get_checkout(cid)["recovery_queued_at"] is not None, (
            "draining early would defeat the purpose of holding it"
        )
