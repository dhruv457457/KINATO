"""Negotiating is not the same as capitulating.

`offer_ladder` has been in the schema since it was written, defaulting to
[3,7,10]. It is deserialised by policies.py. It is handed to the model by
get_policy_limits. And nothing enforced it - so the engine answered every
over-ask with min(requested, ceiling), and a model asking for 40% received
the merchant's FULL CEILING in the first sentence of the negotiation.

That is not a negotiation, it is a vending machine. Every discounted
recovery cost the maximum the merchant had authorised, whether or not the
customer would have happily taken 3%.

It is the fifth dead policy column this codebase has found (FINDINGS #4 has
the other four, and `auto_approval_threshold_inr` was a sixth), and it is
the most expensive of them, because the others merely failed to constrain
anything - this one silently maximised what was given away.

The rung is chosen by how many offers the cart has ALREADY been quoted, so
a customer who turns down 3% can be met at 7% and then at 10%. What the
ladder must never do is loosen anything: it is a negotiation aid layered on
top of the ceiling and the margin floor, never a way past either.
"""
import pytest

from app.services.policy_engine import policy_engine


POLICY = {
    "max_discount_percent": 10.0,
    "minimum_margin_percent": 15.0,
    "offer_ladder": [3, 7, 10],
    "excluded_products": [],
}
CART = {"amount": 1290.0, "cogs": 500.0, "product_ids": []}


def approve(asked, concessions=0, policy=None, cart=None):
    return policy_engine.evaluate(
        asked, policy or POLICY, {"concessions_made": concessions}, cart or CART
    )


class TestTheLadderIsClimbedNotSkipped:
    def test_the_first_offer_opens_at_the_bottom_rung(self):
        """The bug, stated as a test: asking 40% used to return 10%."""
        assert approve(40, concessions=0)["approved_discount"] == 3.0

    @pytest.mark.parametrize("concessions,expected", [(0, 3.0), (1, 7.0), (2, 10.0)])
    def test_each_refusal_earns_the_next_rung(self, concessions, expected):
        assert approve(40, concessions=concessions)["approved_discount"] == expected

    def test_the_ladder_does_not_keep_climbing_past_its_top(self):
        for concessions in (3, 4, 10, 99):
            assert approve(40, concessions=concessions)["approved_discount"] == 10.0

    def test_the_step_is_named_in_the_refusal(self):
        """A merchant seeing a reduced offer is entitled to know which limit
        bound - the ceiling, the margin floor, or the negotiation step."""
        assert approve(40, concessions=0)["reason"] == "REJECTED_LADDER_STEP"


class TestTheLadderNeverLoosensAnything:
    def test_a_smaller_request_is_never_inflated_to_the_rung(self):
        """The ladder caps what may be given. It never decides to give more
        than the model asked for."""
        d = approve(2, concessions=2)
        assert d["decision"] == "ALLOW"
        assert d["approved_discount"] == 2

    def test_the_ceiling_still_binds_above_the_ladder(self):
        loose = {**POLICY, "offer_ladder": [3, 7, 50]}
        d = approve(40, concessions=2, policy=loose)
        assert d["approved_discount"] == 10.0
        assert d["reason"] == "REJECTED_CEILING"

    def test_the_margin_floor_still_binds(self):
        """A cart with almost no margin gets nothing, whatever rung it is
        on. This is the limit that protects the merchant from selling at a
        loss and it outranks the negotiation entirely."""
        thin = {"amount": 1000.0, "cogs": 900.0, "product_ids": []}
        d = approve(40, concessions=2, cart=thin)
        assert d["decision"] == "DENY"
        assert d["reason"] == "REJECTED_MARGIN_FLOOR"


class TestAMalformedLadderCannotBreakTheProduct:
    """It is a negotiation aid, not a safety limit. The ceiling and the
    margin floor must never fail open; this may fail *out of the way*."""

    @pytest.mark.parametrize("bad", [[], None, "not a list", [None], ["seven"], {}])
    def test_an_unusable_ladder_falls_back_to_the_ceiling(self, bad):
        d = policy_engine.evaluate(40, {**POLICY, "offer_ladder": bad}, {}, CART)
        assert d["approved_discount"] == 10.0

    def test_a_missing_concession_count_opens_at_the_bottom(self):
        """No context means this is the first thing we have said to them."""
        d = policy_engine.evaluate(40, POLICY, {}, CART)
        assert d["approved_discount"] == 3.0

    def test_a_nonsense_concession_count_does_not_crash_or_leap(self):
        for junk in (-5, "two", None):
            d = policy_engine.evaluate(40, POLICY, {"concessions_made": junk}, CART)
            assert 0 <= d["approved_discount"] <= 10.0
