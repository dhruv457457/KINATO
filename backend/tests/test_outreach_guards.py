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


class TestOutreachCap:
    def test_under_cap_is_allowed(self, real_merchant_id, unique_checkout_id):
        checkouts_repo.create_checkout(real_merchant_id, amount_paise=100_000, checkout_id=unique_checkout_id)
        ok, _ = outreach_guards.under_outreach_cap(unique_checkout_id, max_total=2)
        assert ok

    def test_cap_blocks_the_third_call(self, real_merchant_id, unique_checkout_id):
        checkouts_repo.create_checkout(real_merchant_id, amount_paise=100_000, checkout_id=unique_checkout_id)
        for _ in range(2):
            a = ra_repo.create_recovery_attempt(real_merchant_id, unique_checkout_id, None)
            ra_repo.update_state(a["recovery_attempt_id"], "CALL_FAILED")

        ok, reason = outreach_guards.under_outreach_cap(unique_checkout_id, max_total=2)
        assert not ok
        assert "max_calls_reached" in reason

    def test_attempts_stopped_before_dialling_do_not_burn_the_budget(self, real_merchant_id, unique_checkout_id):
        """An attempt halted at CREATED (consent revoked, a guard fired) never
        rang the customer's phone, so it must not count against their cap."""
        checkouts_repo.create_checkout(real_merchant_id, amount_paise=100_000, checkout_id=unique_checkout_id)
        for _ in range(3):
            a = ra_repo.create_recovery_attempt(real_merchant_id, unique_checkout_id, None)
            ra_repo.update_state(a["recovery_attempt_id"], "CONSENT_REVOKED")

        ok, _ = outreach_guards.under_outreach_cap(unique_checkout_id, max_total=2)
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


class TestOutreachCapCountsEveryChannel:
    """under_call_cap counted CALLS. That was right while voice was the
    only way anyone was ever contacted, and would have quietly allowed two
    calls plus unlimited email the moment email became real - while
    continuing to report that the cap was holding."""

    def _attempt(self, merchant_id, checkout_id, channel):
        a = ra_repo.create_recovery_attempt(merchant_id, checkout_id, None)
        ra_repo.update_state(a["recovery_attempt_id"], "CALL_FAILED", channel=channel)
        return a["recovery_attempt_id"]

    def test_email_counts_towards_the_same_total_as_voice(
        self, real_merchant_id, unique_checkout_id
    ):
        checkouts_repo.create_checkout(real_merchant_id, amount_paise=100_000, checkout_id=unique_checkout_id)
        self._attempt(real_merchant_id, unique_checkout_id, "voice")
        self._attempt(real_merchant_id, unique_checkout_id, "email")

        ok, reason = outreach_guards.under_outreach_cap(unique_checkout_id, channel="voice")
        assert not ok
        assert "max_calls_reached" in reason

    def test_one_per_channel_per_day_even_below_the_total_cap(
        self, real_merchant_id, unique_checkout_id
    ):
        """A lifetime cap alone permits both attempts inside ten minutes.
        The customer experiences the day, not the lifetime."""
        checkouts_repo.create_checkout(real_merchant_id, amount_paise=100_000, checkout_id=unique_checkout_id)
        self._attempt(real_merchant_id, unique_checkout_id, "voice")

        # Total is 1 of 2, so the lifetime cap is not the thing stopping us.
        blocked, reason = outreach_guards.under_outreach_cap(unique_checkout_id, channel="voice")
        assert not blocked
        assert "channel_cap_today" in reason

        # ...but a different channel is still open today.
        ok, _ = outreach_guards.under_outreach_cap(unique_checkout_id, channel="email")
        assert ok

    def test_a_requested_callback_earns_exactly_one_more_attempt(
        self, real_merchant_id, unique_checkout_id
    ):
        """They asked. That is the one thing that lifts the cap - and it
        lifts it by one, not into an exemption."""
        checkouts_repo.create_checkout(real_merchant_id, amount_paise=100_000, checkout_id=unique_checkout_id)
        self._attempt(real_merchant_id, unique_checkout_id, "voice")
        self._attempt(real_merchant_id, unique_checkout_id, "email")
        assert not outreach_guards.under_outreach_cap(unique_checkout_id, channel="voice")[0]

        rid = self._attempt(real_merchant_id, unique_checkout_id, "voice")
        ra_repo.update_state(rid, "CALLBACK_REQUESTED", callback_requested_at=datetime.now(timezone.utc))
        # Now at 3 attempts against a lifted cap of 3 - still stopped, and
        # that is correct: the extra attempt was the one just recorded.
        ok, reason = outreach_guards.under_outreach_cap(unique_checkout_id, channel="email")
        assert not ok and "max_calls_reached" in reason

    def test_the_callback_lift_is_real(self, real_merchant_id, unique_checkout_id):
        # Both prior attempts on voice, so the TOTAL cap is what stops us
        # and email is still untouched today - otherwise the per-day cap
        # blocks first and the lift is untestable.
        checkouts_repo.create_checkout(real_merchant_id, amount_paise=100_000, checkout_id=unique_checkout_id)
        a1 = self._attempt(real_merchant_id, unique_checkout_id, "voice")
        self._attempt(real_merchant_id, unique_checkout_id, "voice")
        blocked, reason = outreach_guards.under_outreach_cap(unique_checkout_id, channel="email")
        assert not blocked and "max_calls_reached" in reason

        ra_repo.update_state(a1, "CALLBACK_REQUESTED", callback_requested_at=datetime.now(timezone.utc))
        ok, _ = outreach_guards.under_outreach_cap(unique_checkout_id, channel="email")
        assert ok, "a customer who asked for a callback must get one"


