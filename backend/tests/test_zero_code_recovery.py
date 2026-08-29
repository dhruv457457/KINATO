"""
The path the README leads with, which recovered nothing.

    "Setup is one webhook URL. No code on the storefront."

That is the product's headline pitch, and it did this: `payment.failed`
arrived, the customer was created from Razorpay's own payload, **no consent
was recorded**, and the eligibility gate then refused them - silently,
without publishing `recovery.blocked`, unlike the two branches directly
above it. A merchant following the recommended integration saw revenue at
risk, zero recovery attempts against it, and nothing anywhere explaining
why.

Every test in this file failed before the fix. The last two matter most,
because they are the ways the fix itself could be worse than the bug:
consent must never be resurrected for someone who opted out, and opting out
must stop every channel rather than the one they happened to say it on.
"""
import hashlib
import hmac
import json
import uuid

import httpx
import pytest

from app.main import app
from app.agents.state import AgentContext
from app.agents.tools import _record_opt_out
from app.core.crypto import encrypt_secret
from app.db.database import get_db
from app.db.repositories import checkouts as checkouts_repo
from app.db.repositories import consents as consents_repo
from app.db.repositories import customers as customers_repo
from app.gateway.event_bus import bus
from app.services.identity_service import identity_service

from tests.conftest import wait_until


@pytest.fixture
def webhook_merchant(real_merchant_id):
    """A merchant with a configured webhook secret - the state a merchant
    is in after pasting one URL into their Razorpay dashboard, and nothing
    else."""
    secret = "whsec_test_not_a_real_secret"
    with get_db() as conn:
        conn.cursor().execute(
            "UPDATE merchants SET rzp_webhook_secret_enc = %s WHERE merchant_id = %s",
            (encrypt_secret(secret), real_merchant_id),
        )
    return real_merchant_id, secret


async def _send_payment_failed(merchant_id, secret, *, email, contact, payment_id=None):
    payment_id = payment_id or f"pay_test_{uuid.uuid4().hex[:10]}"
    body = json.dumps(
        {
            "event": "payment.failed",
            "payload": {
                "payment": {
                    "entity": {
                        "id": payment_id,
                        "amount": 249900,
                        "email": email,
                        "contact": contact,
                        "method": "card",
                        "error_code": "BAD_REQUEST_ERROR",
                        "error_reason": "payment_failed",
                        "error_description": "Issuer bank timed out",
                        "error_source": "bank",
                        "error_step": "payment",
                    }
                }
            },
        }
    ).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/webhooks/razorpay/{merchant_id}",
            content=body,
            headers={"X-Razorpay-Signature": signature, "content-type": "application/json"},
        )
    assert resp.status_code == 200
    return f"chk_wh_{payment_id}"


class TestTheZeroCodePathActuallyRecovers:
    async def test_a_failed_payment_alone_is_enough_to_start_a_recovery(self, webhook_merchant):
        """The whole pitch, as one assertion. Before the fix this produced
        a customer, a checkout, and then nothing at all."""
        merchant_id, secret = webhook_merchant
        checkout_id = await _send_payment_failed(
            merchant_id, secret, email="zero.code@example.com", contact="+919000000042"
        )

        started = await wait_until(
            lambda: any(
                e["event_type"] == "recovery.opportunity.created"
                and e["payload"].get("checkout_id") == checkout_id
                for e in bus._event_log
            )
        )
        assert started, (
            "a payment.failed carrying an email and a phone must be enough to start a "
            "recovery - this is the integration the README recommends"
        )

    async def test_consent_is_recorded_for_the_channels_we_can_actually_reach(
        self, webhook_merchant
    ):
        merchant_id, secret = webhook_merchant
        await _send_payment_failed(
            merchant_id, secret, email="both@example.com", contact="+919000000043"
        )
        customer = customers_repo.upsert_by_contact(merchant_id, email="both@example.com")
        cid = customer["customer_id"]

        assert consents_repo.check_consent(merchant_id, cid, "email") is True
        assert consents_repo.check_consent(merchant_id, cid, "voice") is True

    async def test_no_phone_means_no_voice_consent(self, webhook_merchant):
        """A granted row for a channel we cannot reach them on would only
        ever produce a failed dial."""
        merchant_id, secret = webhook_merchant
        await _send_payment_failed(merchant_id, secret, email="mailonly@example.com", contact="")
        customer = customers_repo.upsert_by_contact(merchant_id, email="mailonly@example.com")
        cid = customer["customer_id"]

        assert consents_repo.check_consent(merchant_id, cid, "email") is True
        assert consents_repo.check_consent(merchant_id, cid, "voice") is False

    async def test_a_customer_with_only_an_email_is_still_eligible(self, webhook_merchant):
        """The gate asked only about voice, so someone with an email
        address and no phone number was refused outright - not "recover
        them by email instead", refused."""
        merchant_id, secret = webhook_merchant
        checkout_id = await _send_payment_failed(
            merchant_id, secret, email="phoneless@example.com", contact=""
        )

        started = await wait_until(
            lambda: any(
                e["event_type"] == "recovery.opportunity.created"
                and e["payload"].get("checkout_id") == checkout_id
                for e in bus._event_log
            )
        )
        assert started, "a contactable customer must not be refused for lacking a phone number"


