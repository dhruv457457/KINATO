"""
Diagnosing from Razorpay's failure object rather than from speech.

The rule under test is one this project has believed since FINDINGS #1:

    a declined card is a broken checkout, not a price objection

It has been true and it has been enforced by nothing except a paragraph in
the agent's system prompt. These tests cover it as a mechanism: a
deterministic classifier over the fields Razorpay has always sent, and a
`check_offer` that refuses a discount on a payment that broke.

The most important test in this file is the LAST one. "Full price first" is
not "full price forever" - the correction to FINDINGS #1 overshot once
already, sending full price to customers who had explicitly said the price
was too high, and both directions lose money.
"""
import hashlib
import hmac
import json
import uuid

import httpx
import pytest

from app.main import app
from app.agents.state import AgentContext
from app.agents import tools as tools_module
from app.core.crypto import encrypt_secret
from app.db.database import get_db
from app.db.repositories import checkouts as checkouts_repo
from app.services import failure_diagnosis
from app.services.failure_diagnosis import (
    AUTH_DROP,
    HARD_DECLINE,
    INSUFFICIENT_FUNDS,
    RAIL_DOWN,
    SOFT_DECLINE,
    UNKNOWN,
    USER_ABANDON,
    describe,
    diagnose,
)


class TestClassifier:
    """Pure, no I/O. Given the same failure object, forever the same class."""

    def test_no_failure_object_at_all_is_a_walk_away_not_a_decline(self):
        assert diagnose(None).failure_class == USER_ABANDON
        assert diagnose({}).failure_class == USER_ABANDON

    def test_abandoned_bank_verification_is_its_own_class(self):
        """Checked before the decline markers on purpose: a 3DS drop often
        carries a generic decline-ish description while being a completely
        different situation - nothing was declined, they just never
        finished the bank's own step."""
        d = diagnose({"error_step": "payment_authentication", "error_code": "BAD_REQUEST_ERROR"})
        assert d.failure_class == AUTH_DROP

    def test_insufficient_funds_is_the_one_decline_where_price_may_be_real(self):
        d = diagnose({"error_reason": "insufficient_funds", "error_step": "payment"})
        assert d.failure_class == INSUFFICIENT_FUNDS

    def test_stolen_card_is_a_hard_decline_and_must_not_be_retried(self):
        d = diagnose({"error_reason": "payment_pickup_card", "error_step": "payment"})
        assert d.failure_class == HARD_DECLINE
        assert d.retry_same_instrument is False

    def test_bank_timeout_is_a_soft_decline(self):
        d = diagnose({"error_description": "Issuer bank timed out, please try again", "error_step": "payment"})
        assert d.failure_class == SOFT_DECLINE
        assert d.retry_same_instrument is True

    def test_an_unplaceable_decline_is_read_as_soft_not_unknown(self):
        """Something demonstrably broke in the payment. The safe reading of
        "a payment broke" is that the customer still wants to pay - UNKNOWN
        is reserved for having no failure information at all, which is the
        only case where price genuinely might be the issue."""
        d = diagnose({"error_step": "payment", "error_code": "GATEWAY_ERROR"})
        assert d.failure_class == SOFT_DECLINE

    def test_it_reads_every_field_not_just_the_one_cards_happen_to_use(self):
        """Which field carries the useful words differs by payment method.
        A classifier that reads only error_reason returns UNKNOWN with
        total confidence on a UPI failure."""
        assert diagnose({"error_description": "Payment failed: insufficient balance"}).failure_class == INSUFFICIENT_FUNDS
        assert diagnose({"error_code": "insufficient_funds"}).failure_class == INSUFFICIENT_FUNDS

    def test_an_outage_outranks_whatever_the_payment_object_says(self):
        """A failure during a Razorpay outage tells us about Razorpay, not
        about the customer. Calling them about it blames them for our own
        outage."""
        d = diagnose({"error_reason": "payment_pickup_card"}, rail_degraded=True)
        assert d.failure_class == RAIL_DOWN

    def test_unrecognised_shapes_degrade_to_unknown_rather_than_guessing(self):
        d = diagnose({"error_source": "customer"})
        assert d.failure_class == UNKNOWN

    def test_the_model_gets_a_sentence_never_the_raw_object(self):
        line = failure_diagnosis.describe(SOFT_DECLINE)
        assert "full amount" in line
        assert "{" not in line and "error_code" not in line

    def test_nothing_useful_to_say_says_nothing(self):
        assert failure_diagnosis.describe(None) == ""
        assert failure_diagnosis.describe(UNKNOWN) == ""


