"""The agent may not decline a discount it never asked about.

From a live call, verbatim, against a merchant whose ceiling was 8% and
whose ladder opens at 3%:

    17:07:31  customer: "No price is too high."
    17:07:33  check_offer -> REJECTED_UNCONFIRMED_BARRIER
              agent: "Just to confirm, you mentioned that the price is
                      too high..."
    17:07:47  customer: "No, I was thinking to getting any discount."   (0.90)
    17:07:48  agent: "I see, you're looking for a discount. Unfortunately,
                      I can't offer a discount at this time."
              tools: []

`tools: []`. The barrier gate had opened - `discount_bounced` was set on the
previous turn, exactly as designed - and the model walked past it, declined
on its own authority, and told the customer a policy outcome that no policy
engine had produced. A discount was available. It never asked.

This is the same failure as FINDINGS #1 and as the false "I've sent that
offer": **a statement about money that is not true about what happened.**
The direction is merely the expensive one for the merchant rather than the
customer - the sale closed at full price only because the customer gave up
asking.

The rule is therefore not "try harder to remember to call check_offer". It
is that the agent cannot SAY no to a discount unless something that is
allowed to say no actually did. Refusing is a decision reserved to the
policy engine, and a sentence claiming that decision without one behind it
gets replaced - the same treatment, and for the same reason, as a reply
that asks for card details.
"""
import pytest

from app.channels.voice_runtime import (
    _DISCOUNT_REFUSAL_REPLACEMENT,
    claims_discount_refused,
)


class TestSpottingTheClaim:
    @pytest.mark.parametrize(
        "reply",
        [
            "Unfortunately, I can't offer a discount at this time.",
            "I see, you're looking for a discount. Unfortunately, I can't offer a discount at this time.",
            "I'm unable to offer any discount right now.",
            "We can't do a discount on this order.",
            "Sorry, no discount is available for this cart.",
            "I am not able to give you a discount.",
            "There's no discount I can apply here.",
            "I cannot provide any discount, but I can send the link.",
        ],
    )
    def test_a_refusal_the_agent_invented_is_recognised(self, reply):
        assert claims_discount_refused(reply) is True

    @pytest.mark.parametrize(
        "reply",
        [
            "I've applied a 3 percent discount to your order.",
            "Good news - I can do 3 percent off for you.",
            "The total amount for your order is 2,990 rupees.",
            "Would you like me to send you the payment link?",
            "Let me check what I can do on the price.",
            "I've sent that offer to your email.",
            "",
        ],
    )
    def test_an_ordinary_reply_is_left_alone(self, reply):
        assert claims_discount_refused(reply) is False

    def test_offering_a_discount_is_not_refusing_one(self):
        """The guard must not fire on the sentence we most want to hear."""
        assert claims_discount_refused("I can offer you a discount of 3 percent.") is False


class TestTheReplacementSaysSomethingTrue:
    def test_it_does_not_promise_a_discount_either(self):
        """Replacing one false claim with the opposite false claim would be
        worse: the customer would hear a discount is coming when the policy
        engine may be about to refuse."""
        text = _DISCOUNT_REFUSAL_REPLACEMENT.lower()
        for promise in ("i can offer", "you'll get", "approved", "here's your discount"):
            assert promise not in text

    def test_it_commits_only_to_checking(self):
        text = _DISCOUNT_REFUSAL_REPLACEMENT.lower()
        assert "check" in text or "see what" in text

    def test_it_is_short_enough_to_say_on_a_phone(self):
        assert len(_DISCOUNT_REFUSAL_REPLACEMENT) < 200


class TestTheGuardIsWiredAndBounded:
    def test_a_real_refusal_is_not_rewritten(self):
        """When check_offer actually refused, the agent relaying that is
        telling the truth - and rewriting it would hide the one thing the
        customer needs to hear."""
        import inspect
        from app.channels import voice_runtime

        src = inspect.getsource(voice_runtime)
        assert "claims_discount_refused(reply_text) and not result.refusals" in src, (
            "the guard must only fire when nothing refused this turn"
        )

    def test_the_prompt_forbids_deciding_alone(self):
        from app.channels.voice_runtime import SYSTEM_PROMPT_TEMPLATE

        assert "NEVER SAY A DISCOUNT IS UNAVAILABLE" in SYSTEM_PROMPT_TEMPLATE
        assert "check_offer" in SYSTEM_PROMPT_TEMPLATE

    def test_the_replacement_does_not_trip_the_guard_it_replaces(self):
        """A replacement the guard would itself catch cannot be trusted to
        terminate - the same trap the card-details replacement documents."""
        from app.channels.voice_runtime import (
            _DISCOUNT_REFUSAL_REPLACEMENT,
            claims_discount_refused,
            solicits_card_details,
        )

        assert claims_discount_refused(_DISCOUNT_REFUSAL_REPLACEMENT) is False
        assert solicits_card_details(_DISCOUNT_REFUSAL_REPLACEMENT) is False

    def test_the_live_sentence_that_caused_this(self):
        from app.channels.voice_runtime import claims_discount_refused

        assert claims_discount_refused(
            "I see, you're looking for a discount. Unfortunately, I can't offer a "
            "discount at this time. Would you like me to send you the payment link "
            "for the original amount of two thousand nine hundred ninety rupees?"
        ) is True
