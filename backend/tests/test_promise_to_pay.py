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


class TestTheReminderIsActuallySent:
    """The sweeper published `recovery.promise_lapsed` on a carefully
    once-only schedule, and nothing subscribed to it.

    Every existing test in this file passed. They assert that a lapsed
    promise SURFACES and is marked reminded exactly once - and all of that
    was true. What none of them asked was whether an email ever reached the
    customer, which it did not, for the entire life of the feature. The
    restraint was real; the reminder was imaginary.
    """

    @pytest.fixture
    def sent(self, monkeypatch):
        """Captures the email that would have gone out."""
        import app.services.email_service as email_module

        calls: list = []

        async def _fake_send(**kwargs):
            calls.append(kwargs)
            return "delivered", "resend_fake_id"

        monkeypatch.setattr(email_module.EmailService, "send_checkout_email", staticmethod(_fake_send))
        return calls

    @pytest.fixture
    def lapsed_case(self, real_merchant_id, unique_checkout_id):
        from app.db.repositories import customers as customers_repo

        checkouts_repo.create_checkout(
            real_merchant_id, amount_paise=250_000, checkout_id=unique_checkout_id,
            line_items=[{"product_id": "sku_1", "name": "Woven Table Runner"}],
        )
        customer = customers_repo.upsert_by_contact(
            real_merchant_id, email="promiser@example.com", phone="+911234599999", name="Ravi"
        )
        attempt = ra_repo.create_recovery_attempt(
            real_merchant_id, unique_checkout_id, customer["customer_id"]
        )
        rid = attempt["recovery_attempt_id"]
        ra_repo.update_state(
            rid, "PROMISED",
            rzp_payment_link_url="https://rzp.io/i/promise_test",
            final_amount_paise=225_000,
            approved_discount_percent=10.0,
        )
        return {
            "merchant_id": real_merchant_id,
            "checkout_id": unique_checkout_id,
            "customer_id": customer["customer_id"],
            "recovery_attempt_id": rid,
        }

    def _event(self, case):
        return {
            "merchant_id": case["merchant_id"],
            "correlation_id": case["checkout_id"],
            "payload": {
                "recovery_attempt_id": case["recovery_attempt_id"],
                "checkout_id": case["checkout_id"],
                "reminder": "final",
            },
        }

    def test_the_event_has_a_subscriber_at_all(self):
        """The bug, stated as a test. This is the assertion whose absence
        let a feature ship doing nothing."""
        from app.gateway.event_bus import bus

        assert bus._subscribers.get("recovery.promise_lapsed"), (
            "recovery.promise_lapsed must have a subscriber - publishing it to nobody "
            "means the one reminder a lapsed promise earns is never sent"
        )

    async def test_a_lapsed_promise_produces_a_real_email(self, lapsed_case, sent):
        from app.services.email_service import EmailService

        await EmailService.handle_promise_lapsed(self._event(lapsed_case))

        assert len(sent) == 1
        assert sent[0]["to_email"] == "promiser@example.com"
        # The terms they promised against, not the cart total - reminding
        # someone of a number nobody agreed to is its own small betrayal.
        assert sent[0]["final_amount"] == 2250.0
        assert sent[0]["link_url"] == "https://rzp.io/i/promise_test"

    async def test_it_refuses_after_an_opt_out_on_any_channel(self, lapsed_case, sent):
        """They said "don't contact me again" on the phone. They did not
        mean "except by email", and the reminder arrives days later when
        that distinction is easiest to lose."""
        from app.db.repositories import consents as consents_repo
        from app.services.email_service import EmailService

        consents_repo.record_consent(
            lapsed_case["merchant_id"], lapsed_case["customer_id"], "voice", "revoked", source="test"
        )
        await EmailService.handle_promise_lapsed(self._event(lapsed_case))
        assert sent == []

    async def test_it_refuses_once_they_have_paid(self, lapsed_case, sent):
        """The sweeper's query already excludes paid carts. This is the
        last moment before an email leaves, and a payment landing in
        between is exactly the race worth losing safely."""
        from app.services.email_service import EmailService

        checkouts_repo.mark_paid(lapsed_case["checkout_id"], rzp_payment_id="pay_test_promise")
        await EmailService.handle_promise_lapsed(self._event(lapsed_case))
        assert sent == []

    async def test_it_sends_nothing_when_there_is_no_link_to_remind_them_of(
        self, lapsed_case, sent
    ):
        """An email that asks for money with no way to pay it is worse
        than silence."""
        from app.services.email_service import EmailService

        ra_repo.update_state(lapsed_case["recovery_attempt_id"], "PROMISED", rzp_payment_link_url=None)
        await EmailService.handle_promise_lapsed(self._event(lapsed_case))
        assert sent == []


class TestThePromiseRemembersItsTerms:
    async def test_the_offer_token_is_stored_with_the_promise(
        self, real_merchant_id, unique_checkout_id
    ):
        """A promise recorded with only a date loses the terms it was a
        promise about, so a reminder days later cannot say what was
        actually agreed."""
        checkouts_repo.create_checkout(real_merchant_id, amount_paise=250_000, checkout_id=unique_checkout_id)
        attempt = ra_repo.create_recovery_attempt(real_merchant_id, unique_checkout_id, None)
        ctx = AgentContext(
            merchant_id=real_merchant_id, correlation_id=unique_checkout_id,
            checkout_id=unique_checkout_id, recovery_attempt_id=attempt["recovery_attempt_id"],
        )

        result = await record_promise_to_pay.fn(
            ctx, pay_date="tomorrow", amount_inr=2250.0,
            customer_words="I'll pay tomorrow once my salary lands",
            offer_token="tok_test_promise",
        )
        assert result["status"] == "RECORDED"

        row = ra_repo.get_recovery_attempt(attempt["recovery_attempt_id"])
        assert row["promised_offer_token"] == "tok_test_promise"

    async def test_a_promise_with_no_offer_discussed_is_still_valid(
        self, real_merchant_id, unique_checkout_id
    ):
        """Most promises are made at full price, with no offer at all. The
        token is optional and its absence must not refuse the promise."""
        checkouts_repo.create_checkout(real_merchant_id, amount_paise=250_000, checkout_id=unique_checkout_id)
        attempt = ra_repo.create_recovery_attempt(real_merchant_id, unique_checkout_id, None)
        ctx = AgentContext(
            merchant_id=real_merchant_id, correlation_id=unique_checkout_id,
            checkout_id=unique_checkout_id, recovery_attempt_id=attempt["recovery_attempt_id"],
        )
        result = await record_promise_to_pay.fn(ctx, pay_date="tomorrow")
        assert result["status"] == "RECORDED"
        assert ra_repo.get_recovery_attempt(attempt["recovery_attempt_id"])["promised_offer_token"] is None
