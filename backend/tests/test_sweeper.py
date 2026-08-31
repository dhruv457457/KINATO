"""
Tests app/gateway/sweeper.py - the durable, DB-backed replacement for the old
per-checkout asyncio.sleep(window) task (which lost every pending timer on a
restart). sweep_once() is called directly rather than waiting on the real
30s loop, matching the ability to test the old task-based code without a
real timer.
"""
from app.db.repositories import checkouts as checkouts_repo
from app.db.repositories import policies as policies_repo
from app.gateway.event_bus import bus
from app.gateway.sweeper import sweep_once


def _events(event_type: str):
    return [e for e in bus.get_recent_events(500) if e["event_type"] == event_type]


async def test_sweep_marks_stale_checkout_abandoned(real_merchant_id, unique_checkout_id):
    # A window of 0 seconds means "abandoned the instant it's older than now" -
    # avoids a real sleep in the test while still exercising the actual
    # dialect-aware interval query, not a shortcut around it.
    policies_repo.update_policy(real_merchant_id, {"abandonment_window_seconds": 0})
    checkouts_repo.create_checkout(
        real_merchant_id, amount_paise=349900, checkout_id=unique_checkout_id, customer_id="cust_1"
    )

    fired = await sweep_once()
    assert fired >= 1

    checkout = checkouts_repo.get_checkout(unique_checkout_id)
    assert checkout["status"] == "abandoned"
    assert any(e["payload"]["checkout_id"] == unique_checkout_id for e in _events("checkout.abandoned"))


async def test_sweep_never_touches_a_paid_checkout(real_merchant_id, unique_checkout_id):
    policies_repo.update_policy(real_merchant_id, {"abandonment_window_seconds": 0})
    checkouts_repo.create_checkout(
        real_merchant_id, amount_paise=349900, checkout_id=unique_checkout_id, customer_id="cust_1"
    )
    checkouts_repo.mark_paid(unique_checkout_id, rzp_payment_id="pay_test_1")

    await sweep_once()

    checkout = checkouts_repo.get_checkout(unique_checkout_id)
    assert checkout["status"] == "paid", "sweeper must never override an already-paid checkout"
    assert not any(e["payload"]["checkout_id"] == unique_checkout_id for e in _events("checkout.abandoned"))


async def test_sweep_leaves_a_fresh_checkout_alone(real_merchant_id, unique_checkout_id):
    # Real production default (1800s) - this checkout was just created, so a
    # correct sweep must not touch it yet.
    checkouts_repo.create_checkout(
        real_merchant_id, amount_paise=349900, checkout_id=unique_checkout_id, customer_id="cust_1"
    )

    await sweep_once()

    checkout = checkouts_repo.get_checkout(unique_checkout_id)
    assert checkout["status"] == "started"


async def test_sweep_is_idempotent_across_repeated_passes(real_merchant_id, unique_checkout_id):
    """A second sweep pass over an already-abandoned checkout must not
    re-publish checkout.abandoned (the idempotency_key + status='started'
    WHERE clause both guard this)."""
    policies_repo.update_policy(real_merchant_id, {"abandonment_window_seconds": 0})
    checkouts_repo.create_checkout(
        real_merchant_id, amount_paise=349900, checkout_id=unique_checkout_id, customer_id="cust_1"
    )

    def _fired_for_this_checkout() -> int:
        return len(
            [e for e in _events("checkout.abandoned")
             if e["payload"]["checkout_id"] == unique_checkout_id]
        )

    await sweep_once()
    first_count = _fired_for_this_checkout()
    await sweep_once()
    second_count = _fired_for_this_checkout()

    # Scoped to THIS checkout, like every other test in this file.
    #
    # It used to count every checkout.abandoned event in the bus log, which
    # made it an assertion about how much the sweeper swept globally rather
    # than about idempotency. sweep_once() sweeps every merchant, and these
    # tests run against a shared database - so one unrelated stale row left
    # by anything else was enough to fail a test about re-firing. It did,
    # once, in a full-suite run and never in isolation.
    assert first_count == 1
    assert second_count == first_count, "a second sweep pass must not re-fire abandonment for the same checkout"
