"""
Promise-to-pay is a STOPPING rule, not a soft outcome.

"I'll pay on Friday" means stop selling, stop calling, and wait. Recording
that is only meaningful if it actually pauses outreach - otherwise it is a
note in a database while the phone keeps ringing, which is worse than never
asking. And a promise that lapses silently is just a lost sale, so exactly
ONE reminder is allowed afterwards, never a campaign.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.agents.tools import _parse_promise_date, record_promise_to_pay, ALL_TOOLS, FORBIDDEN_ARG_NAMES
from app.agents.state import AgentContext
from app.db.database import get_db
from app.db.repositories import checkouts as checkouts_repo
from app.db.repositories import recovery_attempts as ra_repo
from app.services import outreach_guards


class TestDateParsing:
    @pytest.mark.parametrize("text", ["tomorrow", "in 3 days", "today"])
    def test_spoken_dates_are_accepted(self, text):
        """Speech-to-text gives us "tomorrow" far more often than an ISO
        string; rejecting those would discard valid commitments."""
        assert _parse_promise_date(text) is not None

    @pytest.mark.parametrize("text", ["2020-01-01", "", "next lifetime", "in 900 days"])
    def test_impossible_dates_are_refused(self, text):
        """A promise for a date in the past is a mishearing, not a
        commitment - accepting it would pause outreach on bad data."""
        assert _parse_promise_date(text) is None


class TestPromisePausesOutreach:
    async def test_recording_a_promise_blocks_further_calls(self, real_merchant_id, unique_checkout_id):
        checkouts_repo.create_checkout(real_merchant_id, amount_paise=250_000, checkout_id=unique_checkout_id)
        attempt = ra_repo.create_recovery_attempt(real_merchant_id, unique_checkout_id, None)
        ctx = AgentContext(
            merchant_id=real_merchant_id, correlation_id=unique_checkout_id,
            customer_id=None, checkout_id=unique_checkout_id,
            recovery_attempt_id=attempt["recovery_attempt_id"],
        )

        allowed_before, _ = outreach_guards.no_active_promise(unique_checkout_id)
        assert allowed_before, "precondition: no promise yet"

        result = await record_promise_to_pay.fn(ctx, pay_date="in 3 days", amount_inr=2500.0,
                                                customer_words="I will pay on Friday after payday")
        assert result["status"] == "RECORDED"

        allowed_after, reason = outreach_guards.no_active_promise(unique_checkout_id)
        assert not allowed_after, "a recorded promise must stop further outreach"
        assert "promise_to_pay" in reason

    async def test_customers_own_words_are_stored_verbatim(self, real_merchant_id, unique_checkout_id):
        """The merchant should see what was actually said, not our summary."""
        checkouts_repo.create_checkout(real_merchant_id, amount_paise=250_000, checkout_id=unique_checkout_id)
        attempt = ra_repo.create_recovery_attempt(real_merchant_id, unique_checkout_id, None)
        ctx = AgentContext(
            merchant_id=real_merchant_id, correlation_id=unique_checkout_id, customer_id=None,
            checkout_id=unique_checkout_id, recovery_attempt_id=attempt["recovery_attempt_id"],
        )
        words = "salary comes on the first, I'll do it then"
        await record_promise_to_pay.fn(ctx, pay_date="in 4 days", customer_words=words)

        row = ra_repo.get_recovery_attempt(attempt["recovery_attempt_id"])
        assert row["state"] == "PROMISED"
        assert row["promise_words"] == words


class TestLapsedPromise:
    async def test_lapsed_promise_earns_exactly_one_reminder(self, real_merchant_id, unique_checkout_id):
        checkouts_repo.create_checkout(real_merchant_id, amount_paise=250_000, checkout_id=unique_checkout_id)
        attempt = ra_repo.create_recovery_attempt(real_merchant_id, unique_checkout_id, None)
        rid = attempt["recovery_attempt_id"]
        ra_repo.update_state(rid, "PROMISED")
        with get_db() as conn:
            conn.cursor().execute(
                "UPDATE recovery_attempts SET promised_at = NOW() - INTERVAL '2 days' "
                "WHERE recovery_attempt_id = %s", (rid,),
            )

        lapsed = [a["recovery_attempt_id"] for a in ra_repo.list_lapsed_promises()]
        assert rid in lapsed, "a promise past its date on an unpaid cart must surface"

        ra_repo.mark_promise_reminded(rid)

        lapsed_again = [a["recovery_attempt_id"] for a in ra_repo.list_lapsed_promises()]
        assert rid not in lapsed_again, "a reminded promise must never be reminded twice"

    async def test_promise_on_a_paid_cart_is_never_chased(self, real_merchant_id, unique_checkout_id):
        """They promised, then paid. Chasing that is the worst possible call."""
        checkouts_repo.create_checkout(real_merchant_id, amount_paise=250_000, checkout_id=unique_checkout_id)
        attempt = ra_repo.create_recovery_attempt(real_merchant_id, unique_checkout_id, None)
        rid = attempt["recovery_attempt_id"]
        ra_repo.update_state(rid, "PROMISED")
        with get_db() as conn:
            conn.cursor().execute(
                "UPDATE recovery_attempts SET promised_at = NOW() - INTERVAL '2 days' "
                "WHERE recovery_attempt_id = %s", (rid,),
            )
        checkouts_repo.mark_paid(unique_checkout_id)

        lapsed = [a["recovery_attempt_id"] for a in ra_repo.list_lapsed_promises()]
        assert rid not in lapsed, "a paid cart must never generate a promise reminder"


def test_promise_tool_exposes_no_forbidden_args():
    tool = next(t for t in ALL_TOOLS if t.name == "record_promise_to_pay")
    assert not (FORBIDDEN_ARG_NAMES & set(tool.parameters.keys()))
    assert tool.mutating, "recording a promise changes state and must be gated as mutating"