class TestARefusalIsNeverSilent:
    async def test_no_consent_publishes_a_visible_reason(self, real_merchant_id):
        """The bug this file is really about. The branch returned without
        publishing anything, so a merchant saw revenue at risk and zero
        attempts, with no explanation anywhere - the same silent-failure
        shape as FINDINGS #3."""
        customer = customers_repo.upsert_by_contact(
            real_merchant_id, email="norecord@example.com", name="No Consent"
        )
        checkout = checkouts_repo.create_checkout(
            real_merchant_id, amount_paise=150_000, customer_id=customer["customer_id"]
        )

        await bus.publish(
            event_type="checkout.payment_failed",
            payload={
                "checkout_id": checkout["checkout_id"],
                "customer_id": customer["customer_id"],
                "amount": 1500.0,
            },
            correlation_id=checkout["checkout_id"],
            merchant_id=real_merchant_id,
        )

        blocked = await wait_until(
            lambda: any(
                e["event_type"] == "recovery.blocked"
                and e["payload"].get("reason") == "no_consent"
                for e in bus._event_log
            )
        )
        assert blocked, "a refusal a merchant cannot see is indistinguishable from a bug"


class TestTheFixCannotBecomeWorseThanTheBug:
    async def test_a_failed_payment_never_resurrects_an_opt_out(self, webhook_merchant):
        """The worst thing this change could do.

        The consent ledger is append-only and the LATEST row wins, so
        blindly inserting a grant would bring an opted-out customer back to
        life - and every subsequent failed payment would do it again.
        Someone who asked us to stop would be re-enrolled by the very act
        of trying to pay us.
        """
        merchant_id, secret = webhook_merchant
        customer = customers_repo.upsert_by_contact(
            merchant_id, email="optedout@example.com", phone="+919000000044"
        )
        cid = customer["customer_id"]
        consents_repo.record_consent(merchant_id, cid, "voice", "revoked", source="test_opt_out")
        consents_repo.record_consent(merchant_id, cid, "email", "revoked", source="test_opt_out")

        await _send_payment_failed(
            merchant_id, secret, email="optedout@example.com", contact="+919000000044"
        )

        assert consents_repo.check_consent(merchant_id, cid, "voice") is False
        assert consents_repo.check_consent(merchant_id, cid, "email") is False
        assert consents_repo.has_opted_out(merchant_id, cid) is True

    async def test_opting_out_on_the_phone_also_stops_the_email(self, real_merchant_id):
        """They did not ask to stop being phoned. They asked to stop being
        contacted, and nobody should have to opt out once per protocol."""
        customer = customers_repo.upsert_by_contact(
            real_merchant_id, email="stopall@example.com", phone="+919000000045"
        )
        cid = customer["customer_id"]
        await identity_service.grant_transactional_consent(
            real_merchant_id, cid, email="stopall@example.com", phone="+919000000045"
        )
        assert consents_repo.check_consent(real_merchant_id, cid, "email") is True

        ctx = AgentContext(merchant_id=real_merchant_id, correlation_id="test", customer_id=cid)
        result = await _record_opt_out(ctx)
        assert result["status"] == "RECORDED"

        assert consents_repo.check_consent(real_merchant_id, cid, "voice") is False
        assert consents_repo.check_consent(real_merchant_id, cid, "email") is False

    async def test_granting_twice_does_not_fill_the_ledger_with_duplicates(
        self, real_merchant_id
    ):
        """Razorpay retries webhooks. A merchant's consent ledger is
        evidence, and evidence padded with identical rows is harder to read
        rather than more convincing."""
        customer = customers_repo.upsert_by_contact(
            real_merchant_id, email="twice@example.com", phone="+919000000046"
        )
        cid = customer["customer_id"]

        first = await identity_service.grant_transactional_consent(
            real_merchant_id, cid, email="twice@example.com", phone="+919000000046"
        )
        second = await identity_service.grant_transactional_consent(
            real_merchant_id, cid, email="twice@example.com", phone="+919000000046"
        )
        assert sorted(first) == ["email", "voice"]
        assert second == [], "consent already granted must not be re-recorded"

    async def test_it_is_recorded_as_transactional_not_as_an_explicit_opt_in(
        self, real_merchant_id
    ):
        """A merchant must be able to tell the two apart in the ledger.
        Implied consent from a failed checkout is defensible; quietly
        filing it as though the customer ticked a box is not."""
        customer = customers_repo.upsert_by_contact(
            real_merchant_id, email="source@example.com", phone="+919000000047"
        )
        cid = customer["customer_id"]
        await identity_service.grant_transactional_consent(
            real_merchant_id, cid, email="source@example.com", phone="+919000000047"
        )

        history = consents_repo.get_consent_history(real_merchant_id, cid)
        assert history
        assert all(row["source"] == "razorpay_transactional" for row in history)


