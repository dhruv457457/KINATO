"""An abandoned cart is a diagnosis, not an absence of one.

`failure_diagnosis` is real, pure and tested, and it drives a genuine refusal:
`REJECTED_FULL_PRICE_FIRST` stops the agent discounting a customer whose card
merely broke. It has a `USER_ABANDON` class for a cart someone walked away
from, with no error object at all.

Nothing ever assigned it. `record_failure()` was called from exactly one place
- the `payment.failed` webhook - so carts the sweeper timed out arrived at the
agent with `failure_class = NULL`. In production that was **604 of 608
checkouts**, and `REJECTED_FULL_PRICE_FIRST` had fired three times ever against
206 `check_offer` calls. The second-best feature in this project was running on
0.7% of real traffic.

NULL and USER_ABANDON are not equivalent downstream. Both leave a discount
permissible - neither is in `FULL_PRICE_FIRST_CLASSES` - but `describe(None)`
returns an empty string while `describe("USER_ABANDON")` returns a real
sentence. A NULL costs the agent its opening context, so it opens by asking
the customer something it could have known.

The second class of test here is the one that constrains the fix: a cart whose
payment genuinely failed and which is *later* swept must keep its
`SOFT_DECLINE`. `record_failure` overwrites unconditionally, so a careless
version of this change would quietly downgrade every real bank decline to
"they just wandered off" - and that class is exactly what forbids discounting
them.
"""
import pytest

from app.db.database import get_db
from app.db.repositories import checkouts as checkouts_repo
from app.gateway.sweeper import sweep_once
from app.services.failure_diagnosis import (
    FULL_PRICE_FIRST_CLASSES,
    SOFT_DECLINE,
    USER_ABANDON,
    describe,
    diagnose,
)


def _age_checkout(checkout_id: str) -> None:
    """Backdate started_at so the sweeper considers this cart stale."""
    with get_db() as conn:
        conn.cursor().execute(
            "UPDATE checkouts SET started_at = NOW() - INTERVAL '48 hours' "
            "WHERE checkout_id = %s",
            (checkout_id,),
        )


def _failure_class_of(checkout_id: str):
    return (checkouts_repo.get_checkout(checkout_id) or {}).get("failure_class")


class TestTheClassifierAlreadyAgreed:
    """The value is not invented by the sweeper - diagnose() already returns
    it for this exact input. The sweeper only records what was already true."""

    def test_no_error_object_diagnoses_as_user_abandon(self):
        assert diagnose({}).failure_class == USER_ABANDON
        assert diagnose(None).failure_class == USER_ABANDON

    def test_null_and_user_abandon_differ_where_it_counts(self):
        """Why writing the value is worth anything at all."""
        assert describe(None) == "", "a NULL gives the agent nothing to open with"
        assert describe(USER_ABANDON) != "", "the class it should have had says something"
        # Neither forbids a discount - an abandoned cart may genuinely be a
        # price objection, and that distinction is the whole point of the
        # class existing.
        assert USER_ABANDON not in FULL_PRICE_FIRST_CLASSES


class TestSweepingClassifies:
    async def test_a_swept_cart_is_classified_not_left_null(
        self, connected_merchant_id, unique_checkout_id
    ):
        checkouts_repo.create_checkout(
            merchant_id=connected_merchant_id,
            amount_paise=100_000,
            cogs_paise=50_000,
            checkout_id=unique_checkout_id,
        )
        assert _failure_class_of(unique_checkout_id) is None, "starts unclassified"

        _age_checkout(unique_checkout_id)
        await sweep_once()

        assert _failure_class_of(unique_checkout_id) == USER_ABANDON
        assert (checkouts_repo.get_checkout(unique_checkout_id) or {})["status"] == "abandoned"


class TestSweepingNeverDowngrades:
    async def test_a_real_decline_survives_a_later_sweep(
        self, connected_merchant_id, unique_checkout_id
    ):
        """The constraint on the fix.

        This cart's payment really was declined by a bank. If the sweep
        overwrote that with USER_ABANDON it would lose the class that
        FORBIDS discounting them - turning a customer who wanted to pay full
        price into one the agent is allowed to give margin away to. That is
        FINDINGS #1 measured in rupees.
        """
        checkouts_repo.create_checkout(
            merchant_id=connected_merchant_id,
            amount_paise=100_000,
            cogs_paise=50_000,
            checkout_id=unique_checkout_id,
        )
        checkouts_repo.record_failure(
            unique_checkout_id,
            {
                "error_code": "BAD_REQUEST_ERROR",
                "error_reason": "payment_failed",
                "error_description": "Your payment was declined by the bank.",
                "error_source": "bank",
                "error_step": "payment",
                "method": "card",
            },
            SOFT_DECLINE,
        )
        assert _failure_class_of(unique_checkout_id) == SOFT_DECLINE

        _age_checkout(unique_checkout_id)
        await sweep_once()

        assert _failure_class_of(unique_checkout_id) == SOFT_DECLINE, (
            "a real bank decline was downgraded to 'they wandered off' - "
            "which is precisely the class that stops the agent discounting them"
        )
        assert SOFT_DECLINE in FULL_PRICE_FIRST_CLASSES

    async def test_the_error_object_itself_is_not_blanked(
        self, connected_merchant_id, unique_checkout_id
    ):
        """record_failure overwrites unconditionally, so the sweep must not
        route through it - the error fields a merchant reads in the drawer
        would be wiped."""
        checkouts_repo.create_checkout(
            merchant_id=connected_merchant_id,
            amount_paise=100_000,
            cogs_paise=50_000,
            checkout_id=unique_checkout_id,
        )
        checkouts_repo.record_failure(
            unique_checkout_id,
            {
                "error_code": "BAD_REQUEST_ERROR",
                "error_reason": "payment_failed",
                "error_description": "Your payment was declined by the bank.",
                "error_source": "bank",
                "error_step": "payment",
                "method": "card",
            },
            SOFT_DECLINE,
        )

        _age_checkout(unique_checkout_id)
        await sweep_once()

        row = checkouts_repo.get_checkout(unique_checkout_id) or {}
        assert row.get("error_code") == "BAD_REQUEST_ERROR"
        assert row.get("error_description") == "Your payment was declined by the bank."
