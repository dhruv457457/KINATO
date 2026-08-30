"""A paid cart must not be able to mint a spendable offer.

check_offer used to read the checkout, decide it was unpaid, evaluate the
policy, and only then INSERT the token - three statements, and a real window
between the first and the last. On a live call that window is not
theoretical: the customer can be paying from the earlier link while the
agent is still talking to them, which is exactly why voice_runtime re-checks
`already paid` on every single turn.

The mint is now conditional in the same statement that writes the row, so
the window is gone. These tests hold that closed, and hold the audit shape
steady while it is - the batch scoreboard reads those rows, and a refusal it
cannot recognise is a recovery it silently scores as a loss.
"""
import pytest

from app.agents.audit import execute_tool
from app.agents.state import AgentContext
from app.agents.tools import check_offer, issue_offer
from app.core.ids import new_id
from app.db.database import get_db
from app.db.repositories import checkouts as checkouts_repo
from app.db.repositories import offer_tokens as offer_tokens_repo


def _ctx(merchant_id, checkout_id=None, **overrides):
    return AgentContext(
        merchant_id=merchant_id,
        correlation_id=new_id("corr"),
        checkout_id=checkout_id,
        **overrides,
    )


def _mark_paid(checkout_id: str) -> None:
    """Pay the cart the way the webhook would, behind the agent's back."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE checkouts SET status = 'paid', paid_at = CURRENT_TIMESTAMP WHERE checkout_id = %s",
            (checkout_id,),
        )


@pytest.fixture
def unpaid_cart(connected_merchant_id, unique_checkout_id):
    checkouts_repo.create_checkout(
        merchant_id=connected_merchant_id,
        amount_paise=100_000,
        cogs_paise=50_000,
        checkout_id=unique_checkout_id,
    )
    return connected_merchant_id, unique_checkout_id


class TestPaidCartCannotMint:
    def test_the_repository_refuses_to_mint_against_a_paid_cart(self, unpaid_cart):
        """The guarantee lives in the statement, not in the caller.

        A tool that happens to check first is not the same as a mint that
        cannot happen - tools.py makes that argument about the money gate in
        general, and this is it applied to the row itself.
        """
        merchant_id, checkout_id = unpaid_cart
        _mark_paid(checkout_id)

        token_row = offer_tokens_repo.create_offer_token(
            merchant_id=merchant_id,
            decision="ALLOW",
            reason="APPROVED",
            base_amount_paise=100_000,
            final_amount_paise=95_000,
            requested_percent=5,
            approved_percent=5,
            checkout_id=checkout_id,
        )
        assert token_row is None, "a paid cart minted a spendable offer token"

    def test_an_unpaid_cart_still_mints_normally(self, unpaid_cart):
        """The conditional must not refuse the ordinary case."""
        merchant_id, checkout_id = unpaid_cart
        token_row = offer_tokens_repo.create_offer_token(
            merchant_id=merchant_id,
            decision="ALLOW",
            reason="APPROVED",
            base_amount_paise=100_000,
            final_amount_paise=95_000,
            requested_percent=5,
            approved_percent=5,
            checkout_id=checkout_id,
        )
        assert token_row is not None
        assert token_row["offer_token"].startswith("off_")
        assert token_row["approved_percent"] == 5
        assert token_row["final_amount_paise"] == 95_000
        # RETURNING has to give back the same row a follow-up SELECT did,
        # including the server-computed expiry - that column is what makes a
        # token stop being spendable.
        assert token_row["expires_at"] is not None

    async def test_check_offer_reports_already_paid_not_a_crash(self, unpaid_cart):
        """The refusal the merchant and the scoreboard both read."""
        merchant_id, checkout_id = unpaid_cart
        _mark_paid(checkout_id)

        result = await execute_tool(check_offer, {"requested_discount_percent": 5}, _ctx(merchant_id, checkout_id))
        assert result["decision"] == "DENY"
        assert result["reason"] == "REJECTED_ALREADY_PAID"
        assert "offer_token" not in result, "a denied check_offer handed back something spendable"

    async def test_nothing_spendable_survives_a_paid_cart(self, unpaid_cart):
        """End to end: no token exists, so issue_offer has nothing to spend."""
        merchant_id, checkout_id = unpaid_cart
        _mark_paid(checkout_id)

        checked = await execute_tool(check_offer, {"requested_discount_percent": 5}, _ctx(merchant_id, checkout_id))
        assert checked["decision"] == "DENY"

        issued = await execute_tool(
            issue_offer, {"offer_token": "off_does_not_exist", "channel": "email"},
            _ctx(merchant_id, checkout_id),
        )
        assert issued["status"] == "REJECTED"


class TestAuditShapeIsUnchanged:
    async def test_an_approval_still_carries_everything_the_scoreboard_reads(self, unpaid_cart):
        """run_recovery_batch scores every case from these fields.

        `decision`, `requested_percent` and `approved_percent` are parsed
        straight out of the audit row's args/result JSON to produce
        rule_breaks and approved_percent_ceiling. Changing how the token is
        minted must not change what the row says about it.
        """
        merchant_id, checkout_id = unpaid_cart
        result = await execute_tool(
            check_offer, {"requested_discount_percent": 40}, _ctx(merchant_id, checkout_id)
        )
        assert result["decision"] == "MODIFY"
        assert result["requested_percent"] == 40
        assert result["approved_percent"] == 10.0
        assert result["reason"] == "REJECTED_CEILING"
        assert result["ceiling_percent"] == 10.0
        assert result["offer_token"].startswith("off_")
        assert result["final_amount_paise"] == 90_000
        assert result["expires_at"]
