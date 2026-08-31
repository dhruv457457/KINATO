"""Full price FIRST is not full price FOREVER, and the gate had no key.

`check_offer` refuses a discount when the payment simply broke - a declined
card is not a price objection, and giving margin away on a sale you had
already won is FINDINGS #1 measured in rupees. The refusal is
REJECTED_FULL_PRICE_FIRST, and its own comment says the rule is *first*, not
*forever*: it opens on `ctx.barrier_confirmed` once the customer says plainly
that the price is the problem.

Nothing ever set that flag for this path. `voice_runtime` derived
`barrier_confirmed` from a single refusal code, REJECTED_UNCONFIRMED_BARRIER,
so on a SOFT_DECLINE the escape hatch existed in the tool and had no key.

Live transcript, on a cart the customer had already called too expensive:

    customer: "I found that too much expensive for me"
    customer: "can I get any discount over here?"
    customer: "I want a discount because I found that too much expensive"
    check_offer: DENY REJECTED_FULL_PRICE_FIRST
    agent:    "the checkout failed due to a temporary issue, not the price"

Told three times, and the agent argued with them. That is FINDINGS #1 in the
direction that costs a sale rather than a margin - refusing to negotiate with
someone who is plainly negotiating.

The tool was always right. The caller could never open the door.
"""
import pytest

from app.agents.state import AgentContext
from app.agents import tools as tools_module
from app.db.repositories import checkouts as checkouts_repo
from app.services.failure_diagnosis import AUTH_DROP, HARD_DECLINE, SOFT_DECLINE


@pytest.fixture
def priced_cart(connected_merchant_id, unique_checkout_id):
    checkouts_repo.create_checkout(
        merchant_id=connected_merchant_id,
        amount_paise=129_000,
        cogs_paise=50_000,
        checkout_id=unique_checkout_id,
    )
    return connected_merchant_id, unique_checkout_id


def _ctx(merchant_id, checkout_id, **overrides):
    base = dict(merchant_id=merchant_id, correlation_id="corr_x", checkout_id=checkout_id)
    base.update(overrides)
    return AgentContext(**base)


class TestTheGateOpensOnceTheBarrierIsConfirmed:
    @pytest.mark.parametrize("failure_class", [SOFT_DECLINE, HARD_DECLINE, AUTH_DROP])
    async def test_a_discount_is_refused_before_confirmation(self, priced_cart, failure_class):
        """Unchanged, and it must stay unchanged. This is the rule that stops
        margin being given away on a sale that was already won."""
        merchant_id, checkout_id = priced_cart
        result = await tools_module._check_offer(
            _ctx(merchant_id, checkout_id, failure_class=failure_class),
            requested_discount_percent=10,
        )
        assert result["decision"] == "DENY"
        assert result["reason"] == "REJECTED_FULL_PRICE_FIRST"

    @pytest.mark.parametrize("failure_class", [SOFT_DECLINE, HARD_DECLINE, AUTH_DROP])
    async def test_a_discount_is_allowed_once_the_barrier_is_confirmed(
        self, priced_cart, failure_class
    ):
        """The half that was unreachable.

        A customer who has said the price is too high has said something the
        failure class cannot know. Once that is confirmed, the payment having
        broken stops being the whole story.
        """
        merchant_id, checkout_id = priced_cart
        result = await tools_module._check_offer(
            _ctx(merchant_id, checkout_id, failure_class=failure_class, barrier_confirmed=True),
            requested_discount_percent=10,
        )
        assert result["decision"] in ("ALLOW", "MODIFY"), (
            "full price FIRST became full price FOREVER - the customer said the price "
            "was the problem and the agent still could not act on it"
        )
        assert result["approved_percent"] > 0

    async def test_full_price_still_goes_through_unconfirmed(self, priced_cart):
        """The common path must never be blocked by any of this. They want
        to pay; blocking the link would be far worse than the bug above."""
        merchant_id, checkout_id = priced_cart
        result = await tools_module._check_offer(
            _ctx(merchant_id, checkout_id, failure_class=SOFT_DECLINE),
            requested_discount_percent=0,
        )
        assert result["decision"] in ("ALLOW", "MODIFY")
        assert result["offer_token"]


class TestTheCallerActuallyTurnsTheKey:
    """The bug was never in the tool. It was that nothing set the flag.

    voice_runtime watched for exactly one refusal code, so the branch that
    opens this gate could not be reached from the class that needs it most.
    """

    def test_both_refusals_open_the_gate(self):
        import inspect

        from app.channels import voice_runtime

        src = inspect.getsource(voice_runtime._run_agent_turn)
        assert "REJECTED_UNCONFIRMED_BARRIER" in src
        assert "REJECTED_FULL_PRICE_FIRST" in src, (
            "a FULL_PRICE_FIRST refusal must also mark the barrier as needing "
            "confirmation, or the discount path is unreachable on a declined card"
        )

    def test_the_flag_is_read_from_the_session_into_the_context(self):
        import inspect

        from app.channels import voice_runtime

        src = inspect.getsource(voice_runtime._run_agent_turn)
        assert "barrier_confirmed=session[\"discount_bounced\"]" in src
