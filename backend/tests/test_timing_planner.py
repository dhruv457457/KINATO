"""When to try again - decided by code, on a clock, with no model involved.

Every recovery so far has had exactly one moment: now. A payment fails, the
sweeper notices, and the customer is contacted whenever that happened to be.
That is fine for a soft decline at 11am and actively wrong for "I don't have
the money until the 1st" on the 28th.

This module picks the moments. Two things make it cheap and safe:

  * It is pure. No database, no network, no LLM. Given the same failure and
    the same clock it returns the same windows forever.
  * It is anchored on `failed_at`, never on `now()`. A plan computed today
    and recomputed next week is the same plan, which is why nothing has to
    be written down - and a plan that is never stored can never go stale.

What it deliberately does NOT do is act. Nothing here contacts anybody. The
windows are a suggestion the agent may offer and the merchant may read; the
only thing that schedules anything is the customer agreeing to a date, which
goes through `record_promise_to_pay` exactly as it did before.
"""
import re
from datetime import datetime, timedelta

import pytest

from app.services.failure_diagnosis import (
    AUTH_DROP,
    HARD_DECLINE,
    INSUFFICIENT_FUNDS,
    RAIL_DOWN,
    SOFT_DECLINE,
    UNKNOWN,
    USER_ABANDON,
)
from app.services.outreach_guards import IST
from app.services.timing_planner import plan_windows


def at(y, m, d, hh=12, mm=0):
    """A moment in IST - the timezone every calling-hours rule is written in."""
    return datetime(y, m, d, hh, mm, tzinfo=IST)


class TestAHardDeclineIsNeverRetried:
    """`retry_same_instrument=False` has been computed since failure_diagnosis
    shipped and read by nothing. This is the thing that finally reads it.

    A stolen-card block or a `do_not_honour` does not become payable by
    waiting. Suggesting a time implies the card might work later; it will
    not, and a retried fraud block is a signal against the merchant.
    """

    def test_no_windows_at_all(self):
        assert plan_windows(HARD_DECLINE, at(2026, 9, 2)) == []

    @pytest.mark.parametrize("day", range(1, 29))
    @pytest.mark.parametrize("hour", [0, 6, 11, 17, 23])
    def test_no_window_on_any_day_at_any_hour(self, day, hour):
        assert plan_windows(HARD_DECLINE, at(2026, 9, day, hour)) == []


class TestWindowsRespectTheMerchantsCallingHours:
    """calling_start_hour/end_hour is the merchant's own setting, already
    enforced for live outreach by outreach_guards.within_calling_hours. A
    planner that suggests 03:00 has invented a time its own policy forbids.
    """

    @pytest.mark.parametrize(
        "failure_class",
        [SOFT_DECLINE, RAIL_DOWN, INSUFFICIENT_FUNDS, AUTH_DROP, USER_ABANDON, UNKNOWN],
    )
    @pytest.mark.parametrize("start,end", [(10, 20), (9, 18), (11, 15), (8, 21)])
    def test_every_window_lands_inside_the_window(self, failure_class, start, end):
        for anchor_hour in (0, 3, 7, 13, 19, 23):
            windows = plan_windows(
                failure_class,
                at(2026, 9, 2, anchor_hour),
                calling_start_hour=start,
                calling_end_hour=end,
            )
            for w in windows:
                hour = w.at.astimezone(IST).hour
                assert start <= hour < end, (
                    f"{failure_class} at {anchor_hour}:00 produced {w.at} "
                    f"which is outside {start}:00-{end}:00"
                )

    def test_round_the_clock_merchants_are_not_clamped_into_nothing(self):
        """start == end means "always", per within_calling_hours. It must not
        collapse to a window that can never be satisfied."""
        windows = plan_windows(SOFT_DECLINE, at(2026, 9, 2, 23), calling_start_hour=0, calling_end_hour=0)
        assert windows, "a round-the-clock merchant got no windows at all"

    def test_an_overnight_window_is_understood(self):
        """22:00-06:00 is a legitimate setting and wraps midnight."""
        windows = plan_windows(USER_ABANDON, at(2026, 9, 2, 12), calling_start_hour=22, calling_end_hour=6)
        assert windows
        for w in windows:
            hour = w.at.astimezone(IST).hour
            assert hour >= 22 or hour < 6, f"{w.at} is outside the overnight window"


