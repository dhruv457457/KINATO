"""The price the customer hears has to be the price they owe.

From a live call, about a cart costing ₹1,290:

    "your order can be sent at the full amount of two hundred ninety-nine
     thousand paise, which is two hundred ninety-nine rupees"

Wrong twice in one sentence - a figure a hundred times too large, and then a
"conversion" that lands on a different number entirely - and said with total
confidence. The customer cannot act on either.

The cause was that the tools handed the model paise and left it to divide.
That is arithmetic in the middle of a spoken sentence, and a model will
sometimes get it wrong; there is no prompt that makes it reliably right. So
the tools now hand over the finished phrase and the prompt says to read it.

Same shape as the offer token: do not ask the model to compute a number that
matters, give it one.
"""
import pytest

from app.agents.tools import _say_rupees


class TestTheSpokenFigure:
    @pytest.mark.parametrize(
        "paise,expected",
        [
            (129_000, "1,290 rupees"),
            (549_000, "5,490 rupees"),
            (299_000, "2,990 rupees"),
            (100_000, "1,000 rupees"),
            (99_900, "999 rupees"),
            (5_000, "50 rupees"),
        ],
    )
    def test_whole_rupees_are_said_the_way_a_person_says_them(self, paise, expected):
        assert _say_rupees(paise) == expected

    def test_the_live_failure_is_now_correct(self):
        """The exact cart the agent misquoted."""
        assert _say_rupees(129_000) == "1,290 rupees"
        assert "paise" not in _say_rupees(129_000)

    def test_paise_appear_only_when_there_actually_are_any(self):
        """"1,290 rupees and 0 paise" is what a computer says."""
        assert _say_rupees(129_050) == "1,290 rupees and 50 paise"
        assert _say_rupees(129_000) == "1,290 rupees"

    @pytest.mark.parametrize("empty", [0, None])
    def test_nothing_is_said_as_zero_not_as_a_crash(self, empty):
        """A missing amount must not take down the turn that quotes it."""
        assert _say_rupees(empty) == "0 rupees"


class TestTheModelIsNotAskedToConvert:
    async def test_check_offer_returns_a_ready_made_phrase(
        self, connected_merchant_id, unique_checkout_id
    ):
        from app.agents import tools as tools_module
        from app.agents.state import AgentContext
        from app.db.repositories import checkouts as checkouts_repo

        checkouts_repo.create_checkout(
            merchant_id=connected_merchant_id,
            amount_paise=129_000,
            cogs_paise=50_000,
            checkout_id=unique_checkout_id,
        )
        result = await tools_module._check_offer(
            AgentContext(
                merchant_id=connected_merchant_id,
                correlation_id="corr_x",
                checkout_id=unique_checkout_id,
            ),
            requested_discount_percent=0,
        )
        assert result["say_amount"] == "1,290 rupees"
        # The machine-readable figure stays for the audit trail and the
        # token; it is simply not the one the agent is told to speak.
        assert result["final_amount_paise"] == 129_000

    async def test_the_cart_tool_does_not_hand_over_the_merchants_cost_price(
        self, connected_merchant_id, unique_checkout_id
    ):
        """cogs has no business reaching the model at all.

        It is what the merchant PAID for the goods. Nothing the agent says to
        a customer should be derived from it, and a model that can see a
        number can say it out loud. The policy engine reads it straight from
        the database for the margin floor, which is the only place it is
        needed.
        """
        from app.agents.tools import get_cart
        from app.agents.audit import execute_tool
        from app.agents.state import AgentContext
        from app.db.repositories import checkouts as checkouts_repo

        checkouts_repo.create_checkout(
            merchant_id=connected_merchant_id,
            amount_paise=129_000,
            cogs_paise=50_000,
            checkout_id=unique_checkout_id,
        )
        result = await execute_tool(
            get_cart,
            {},
            AgentContext(
                merchant_id=connected_merchant_id,
                correlation_id="corr_x",
                checkout_id=unique_checkout_id,
            ),
        )
        assert "cogs_paise" not in result, "the model can see the merchant's cost price"
        assert "cogs" not in str(result).lower()
        assert result["say_amount"] == "1,290 rupees"

    def test_the_prompt_tells_the_agent_which_field_to_read(self):
        from app.channels.voice_runtime import SYSTEM_PROMPT_TEMPLATE

        assert "say_amount" in SYSTEM_PROMPT_TEMPLATE