@pytest.mark.real_clock
class TestTheGateIsOneFunctionNotTwoCopies:
    def test_check_all_accepts_an_injected_clock(self, real_merchant_id, unique_checkout_id):
        """The scoreboard used to walk the individual guards in an order it
        wrote out by hand - a second copy of the rule deciding whether a
        customer may be contacted, free to drift from production. It needs
        a fixed clock, so the clock is a parameter rather than a reason to
        fork the logic."""
        checkouts_repo.create_checkout(real_merchant_id, amount_paise=100_000, checkout_id=unique_checkout_id)

        quiet = datetime(2026, 8, 28, 4, 0, tzinfo=outreach_guards.IST)
        open_hours = datetime(2026, 8, 28, 14, 0, tzinfo=outreach_guards.IST)

        ok, reason = outreach_guards.check_all(real_merchant_id, unique_checkout_id, now=quiet)
        assert not ok and outreach_guards.stop_code(reason) == "quiet_hours"

        ok, _ = outreach_guards.check_all(real_merchant_id, unique_checkout_id, now=open_hours)
        assert ok

    def test_stop_code_extracts_the_machine_readable_half(self):
        assert outreach_guards.stop_code("quiet_hours (IST hour 4 outside 10:00-20:00)") == "quiet_hours"
        assert outreach_guards.stop_code("already_paid") == "already_paid"
        assert outreach_guards.stop_code("") == ""
        assert outreach_guards.stop_code(None) == ""

    def test_every_stop_code_a_guard_can_return_is_declared(self):
        """STOP_CODES seeds the report's counters at zero, so a code
        missing from it is a stop nobody would know to look for."""
        assert "channel_cap_today" in outreach_guards.STOP_CODES
        assert "promise_to_pay" in outreach_guards.STOP_CODES


@pytest.mark.real_clock
class TestRoundTheClockIsReachable:
    """A merchant asked how to call 24/7 and the honest answer was that
    they could not. 0-23 silently skipped the 23:00 hour, 24 was rejected
    by validation, and 0-0 - which reads as "midnight to midnight, always"
    - evaluated to `0 <= hour < 0`, meaning never. They would have switched
    calling off entirely while believing they had switched it fully on.
    """

    @pytest.mark.parametrize("start,end", [(0, 24), (0, 0), (10, 10), (23, 23)])
    def test_every_hour_is_allowed(self, real_merchant_id, start, end):
        policies_repo.update_policy(
            real_merchant_id, {"calling_start_hour": start, "calling_end_hour": end}
        )
        for hour in range(24):
            clock = datetime(2026, 8, 28, hour, 30, tzinfo=IST)
            ok, reason = outreach_guards.within_calling_hours(real_merchant_id, now=clock)
            assert ok, f"{start}-{end} refused hour {hour}: {reason}"

    def test_same_value_no_longer_means_never(self, real_merchant_id):
        """The dangerous reading. This is the assertion that would have
        caught it."""
        policies_repo.update_policy(
            real_merchant_id, {"calling_start_hour": 0, "calling_end_hour": 0}
        )
        allowed = [
            h for h in range(24)
            if outreach_guards.within_calling_hours(
                real_merchant_id, now=datetime(2026, 8, 28, h, 30, tzinfo=IST)
            )[0]
        ]
        assert len(allowed) == 24, "0-0 must mean always, never nothing"

    def test_a_real_window_still_refuses_outside_it(self, real_merchant_id):
        """The point of making 24/7 reachable is not to weaken the guard."""
        policies_repo.update_policy(
            real_merchant_id, {"calling_start_hour": 10, "calling_end_hour": 20}
        )
        ok, reason = outreach_guards.within_calling_hours(
            real_merchant_id, now=datetime(2026, 8, 28, 22, 30, tzinfo=IST)
        )
        assert not ok and "quiet_hours" in reason

    def test_an_overnight_window_still_wraps(self, real_merchant_id):
        policies_repo.update_policy(
            real_merchant_id, {"calling_start_hour": 22, "calling_end_hour": 6}
        )
        for hour, expected in ((23, True), (2, True), (12, False)):
            ok, _ = outreach_guards.within_calling_hours(
                real_merchant_id, now=datetime(2026, 8, 28, hour, 30, tzinfo=IST)
            )
            assert ok is expected, f"hour {hour} judged wrongly for a 22-6 window"
