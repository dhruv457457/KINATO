"""The turn budget has to add up, and nothing was checking that it did.

Before this file the constants were three independent numbers that happened
to be edited by hand: `VOICE_DEADLINE_S` 7.5 plus the agent runtime's 6.0s
settle grace plus a 2.0s TTS budget came to 15.5s, wrapped by an 11.0s hard
timeout, inside Twilio's 15.0s. Every one of those numbers had a careful
comment explaining it, and together they were impossible - the shield that
protects a half-finished money action could not physically fit inside the
clock that was cutting it off.

A comment cannot enforce arithmetic. These tests can.
"""
import pytest

from app.channels import voice_runtime as vr


class TestTurnBudgetArithmetic:
    def test_reasoning_plus_speaking_fits_inside_the_hard_timeout(self):
        """The parts must fit the whole, with the ceiling as the worst case."""
        worst_case = vr.VOICE_DEADLINE_MAX_S + vr.TTS_BUDGET_S + vr.RESPONSE_RESERVE_S
        assert worst_case <= vr.TURN_HARD_TIMEOUT_S, (
            f"reasoning {vr.VOICE_DEADLINE_MAX_S} + tts {vr.TTS_BUDGET_S} + "
            f"reserve {vr.RESPONSE_RESERVE_S} = {worst_case}, which does not fit "
            f"inside TURN_HARD_TIMEOUT_S {vr.TURN_HARD_TIMEOUT_S}"
        )

    def test_the_hard_timeout_leaves_time_to_actually_answer(self):
        """Answering late is the same as not answering.

        The cut-off branch still has to render TwiML and return it before
        Twilio gives up and plays its own error to the customer.
        """
        assert vr.TURN_HARD_TIMEOUT_S < vr.TWILIO_WEBHOOK_DEADLINE_S
        margin = vr.TWILIO_WEBHOOK_DEADLINE_S - vr.TURN_HARD_TIMEOUT_S
        assert margin >= 2.0, f"only {margin}s of margin to Twilio's deadline"

    def test_the_warning_fires_before_the_cut_not_after(self):
        """A warning logged after the turn was already killed is not a warning."""
        assert vr.TURN_BUDGET_WARN_S < vr.TURN_HARD_TIMEOUT_S

    def test_the_floor_is_below_the_ceiling(self):
        assert vr.VOICE_DEADLINE_MIN_S < vr.VOICE_DEADLINE_MAX_S


class TestDerivedBudget:
    """The budget is derived from the wall clock, not assumed in advance."""

    def test_with_no_turn_clock_it_falls_back_to_the_ceiling(self):
        """Unit tests and the batch harness call in without a turn started.

        Inventing a middling number there would silently give the scoreboard
        a different budget from production. The honest fallback is the
        ceiling.
        """
        token = vr._turn_started.set(0.0)
        try:
            assert vr._remaining_reasoning_budget() == vr.VOICE_DEADLINE_MAX_S
        finally:
            vr._turn_started.reset(token)

    def test_a_fresh_turn_gets_more_than_the_old_flat_budget(self, monkeypatch):
        """This is the point of the change.

        The old constant was a flat 7.5s that had to assume the worst about
        everything preceding it. A turn that has spent almost nothing should
        get materially more reasoning time than that - the seconds were
        always there, they were just being thrown away.
        """
        import time

        monkeypatch.setattr(time, "monotonic", lambda: 1000.0)
        token = vr._turn_started.set(999.9)  # 0.1s spent so far
        try:
            assert vr._remaining_reasoning_budget() > 7.5
        finally:
            vr._turn_started.reset(token)

    def test_an_expensive_turn_shrinks_its_own_budget(self, monkeypatch):
        """Self-correcting is the other half of deriving it.

        A turn that has already burned seconds - a cold session rehydration
        on a restarted worker, a slow paid-guard read - must not then hand
        the agent a budget that guarantees the hard timeout fires.
        """
        import time

        monkeypatch.setattr(time, "monotonic", lambda: 1008.0)
        token = vr._turn_started.set(1000.0)  # 8s already gone
        try:
            budget = vr._remaining_reasoning_budget()
            assert budget == vr.VOICE_DEADLINE_MIN_S
            # Even clamped at the floor, the turn is allowed to overrun the
            # hard timeout rather than being given zero: the outer wait_for
            # is what stops it, and it answers the customer when it does.
            assert budget > 0
        finally:
            vr._turn_started.reset(token)

    def test_the_budget_never_exceeds_its_ceiling(self, monkeypatch):
        import time

        monkeypatch.setattr(time, "monotonic", lambda: 1000.0)
        token = vr._turn_started.set(1000.0)  # nothing spent at all
        try:
            assert vr._remaining_reasoning_budget() <= vr.VOICE_DEADLINE_MAX_S
        finally:
            vr._turn_started.reset(token)
