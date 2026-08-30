"""The agent must never ask a customer to say their card details aloud.

This is a transcript from a real call, after issue_offer had failed four
times in a row:

    "I apologize for the inconvenience, but it looks like I'm unable to
     send the link at the moment. However, I can assist you in completing
     your order if you'd like to provide your card details again."

Nothing in this system can accept a card number. No tool takes one, no
endpoint receives one, and a customer who complied would be reading
payment credentials to an automated line. The model reached for it because
the sanctioned path kept failing and a model under pressure improvises -
which is precisely when a guarantee has to be structural rather than a
sentence in a prompt.

The hard part is the second class of test below. "Your card was declined"
is the single most common true thing this agent says, and a guard that
suppresses it would break every call it was meant to protect.
"""
import pytest

from app.channels.voice_runtime import (
    _CARD_SOLICITATION_REPLACEMENT,
    solicits_card_details,
)


class TestSolicitationIsCaught:
    @pytest.mark.parametrize(
        "line",
        [
            # The verbatim line from the live call.
            "However, I can assist you in completing your order if you'd like to "
            "provide your card details again. Would you like to proceed with that?",
            "Could you please provide your card number?",
            "If you read me your card details I can put that through for you.",
            "I just need the CVV from the back of the card.",
            "Can you confirm your card number and expiry date?",
            "Please share the card information again.",
            "Tell me your card number when you're ready.",
            "What's the expiry on that card?",
        ],
    )
    def test_a_request_for_card_data_is_caught(self, line):
        assert solicits_card_details(line), f"not caught: {line!r}"


class TestLegitimateCardTalkSurvives:
    """The agent's whole job involves discussing a failed card payment.

    A guard that cannot tell "your card was declined" from "read me your
    card number" would silence the agent on the one subject it exists to
    talk about. These must all pass through untouched.
    """

    @pytest.mark.parametrize(
        "line",
        [
            "I understand - it looks like your card was declined by your bank that time.",
            "That can happen when a bank blocks an online payment. Nothing to worry about.",
            "Your payment didn't go through, so the order was never completed.",
            "Would you like me to send a fresh payment link so you can try again?",
            "You can pay with any card you like once the link arrives.",
            "Sometimes a card is declined for a reason the bank doesn't share with us.",
            "I've sent that to your email - just tap the link and pay however you prefer.",
            "Your order total is one thousand two hundred and ninety rupees.",
        ],
    )
    def test_talking_about_a_declined_card_is_not_solicitation(self, line):
        assert not solicits_card_details(line), f"false positive: {line!r}"


class TestReplacement:
    def test_the_replacement_offers_the_route_that_works(self):
        """A refusal that just says no leaves the customer stuck.

        The replacement has to name the only way this agent can actually
        take money - a link - or the call ends with a customer who wanted
        to pay and was told nothing useful.
        """
        assert "link" in _CARD_SOLICITATION_REPLACEMENT.lower()
        assert not solicits_card_details(_CARD_SOLICITATION_REPLACEMENT), (
            "the replacement line trips the guard that produced it"
        )

    def test_empty_and_none_are_safe(self):
        assert not solicits_card_details("")
        assert not solicits_card_details(None)  # type: ignore[arg-type]
