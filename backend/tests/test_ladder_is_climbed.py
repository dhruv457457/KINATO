"""Turning down an offer is what earns the next one.

From a live call, verbatim, on a merchant whose ladder is [3, 7, 10]:

    check_offer   MODIFY  asked 15% -> approved 3%   (REJECTED_LADDER_STEP)
    agent:   "I can offer you a discount of three percent... would you like
              me to send the link?"
    customer: "No, can we go for 15% discount, that is too low for me."
    agent:   "I understand that you're looking for a larger discount, but
              the maximum I can offer right now is three percent."
    tools:   []

`tools: []` again. The customer refused rung one - which is precisely the
event the ladder exists to answer - and the agent declared three percent a
maximum without asking anything. A second check_offer would have returned
seven. The sale closed at 3% on a policy that would have gone further to
win it.

This is the sibling of the "I can't offer a discount" bug and the same
mistake underneath: **the agent stating a limit that no policy engine
produced on that turn.** The difference is only in the wording, which is
why the first guard did not catch it - "the maximum I can offer is three
percent" contains no refusal words at all.

The rule is the same one, widened: an agent may report a limit it has just
been given. It may not announce one from memory, because the number it
remembers is a rung, and a rung is not a ceiling.
"""
import pytest

from app.channels.voice_runtime import (
    _LADDER_NOT_CLIMBED_REPLACEMENT,
    claims_a_maximum,
    claims_discount_refused,
    solicits_card_details,
)


class TestSpottingTheClaim:
    @pytest.mark.parametrize(
        "reply",
        [
            "the maximum I can offer right now is three percent",
            "I understand you want more, but the maximum I can offer right now is three percent.",
            "That's the most I can do on this order.",
            "Three percent is the best I can do, I'm afraid.",
            "The highest discount I can give you is 3%.",
            "That is the maximum discount available on this cart.",
            "I'm afraid that's the limit of what I can offer.",
        ],
    )
    def test_an_unchecked_maximum_is_recognised(self, reply):
        assert claims_a_maximum(reply) is True

    @pytest.mark.parametrize(
        "reply",
        [
            "I can offer you a discount of three percent.",
            "Good news - three percent off brings it to 4,161 rupees.",
            "Would you like me to send you the payment link?",
            "Let me check what I can do on the price for you.",
            "I've sent that offer to your email.",
            "Your order total is 4,290 rupees.",
            "",
        ],
    )
    def test_an_ordinary_offer_is_left_alone(self, reply):
        """Quoting a number the engine just approved is the agent doing its
        job. Only the claim that it is the CEILING is the problem."""
        assert claims_a_maximum(reply) is False


class TestTheReplacementGoesBackAndAsks:
    def test_it_does_not_assert_any_limit_of_its_own(self):
        text = _LADDER_NOT_CLIMBED_REPLACEMENT.lower()
        for banned in ("maximum", "most i can", "best i can", "limit", "cannot", "can't"):
            assert banned not in text

    def test_it_does_not_promise_a_bigger_discount_either(self):
        """Replacing an invented ceiling with an invented concession would
        be the same bug pointed the other way."""
        text = _LADDER_NOT_CLIMBED_REPLACEMENT.lower()
        for banned in ("i can offer", "approved", "you'll get", "percent off"):
            assert banned not in text

    def test_it_cannot_trip_any_of_the_guards_including_itself(self):
        """A replacement one of the guards would catch is a replacement
        that cannot be trusted to terminate."""
        assert claims_a_maximum(_LADDER_NOT_CLIMBED_REPLACEMENT) is False
        assert claims_discount_refused(_LADDER_NOT_CLIMBED_REPLACEMENT) is False
        assert solicits_card_details(_LADDER_NOT_CLIMBED_REPLACEMENT) is False

    def test_it_is_short_enough_for_a_phone_turn(self):
        assert len(_LADDER_NOT_CLIMBED_REPLACEMENT) < 200


class TestTheGuardOnlyFiresWhenNothingWasAsked:
    def test_it_is_gated_on_check_offer_not_having_run(self):
        """After a real check_offer, a maximum the engine returned is a fact
        the agent SHOULD relay - at the top rung it is literally true, and
        rewriting it would hide the answer the customer needs."""
        import inspect

        from app.channels import voice_runtime

        src = inspect.getsource(voice_runtime)
        assert 'claims_a_maximum(reply_text) and "check_offer" not in' in src, (
            "the guard must only fire on a turn where the engine was not consulted"
        )

    def test_the_prompt_says_a_refusal_earns_another_check(self):
        from app.channels.voice_runtime import SYSTEM_PROMPT_TEMPLATE

        assert "TURN DOWN AN OFFER" in SYSTEM_PROMPT_TEMPLATE
        assert "CALL check_offer AGAIN" in SYSTEM_PROMPT_TEMPLATE
        # And the specific sentence the live call produced, forbidden by name.
        assert "that is the maximum" in SYSTEM_PROMPT_TEMPLATE

    def test_the_live_sentence_that_caused_this(self):
        assert claims_a_maximum(
            "I understand that you're looking for a larger discount, but the maximum "
            "I can offer right now is three percent. Would you like me to send the "
            "link at that price?"
        ) is True
