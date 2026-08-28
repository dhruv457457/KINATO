"""
Hard stops that must hold before Kinato contacts anyone.

Two of these were advertised but never enforced. merchant_policies has had
calling_start_hour/calling_end_hour since the schema was written, they are
editable on the Policies screen, and NO service read them - a merchant who
set 10:00-20:00 got no protection whatsoever. There was likewise no cap on
how many times one checkout could be dialled.

A breach of any of these is a "rule break", the one dashboard number that
must read zero.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.services import outreach_guards
from app.db.repositories import policies as policies_repo
from app.db.repositories import checkouts as checkouts_repo
from app.db.repositories import recovery_attempts as ra_repo

IST = outreach_guards.IST


@pytest.mark.real_clock
class TestQuietHours:
    def test_call_inside_calling_hours_is_allowed(self, real_merchant_id):
        policies_repo.update_policy(real_merchant_id, {"calling_start_hour": 10, "calling_end_hour": 20})
        noon = datetime(2026, 8, 28, 12, 0, tzinfo=IST)
        ok, reason = outreach_guards.within_calling_hours(real_merchant_id, now=noon)
        assert ok, reason

    def test_call_before_start_hour_is_blocked(self, real_merchant_id):
        policies_repo.update_policy(real_merchant_id, {"calling_start_hour": 10, "calling_end_hour": 20})
        early = datetime(2026, 8, 28, 6, 30, tzinfo=IST)
        ok, reason = outreach_guards.within_calling_hours(real_merchant_id, now=early)
        assert not ok
        assert "quiet_hours" in reason

    def test_call_after_end_hour_is_blocked(self, real_merchant_id):
        policies_repo.update_policy(real_merchant_id, {"calling_start_hour": 10, "calling_end_hour": 20})
        late = datetime(2026, 8, 28, 23, 15, tzinfo=IST)
        ok, reason = outreach_guards.within_calling_hours(real_merchant_id, now=late)
        assert not ok
        assert "quiet_hours" in reason

    def test_hours_are_evaluated_in_ist_not_server_time(self, real_merchant_id):
        """A container in Amsterdam at 22:30 UTC is 04:00 IST - the middle of
        the night for the customer. Evaluating in server time would call them."""
        policies_repo.update_policy(real_merchant_id, {"calling_start_hour": 10, "calling_end_hour": 20})
        utc_2230 = datetime(2026, 8, 28, 22, 30, tzinfo=timezone.utc)  # 04:00 IST next day
        ok, _ = outreach_guards.within_calling_hours(real_merchant_id, now=utc_2230)
        assert not ok, "quiet hours must be judged in the customer's timezone, not the server's"


class TestCallCap:
    def test_under_cap_is_allowed(self, real_merchant_id, unique_checkout_id):
        checkouts_repo.create_checkout(real_merchant_id, amount_paise=100_000, checkout_id=unique_checkout_id)
        ok, _ = outreach_guards.under_call_cap(unique_checkout_id, max_calls=2)
        assert ok

    def test_cap_blocks_the_third_call(self, real_merchant_id, unique_checkout_id):
        checkouts_repo.create_checkout(real_merchant_id, amount_paise=100_000, checkout_id=unique_checkout_id)
        for _ in range(2):
            a = ra_repo.create_recovery_attempt(real_merchant_id, unique_checkout_id, None)
            ra_repo.update_state(a["recovery_attempt_id"], "CALL_FAILED")

        ok, reason = outreach_guards.under_call_cap(unique_checkout_id, max_calls=2)
        assert not ok
        assert "max_calls_reached" in reason

    def test_attempts_stopped_before_dialling_do_not_burn_the_budget(self, real_merchant_id, unique_checkout_id):
        """An attempt halted at CREATED (consent revoked, a guard fired) never
        rang the customer's phone, so it must not count against their cap."""
        checkouts_repo.create_checkout(real_merchant_id, amount_paise=100_000, checkout_id=unique_checkout_id)
        for _ in range(3):
            a = ra_repo.create_recovery_attempt(real_merchant_id, unique_checkout_id, None)
            ra_repo.update_state(a["recovery_attempt_id"], "CONSENT_REVOKED")

        ok, _ = outreach_guards.under_call_cap(unique_checkout_id, max_calls=2)
        assert ok, "attempts that never dialled must not consume the call cap"


class TestAlreadyPaid:
    def test_unpaid_checkout_is_allowed(self, real_merchant_id, unique_checkout_id):
        checkouts_repo.create_checkout(real_merchant_id, amount_paise=100_000, checkout_id=unique_checkout_id)
        ok, _ = outreach_guards.not_already_paid(unique_checkout_id)
        assert ok

    def test_paid_checkout_is_blocked(self, real_merchant_id, unique_checkout_id):
        checkouts_repo.create_checkout(real_merchant_id, amount_paise=100_000, checkout_id=unique_checkout_id)
        checkouts_repo.mark_paid(unique_checkout_id)
        ok, reason = outreach_guards.not_already_paid(unique_checkout_id)
        assert not ok
        assert reason == "already_paid"