class TestCheckOfferRefusesToDiscountABrokenCheckout:
    @pytest.fixture
    def case(self, connected_merchant_id, unique_checkout_id):
        checkouts_repo.create_checkout(
            merchant_id=connected_merchant_id,
            amount_paise=249_900,
            cogs_paise=100_000,
            checkout_id=unique_checkout_id,
            line_items=[{"product_id": "sku_1", "name": "Woven Table Runner"}],
        )
        return {"merchant_id": connected_merchant_id, "checkout_id": unique_checkout_id}

    def _ctx(self, case, **overrides):
        base = dict(
            merchant_id=case["merchant_id"],
            correlation_id="test",
            checkout_id=case["checkout_id"],
        )
        base.update(overrides)
        return AgentContext(**base)

    @pytest.mark.parametrize("failure_class", [SOFT_DECLINE, HARD_DECLINE, AUTH_DROP])
    async def test_a_discount_is_refused_when_the_payment_simply_broke(self, case, failure_class):
        ctx = self._ctx(case, failure_class=failure_class)
        result = await tools_module._check_offer(ctx, requested_discount_percent=20, reason="guessing")
        assert result["decision"] == "DENY"
        assert result["reason"] == "REJECTED_FULL_PRICE_FIRST"

    @pytest.mark.parametrize("failure_class", [SOFT_DECLINE, HARD_DECLINE, AUTH_DROP])
    async def test_full_price_always_goes_through(self, case, failure_class):
        """The customer wants to pay. Blocking the full-price link would be
        a far worse bug than the one this gate prevents."""
        ctx = self._ctx(case, failure_class=failure_class)
        result = await tools_module._check_offer(ctx, requested_discount_percent=0, reason="broken checkout")
        assert result["decision"] in ("ALLOW", "MODIFY")
        assert result["approved_percent"] == 0
        assert result["offer_token"]

    async def test_insufficient_funds_may_still_be_negotiated(self, case):
        """This is the one decline where money genuinely might be the
        barrier, so the agent must not be locked out of it."""
        ctx = self._ctx(case, failure_class=INSUFFICIENT_FUNDS)
        result = await tools_module._check_offer(ctx, requested_discount_percent=5, reason="they said it's tight")
        assert result["decision"] in ("ALLOW", "MODIFY")

    async def test_an_abandoned_cart_is_not_gated_at_all(self, case):
        ctx = self._ctx(case, failure_class=USER_ABANDON)
        result = await tools_module._check_offer(ctx, requested_discount_percent=5, reason="price")
        assert result["decision"] in ("ALLOW", "MODIFY")

    async def test_full_price_FIRST_is_not_full_price_FOREVER(self, case):
        """The most important test in this file.

        FINDINGS #1's correction overshot once already: after being taught
        not to discount a won sale, the agent began sending full price to
        customers who had explicitly said the price was too high. Both
        directions lose money. Once the customer confirms the barrier is
        price, the discount must become available even on a card decline.
        """
        blocked = self._ctx(case, failure_class=SOFT_DECLINE, barrier_confirmed=False)
        assert (await tools_module._check_offer(blocked, 15, "price"))["reason"] == "REJECTED_FULL_PRICE_FIRST"

        confirmed = self._ctx(case, failure_class=SOFT_DECLINE, barrier_confirmed=True)
        result = await tools_module._check_offer(confirmed, 15, "they said it's too expensive")
        assert result["decision"] in ("ALLOW", "MODIFY")
        assert result["approved_percent"] > 0