class TestOneCartIsOneRecovery:
    """Observed in production. The SDK tracked a cart as
    order_TVd3XEkPFeE9VI; the webhook then arrived carrying that same order
    id, looked only at notes.checkout_id, found nothing, and opened
    chk_wh_pay_TVd3g0Qckljb1S beside it. Two checkouts, two recovery
    attempts, and two phone calls to one person about one order.
    """

    async def test_the_webhook_reuses_a_cart_the_sdk_already_tracked(
        self, webhook_merchant
    ):
        merchant_id, secret = webhook_merchant
        order_id = f"order_{uuid.uuid4().hex[:12]}"

        # What the SDK does first, on the storefront.
        checkouts_repo.create_checkout(
            merchant_id, amount_paise=549_000, checkout_id=order_id, source="sdk"
        )

        payment_id = f"pay_{uuid.uuid4().hex[:10]}"
        body = json.dumps(
            {
                "event": "payment.failed",
                "payload": {
                    "payment": {
                        "entity": {
                            "id": payment_id,
                            "order_id": order_id,
                            "amount": 549000,
                            "email": "one.cart@example.com",
                            "contact": "+919000000077",
                            "method": "card",
                            "error_reason": "payment_failed",
                            "error_step": "payment",
                        }
                    }
                },
            }
        ).encode()
        signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/webhooks/razorpay/{merchant_id}",
                content=body,
                headers={"X-Razorpay-Signature": signature, "content-type": "application/json"},
            )
        assert resp.status_code == 200

        assert checkouts_repo.get_checkout(f"chk_wh_{payment_id}") is None, (
            "the webhook opened a second checkout for a cart the SDK had already tracked"
        )
        matched = checkouts_repo.get_checkout(order_id)
        assert matched is not None
        assert matched["failure_class"], "the failure should be recorded on the EXISTING cart"

    async def test_an_untracked_cart_still_gets_one(self, webhook_merchant):
        """The zero-code path must keep working: nothing on the storefront
        told us about this cart, so the webhook is the only thing that can
        open it."""
        merchant_id, secret = webhook_merchant
        checkout_id = await _send_payment_failed(
            merchant_id, secret, email="untracked@example.com", contact="+919000000078"
        )
        assert checkouts_repo.get_checkout(checkout_id) is not None

    def test_lookup_matches_on_either_column(self, real_merchant_id):
        """Which column holds the order id depends on how the merchant
        integrated, and a customer should not be called twice over that."""
        from app.db.database import get_db

        order_id = f"order_{uuid.uuid4().hex[:12]}"
        checkouts_repo.create_checkout(real_merchant_id, amount_paise=1000, checkout_id=order_id)
        assert checkouts_repo.find_by_order_id(real_merchant_id, order_id) is not None

        other = checkouts_repo.create_checkout(real_merchant_id, amount_paise=1000)
        rzp_id = f"order_{uuid.uuid4().hex[:12]}"
        with get_db() as conn:
            conn.cursor().execute(
                "UPDATE checkouts SET rzp_order_id = %s WHERE checkout_id = %s",
                (rzp_id, other["checkout_id"]),
            )
        assert checkouts_repo.find_by_order_id(real_merchant_id, rzp_id) is not None
        assert checkouts_repo.find_by_order_id(real_merchant_id, "") is None
