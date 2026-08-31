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
        """The parts must fit the whole.

        The ceiling applies when TTS is free - ElevenLabs off, every line
        rendered by Twilio with no network call. When ElevenLabs IS active
        the reservation is subtracted dynamically instead, so the ceiling is
        never reached; that path is covered below.
        """
        worst_case = vr.VOICE_DEADLINE_MAX_S + vr.RESPONSE_RESERVE_S
        assert worst_case <= vr.TURN_HARD_TIMEOUT_S, (
            f"reasoning {vr.VOICE_DEADLINE_MAX_S} + reserve {vr.RESPONSE_RESERVE_S} "
            f"= {worst_case}, which does not fit inside TURN_HARD_TIMEOUT_S "
            f"{vr.TURN_HARD_TIMEOUT_S}"
        )

    def test_the_whole_turn_fits_twilios_real_budget_not_its_documented_one(self):
        """Measured, not documented.

        Twilio's stated webhook budget is ~15s. This deployment's real one is
        about five - established from six live turns, where 1.2s/1.3s/1.5s/4.0s
        survived and 5.2s/5.7s/7.2s were killed with "an application error has
        occurred".

        Designing against 15 is what killed three calls on the turn that
        commits the sale.
        """
        MEASURED_TWILIO_CEILING_S = 5.0
        assert vr.TURN_HARD_TIMEOUT_S < MEASURED_TWILIO_CEILING_S, (
            "our own timeout must fire BEFORE Twilio's, or the cut-off branch "
            "never speaks and the customer hears Twilio's error instead"
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

    def test_tts_time_is_only_reserved_when_tts_will_cost_any(self, monkeypatch):
        """With ElevenLabs off, voice_block makes no network call at all.

        Reserving two seconds for it hands 40% of a five-second budget to
        something that takes microseconds - and takes it from the only thing
        left to spend it on.
        """
        import time

        monkeypatch.setattr(time, "monotonic", lambda: 1000.0)
        # 0.6s spent, chosen so NEITHER result is clamped by the floor or
        # the ceiling - otherwise this measures the clamp rather than the
        # reservation it is supposed to be about.
        token = vr._turn_started.set(999.4)
        try:
            monkeypatch.setattr(vr, "elevenlabs_active", lambda: False)
            without_tts = vr._remaining_reasoning_budget()

            monkeypatch.setattr(vr, "elevenlabs_active", lambda: True)
            with_tts = vr._remaining_reasoning_budget()
        finally:
            vr._turn_started.reset(token)

        assert without_tts > with_tts, "the TTS reservation is not being applied"
        assert without_tts - with_tts == pytest.approx(vr.TTS_BUDGET_S, abs=0.01)

    def test_with_elevenlabs_on_the_turn_still_fits(self, monkeypatch):
        """The dynamic path, which is what keeps the arithmetic honest when
        TTS does cost real time."""
        import time

        monkeypatch.setattr(time, "monotonic", lambda: 1000.0)
        monkeypatch.setattr(vr, "elevenlabs_active", lambda: True)
        token = vr._turn_started.set(1000.0)  # nothing spent yet
        try:
            budget = vr._remaining_reasoning_budget()
        finally:
            vr._turn_started.reset(token)

        total = budget + vr.TTS_BUDGET_S + vr.RESPONSE_RESERVE_S
        assert total <= vr.TURN_HARD_TIMEOUT_S + 0.01

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