class TestTheWebhookRecordsWhatItWasTold:
    @pytest.fixture
    def merchant_with_webhook_secret(self, real_merchant_id):
        secret = "whsec_test_not_a_real_secret"
        with get_db() as conn:
            conn.cursor().execute(
                "UPDATE merchants SET rzp_webhook_secret_enc = %s WHERE merchant_id = %s",
                (encrypt_secret(secret), real_merchant_id),
            )
        return real_merchant_id, secret

    async def test_the_whole_failure_object_is_persisted_and_classified(
        self, merchant_with_webhook_secret
    ):
        """Razorpay has always sent all of this. Only error_reason ever
        reached the event bus, and nothing read even that."""
        merchant_id, secret = merchant_with_webhook_secret
        payment_id = f"pay_test_{uuid.uuid4().hex[:10]}"
        body = json.dumps(
            {
                "event": "payment.failed",
                "payload": {
                    "payment": {
                        "entity": {
                            "id": payment_id,
                            "amount": 249900,
                            "email": "someone@example.com",
                            "contact": "+919000000001",
                            "method": "card",
                            "error_code": "BAD_REQUEST_ERROR",
                            "error_reason": "payment_authentication_failed",
                            "error_description": "Customer did not complete verification",
                            "error_source": "customer",
                            "error_step": "payment_authentication",
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

        checkout = checkouts_repo.get_checkout(f"chk_wh_{payment_id}")
        assert checkout is not None
        assert checkout["error_code"] == "BAD_REQUEST_ERROR"
        assert checkout["error_step"] == "payment_authentication"
        assert checkout["payment_method"] == "card"
        # The point of storing it: a second recovery attempt days later
        # diagnoses from the same evidence instead of guessing again.
        assert checkout["failure_class"] == AUTH_DROP


class TestCardFailuresNameTheRailThatWorks:
    """A card that failed is a rail problem, and India has another rail.

    "Let them pay another way" is advice the agent cannot act on: it does
    not name anything, so the model either invents an option or says
    nothing. UPI is the specific answer, and for two of these classes it is
    not merely an alternative but a strictly better one - a hard decline
    means that card will never work, and an auth drop means the customer
    reached 3DS and bailed, which UPI skips entirely.

    This is the honest half of "smart payment retry". A silent server-side
    re-charge is not possible for a one-time cart payment without a saved
    mandate, so the recoverable thing is not retrying the same rail - it is
    naming a different one.
    """

    @pytest.mark.parametrize("failure_class", [SOFT_DECLINE, HARD_DECLINE, AUTH_DROP])
    def test_a_card_failure_tells_the_agent_about_upi(self, failure_class):
        assert "UPI" in describe(failure_class), (
            f"{failure_class} leaves the agent with no rail to suggest"
        )

    def test_a_hard_decline_says_the_card_will_not_work_again(self):
        """The one class where re-sending the same link in silence is
        actively unhelpful - the customer would fail on the same card
        twice."""
        line = describe(HARD_DECLINE)
        assert "NOT work again" in line or "not work again" in line

    @pytest.mark.parametrize("failure_class", [USER_ABANDON, INSUFFICIENT_FUNDS])
    def test_non_card_failures_do_not_push_a_rail(self, failure_class):
        """Nobody's card failed here.

        Suggesting a different payment method to someone who walked away, or
        to someone who has no money in the account today, answers a question
        they did not ask - and for INSUFFICIENT_FUNDS it talks past a real
        cashflow problem.
        """
        assert "UPI" not in describe(failure_class)


class TestInstalmentsBeforeDiscount:
    """The cheapest instrument the agent has, offered before the dearest one.

    "I can't afford that right now" is a CASHFLOW objection, and a discount
    is the most expensive possible answer to it - the merchant loses margin
    on a sale that EMI would have closed at full revenue. The policy engine
    knows exactly one instrument, a discount percent, so the cheaper one has
    to be reached for in what the agent SAYS before the expensive one is
    reached for in what it DOES.

    The gate on it is the point. EMI has to be enabled on the merchant's own
    Razorpay account, and an agent that offers instalments the checkout
    cannot provide has told a customer something untrue about their money -
    the failure this codebase keeps finding (#2, #19). Silence is the
    default; the merchant opts in.
    """

    def test_instalments_are_offered_when_the_merchant_has_emi(self):
        line = describe(INSUFFICIENT_FUNDS, emi_available=True)
        assert "EMI" in line or "instalment" in line
        # And explicitly ahead of a discount, not merely alongside one.
        assert "BEFORE any discount" in line

    def test_nothing_is_promised_when_the_merchant_has_no_emi(self):
        """The default, and the safe direction."""
        line = describe(INSUFFICIENT_FUNDS, emi_available=False)
        assert "EMI" not in line and "instalment" not in line
        assert describe(INSUFFICIENT_FUNDS) == line, "emi_available must default to False"

    @pytest.mark.parametrize(
        "failure_class", [SOFT_DECLINE, HARD_DECLINE, AUTH_DROP, USER_ABANDON, RAIL_DOWN]
    )
    def test_instalments_are_not_offered_to_anyone_else(self, failure_class):
        """Only the class where money is genuinely the barrier.

        A declined card is not a cashflow problem and a walk-away has said
        nothing about price. Offering either of them instalments answers a
        question they did not ask - the same restraint as not pushing UPI at
        someone who simply wandered off.
        """
        assert "EMI" not in describe(failure_class, emi_available=True)

    def test_an_unknown_class_still_says_nothing_at_all(self):
        assert describe(UNKNOWN, emi_available=True) == ""
        assert describe(None, emi_available=True) == ""
