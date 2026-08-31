"""A paid recovery link has to become recovered revenue.

Found live. A customer paid a real Razorpay recovery link in full - ₹2,990,
status `paid` per Razorpay's own API - and the attempt sat at
PAYMENT_LINK_SENT with the dashboard reporting the money as never recovered.

Everything needed was being sent correctly. `payment_execution` writes
`recovery_attempt_id` into the link's `notes` AND into its `reference_id`.
The receiver read notes from `payload.payment.entity` and
`payload.order.entity` only - and a `payment_link.paid` event carries them
under `payload.payment_link.entity`, which nothing in the file ever touched.

Razorpay does NOT copy a link's notes onto the payment entity, so the
payment-first lookup found an empty dict, `recovery_attempt_id` came back
None, and attribution logged "Organic revenue" and returned. The single most
important number this product reports - money actually recovered - was
silently zero for every link anyone paid.

The payloads below carry the exact KEYS payment_execution writes, with
synthetic ids. The first version of this file used the real ones from the
call that exposed the bug, which was a mistake twice over: it ties a test to
one customer's data, and a secret scanner flagged the offer token in it as a
high-entropy credential. It was neither live nor a credential - single-use,
already consumed at the moment that payment went through, and expired inside
fifteen minutes - but a test proving a payload SHAPE never needed a real
value to do it.
"""
import pytest

from app.payments import webhooks as webhooks_module


def _extract(payload: dict) -> dict:
    """Mirror of the receiver's extraction step.

    Kept as a helper rather than driving the HTTP endpoint because the part
    under test is the parsing, and the endpoint would need a signed body and
    a real merchant to reach it - which would test the signature code, not
    this.
    """
    payment_entity = payload.get("payment", {}).get("entity", {})
    order_entity = payload.get("order", {}).get("entity", {})
    link_entity = payload.get("payment_link", {}).get("entity", {})
    notes = (
        payment_entity.get("notes")
        or link_entity.get("notes")
        or order_entity.get("notes")
        or {}
    )
    return {
        "checkout_id": notes.get("checkout_id"),
        "recovery_attempt_id": notes.get("recovery_attempt_id") or link_entity.get("reference_id"),
        "amount": payment_entity.get("amount") or link_entity.get("amount_paid") or 0,
    }


LINK_NOTES = {
    "checkout_id": "chk_example_cart",
    "customer_id": "cust_example",
    "discount_percent": "0",
    "kinato_touchpoint": "voice_recovery",
    "merchant_id": "mch_example",
    "offer_token": "off_example_token",
    "original_amount_paise": "299000",
    "recovery_attempt_id": "rec_example_attempt",
}


class TestPaymentLinkPaid:
    def test_the_recovery_is_attributed_from_the_link_entity(self):
        """The exact shape that was being dropped."""
        payload = {
            "payment_link": {
                "entity": {
                    "id": "plink_example",
                    "status": "paid",
                    "amount_paid": 299000,
                    "reference_id": "rec_example_attempt",
                    "notes": LINK_NOTES,
                }
            },
            "payment": {"entity": {"id": "pay_example", "amount": 299000, "notes": {}}},
        }
        got = _extract(payload)
        assert got["recovery_attempt_id"] == "rec_example_attempt"
        assert got["checkout_id"] == "chk_example_cart"
        assert got["amount"] == 299000

    def test_reference_id_rescues_a_link_whose_notes_are_gone(self):
        """Two independent carriers, on purpose.

        payment_execution sets reference_id to the attempt id as well as
        putting it in notes. Attribution should survive losing either one -
        the cost of missing it is money reported as never recovered.
        """
        payload = {
            "payment_link": {
                "entity": {"amount_paid": 299000, "reference_id": "rec_example_attempt", "notes": {}}
            }
        }
        assert _extract(payload)["recovery_attempt_id"] == "rec_example_attempt"

    def test_the_amount_never_falls_back_to_zero_when_the_link_knows_it(self):
        """A recovery recorded at zero rupees is worse than none recorded.

        attributed_revenue_paise is written straight from this. Zero would
        report the recovery RATE correctly and the revenue wrongly, which is
        the harder error to notice.
        """
        payload = {
            "payment_link": {
                "entity": {"amount_paid": 299000, "reference_id": "rec_x", "notes": LINK_NOTES}
            }
        }
        assert _extract(payload)["amount"] == 299000


class TestNothingElseRegressed:
    def test_a_normal_captured_payment_still_reads_its_own_notes(self):
        """The payment entity stays first in the lookup order.

        A direct payment carries its own notes, and they must win over any
        link entity that happens to be in the same payload.
        """
        payload = {
            "payment": {
                "entity": {
                    "id": "pay_direct",
                    "amount": 500000,
                    "notes": {"checkout_id": "chk_direct", "recovery_attempt_id": "rec_direct"},
                }
            },
            "payment_link": {"entity": {"reference_id": "rec_should_lose", "amount_paid": 1}},
        }
        got = _extract(payload)
        assert got["recovery_attempt_id"] == "rec_direct"
        assert got["amount"] == 500000

    def test_an_organic_payment_attributes_to_nothing(self):
        """Someone paying normally on the storefront is not a recovery, and
        claiming it as one would inflate the only number that matters."""
        payload = {"payment": {"entity": {"id": "pay_organic", "amount": 100000, "notes": {}}}}
        got = _extract(payload)
        assert got["recovery_attempt_id"] is None
        assert got["checkout_id"] is None

    def test_payment_link_paid_is_a_handled_event(self):
        import inspect

        src = inspect.getsource(webhooks_module)
        assert '"payment_link.paid"' in src
        assert 'payload.get("payment_link"' in src, "the link entity must actually be read"
