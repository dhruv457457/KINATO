"""One live link per cart per price, not one per attempt.

Razorpay's test mode caps an account at thirty payment links FOREVER. This
project has exhausted that twice, and both times it presented as a broken
integration rather than an exhausted quota - the second time mid-call, with a
customer on the line who had just said "yes, send it" (FINDINGS #7).

The cause was that every attempt minted a fresh link, including a retry of a
cart where nothing had changed. Sending the same customer the same URL for the
same order at the same price is not a workaround: it is the artifact they were
already promised.

The condition that matters most here is the one that REFUSES to reuse. A link
carries an amount, and Razorpay will happily charge whatever that link says. A
reused link at the wrong price takes money nobody approved - which is worse
than any number of exhausted quotas, and worse than the bug this file exists
to fix. So the amount match is exact, in integer paise, and every test below
that asserts a refusal is protecting that.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.db.database import get_db
from app.db.repositories import checkouts as checkouts_repo
from app.db.repositories import recovery_attempts as recovery_attempts_repo


def _attempt_with_link(
    merchant_id: str,
    checkout_id: str,
    final_amount_paise: int,
    expires_in_hours: float = 24.0,
    url: str = "https://rzp.io/rzp/reused",
    link_id: str = "plink_reusable",
):
    """A prior attempt that already minted a link, the way _issue_offer does."""
    attempt = recovery_attempts_repo.create_recovery_attempt(merchant_id, checkout_id, None)
    recovery_attempts_repo.update_state(
        attempt["recovery_attempt_id"],
        "PAYMENT_LINK_SENT",
        final_amount_paise=final_amount_paise,
        rzp_payment_link_id=link_id,
        rzp_payment_link_url=url,
        rzp_payment_link_expires_at=datetime.now(timezone.utc) + timedelta(hours=expires_in_hours),
    )
    return attempt["recovery_attempt_id"]


@pytest.fixture
def cart(connected_merchant_id, unique_checkout_id):
    checkouts_repo.create_checkout(
        merchant_id=connected_merchant_id,
        amount_paise=100_000,
        cogs_paise=50_000,
        checkout_id=unique_checkout_id,
    )
    return connected_merchant_id, unique_checkout_id


class TestReuseHappens:
    def test_the_same_cart_at_the_same_price_reuses(self, cart):
        merchant_id, checkout_id = cart
        _attempt_with_link(merchant_id, checkout_id, 95_000)

        found = recovery_attempts_repo.find_reusable_payment_link(merchant_id, checkout_id, 95_000)
        assert found is not None
        assert found["rzp_payment_link_url"] == "https://rzp.io/rzp/reused"
        assert found["rzp_payment_link_id"] == "plink_reusable"

    def test_the_freshest_link_wins_when_several_exist(self, cart):
        """Retrying a cart repeatedly leaves several. Hand back the one that
        lives longest, not whichever the database happened to return."""
        merchant_id, checkout_id = cart
        _attempt_with_link(merchant_id, checkout_id, 95_000, expires_in_hours=2, link_id="plink_old",
                           url="https://rzp.io/rzp/old")
        _attempt_with_link(merchant_id, checkout_id, 95_000, expires_in_hours=20, link_id="plink_new",
                           url="https://rzp.io/rzp/new")

        found = recovery_attempts_repo.find_reusable_payment_link(merchant_id, checkout_id, 95_000)
        assert found["rzp_payment_link_id"] == "plink_new"


class TestReuseIsRefused:
    """Every one of these mints a new link instead. That is the safe direction:
    an extra link costs quota, a wrong link costs a customer money."""

    def test_a_different_amount_is_never_reused(self, cart):
        """The whole reason the match is exact.

        A 10% offer and a 7% offer on the same cart are different links for
        different amounts. Reusing one for the other charges a price nobody
        approved - and the policy engine's entire job is that no amount is
        ever spendable unless it wrote it.
        """
        merchant_id, checkout_id = cart
        _attempt_with_link(merchant_id, checkout_id, 95_000)

        assert recovery_attempts_repo.find_reusable_payment_link(merchant_id, checkout_id, 93_000) is None
        assert recovery_attempts_repo.find_reusable_payment_link(merchant_id, checkout_id, 100_000) is None
        # Off by a single paise is still off.
        assert recovery_attempts_repo.find_reusable_payment_link(merchant_id, checkout_id, 94_999) is None

    def test_an_expired_link_is_never_reused(self, cart):
        merchant_id, checkout_id = cart
        _attempt_with_link(merchant_id, checkout_id, 95_000, expires_in_hours=-1)

        assert recovery_attempts_repo.find_reusable_payment_link(merchant_id, checkout_id, 95_000) is None

    def test_a_link_with_no_recorded_expiry_is_never_reused(self, cart):
        """Rows minted before the expiry column existed.

        Unknown is not the same as live. Treating a NULL as reusable would
        hand out links from the whole history of this database, most of them
        long dead.
        """
        merchant_id, checkout_id = cart
        attempt = recovery_attempts_repo.create_recovery_attempt(merchant_id, checkout_id, None)
        recovery_attempts_repo.update_state(
            attempt["recovery_attempt_id"],
            "PAYMENT_LINK_SENT",
            final_amount_paise=95_000,
            rzp_payment_link_id="plink_legacy",
            rzp_payment_link_url="https://rzp.io/rzp/legacy",
        )
        assert recovery_attempts_repo.find_reusable_payment_link(merchant_id, checkout_id, 95_000) is None

    def test_another_merchants_link_is_never_reused(self, cart):
        """A link is payable into ONE Razorpay account. Reusing another
        merchant's would route a customer's money to the wrong business."""
        merchant_id, checkout_id = cart
        _attempt_with_link(merchant_id, checkout_id, 95_000)

        assert recovery_attempts_repo.find_reusable_payment_link(
            "mch_someone_else", checkout_id, 95_000
        ) is None

    def test_a_different_cart_is_never_reused(self, cart, connected_merchant_id):
        merchant_id, checkout_id = cart
        _attempt_with_link(merchant_id, checkout_id, 95_000)

        assert recovery_attempts_repo.find_reusable_payment_link(
            merchant_id, "chk_some_other_cart", 95_000
        ) is None

    def test_an_attempt_that_never_minted_one_is_not_a_match(self, cart):
        """A CREATED attempt has a null link. It must not satisfy the lookup."""
        merchant_id, checkout_id = cart
        recovery_attempts_repo.create_recovery_attempt(merchant_id, checkout_id, None)

        assert recovery_attempts_repo.find_reusable_payment_link(merchant_id, checkout_id, 95_000) is None