class TestTheSameFailurePlansTheSameWay:
    """Anchored on failed_at, not on now(). This is what lets the plan be
    recomputed on demand instead of stored - and a stored plan is a plan that
    can disagree with the row it came from."""

    def test_calling_it_twice_gives_the_same_answer(self):
        failed = at(2026, 9, 2, 14, 30)
        first = plan_windows(INSUFFICIENT_FUNDS, failed)
        second = plan_windows(INSUFFICIENT_FUNDS, failed)
        assert [w.at for w in first] == [w.at for w in second]

    def test_the_wall_clock_does_not_change_the_windows(self):
        """Only `is_past` may differ as time moves. The moments themselves
        are a function of the failure."""
        failed = at(2026, 9, 2, 14, 30)
        monday = plan_windows(INSUFFICIENT_FUNDS, failed, now=at(2026, 9, 3))
        much_later = plan_windows(INSUFFICIENT_FUNDS, failed, now=at(2026, 10, 20))
        assert [w.at for w in monday] == [w.at for w in much_later]


class TestPaydayProximity:
    """"I get paid on the 1st" is the one objection where waiting is the
    correct answer rather than a discount."""

    def test_a_failure_on_the_second_targets_the_fifteenth_not_the_first_that_just_went(self):
        windows = plan_windows(INSUFFICIENT_FUNDS, at(2026, 9, 2, 11))
        assert windows
        assert windows[0].at.day == 15, f"aimed at day {windows[0].at.day}"

    def test_a_failure_late_in_the_month_targets_month_end_not_the_first(self):
        """The 30th, not the 1st. Month-end is a payroll date in its own
        right, and it is two days sooner - there is no reason to make
        somebody wait into October for money that arrives in September."""
        windows = plan_windows(INSUFFICIENT_FUNDS, at(2026, 9, 28, 11))
        assert windows
        assert (windows[0].at.month, windows[0].at.day) == (9, 30)
        # ...and the 1st is still offered, just second.
        assert (windows[1].at.month, windows[1].at.day) == (10, 1)

    def test_every_window_is_in_the_future_of_the_failure(self):
        for day in (1, 2, 14, 15, 16, 29, 30):
            failed = at(2026, 9, day, 11)
            for w in plan_windows(INSUFFICIENT_FUNDS, failed):
                assert w.at > failed, f"window {w.at} is before the failure {failed}"

    def test_the_spoken_reason_describes_the_date_it_is_actually_on(self):
        """One reason_code covers the 1st, the 15th and month-end, so a
        single fixed phrase is wrong two times in three.

        This shipped briefly as "Tuesday the 15th of September ... just
        after the usual end-of-month date" - false, specific, and said to a
        customer with complete confidence. Found by reading the output, not
        by a test, which is why there is now a test.
        """
        import calendar as _cal

        for failed_day in (2, 3, 9, 16, 20, 27, 28):
            for w in plan_windows(INSUFFICIENT_FUNDS, at(2026, 9, failed_day, 11)):
                if w.reason_code != "payday_proximity":
                    continue
                last = _cal.monthrange(w.at.year, w.at.month)[1]
                reason = w.say_reason.lower()
                if w.at.day == 1:
                    assert "1st of the month" in reason, (w.at, reason)
                elif w.at.day == 15:
                    assert "middle of the month" in reason, (w.at, reason)
                elif w.at.day == last:
                    assert "end of the month" in reason, (w.at, reason)
                # A window on the 15th must never be described as month-end.
                if w.at.day != last:
                    assert "end of the month" not in reason, (w.at, reason)

    def test_it_is_labelled_as_payday_proximity(self):
        windows = plan_windows(INSUFFICIENT_FUNDS, at(2026, 9, 2, 11))
        assert any(w.reason_code == "payday_proximity" for w in windows)


class TestTransientFailuresGetAQuickRetry:
    def test_a_soft_decline_is_offered_a_window_within_the_day(self):
        failed = at(2026, 9, 2, 11)
        windows = plan_windows(SOFT_DECLINE, failed)
        assert windows
        assert windows[0].at - failed <= timedelta(hours=24)

    def test_the_suggested_minute_is_one_a_person_would_say(self):
        """The quick retry is the only window derived by arithmetic on the
        failure rather than from a calendar date, so it used to inherit the
        exact minute the payment broke - "around 4:17 in the afternoon".
        Nobody offers somebody 4:17."""
        for minute in (0, 7, 17, 29, 30, 31, 45, 59):
            windows = plan_windows(SOFT_DECLINE, at(2026, 9, 2, 10, minute))
            assert windows[0].at.minute in (0, 30), windows[0].at

    def test_a_rail_outage_is_treated_as_transient_too(self):
        windows = plan_windows(RAIL_DOWN, at(2026, 9, 2, 11))
        assert any(w.reason_code == "transient_quick_retry" for w in windows)


class TestTheShapeOfWhatComesBack:
    @pytest.mark.parametrize(
        "failure_class", [SOFT_DECLINE, RAIL_DOWN, INSUFFICIENT_FUNDS, AUTH_DROP, USER_ABANDON, UNKNOWN]
    )
    def test_windows_are_ordered_capped_and_bounded(self, failure_class):
        failed = at(2026, 9, 2, 11)
        windows = plan_windows(failure_class, failed)
        assert 1 <= len(windows) <= 3
        assert [w.at for w in windows] == sorted(w.at for w in windows)
        # INSUFFICIENT_FUNDS reaches further on purpose. A customer who gets
        # paid on the 1st is thirteen days away from being able to pay, and
        # a week-long horizon silently dropped every payday it could name -
        # so the one class that most needed a date got none at all.
        horizon = timedelta(days=35 if failure_class == INSUFFICIENT_FUNDS else 7)
        for w in windows:
            assert w.at - failed <= horizon

    def test_no_two_windows_land_on_the_same_moment(self):
        windows = plan_windows(SOFT_DECLINE, at(2026, 9, 2, 11))
        assert len({w.at for w in windows}) == len(windows)

    @pytest.mark.parametrize(
        "failure_class", [SOFT_DECLINE, RAIL_DOWN, INSUFFICIENT_FUNDS, AUTH_DROP, USER_ABANDON, UNKNOWN]
    )
    def test_every_window_carries_a_phrase_the_agent_can_read_aloud(self, failure_class):
        for w in plan_windows(failure_class, at(2026, 9, 2, 11)):
            assert w.say_window and isinstance(w.say_window, str)
            # The agent is handed the finished phrase for the same reason it
            # is handed say_amount: a model asked to format a date mid-
            # sentence will sometimes get it wrong, and there is no prompt
            # that makes it reliably right.
            assert not re.search(r"\d{4}-\d{2}-\d{2}", w.say_window), w.say_window
            assert "T00:" not in w.say_window and "+05:30" not in w.say_window
            # "the 3th of September" is a machine talking.
            assert not re.search(r"\d*[123]th", w.say_window), w.say_window

    def test_a_window_already_gone_by_is_marked_rather_than_hidden(self):
        failed = at(2026, 9, 2, 11)
        windows = plan_windows(SOFT_DECLINE, failed, now=at(2026, 9, 30))
        assert windows, "windows vanished once they were in the past"
        assert all(w.is_past for w in windows)


class TestTheAgentIsNeverGivenACausalClaim:
    """The agent may say WHEN. It may not say WHY in terms of how banks or
    issuers behave - we have no evidence for that, and a confident invented
    justification to a customer is the failure this codebase keeps finding.
    """

    FORBIDDEN = (
        "bank", "banks", "issuer", "more likely", "more reliabl",
        "success rate", "process payments", "higher chance", "statistic",
    )

    @pytest.mark.parametrize(
        "failure_class", [SOFT_DECLINE, RAIL_DOWN, INSUFFICIENT_FUNDS, AUTH_DROP, USER_ABANDON, UNKNOWN]
    )
    def test_no_spoken_phrase_explains_bank_behaviour(self, failure_class):
        for w in plan_windows(failure_class, at(2026, 9, 2, 11)):
            spoken = f"{w.say_window} {w.say_reason}".lower()
            for banned in self.FORBIDDEN:
                assert banned not in spoken, f"{failure_class} says {spoken!r}"

    @pytest.mark.parametrize(
        "failure_class", [SOFT_DECLINE, RAIL_DOWN, INSUFFICIENT_FUNDS, AUTH_DROP, USER_ABANDON, UNKNOWN]
    )
    def test_nothing_promises_an_automatic_retry(self, failure_class):
        """Tier 1 has no dispatcher. An agent saying "we'll try again
        automatically" would be describing machinery that does not exist -
        the same shape as the false "I've sent that offer" in FINDINGS #2.
        """
        for w in plan_windows(failure_class, at(2026, 9, 2, 11)):
            spoken = f"{w.say_window} {w.say_reason}".lower()
            for banned in ("automatic", "we will try", "we'll try", "will be charged", "retry your card"):
                assert banned not in spoken, f"{failure_class} promises dispatch: {spoken!r}"
