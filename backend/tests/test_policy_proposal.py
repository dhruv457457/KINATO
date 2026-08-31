"""A merchant may describe their policy in words. A model may not write it.

The Policies page can now read "never discount more than 10% and don't call
before 9am" and turn it into numbers. That is a convenience, and it sits on
top of the one arrangement every guarantee in this project depends on:

    the model argues, a deterministic policy engine decides

A model that could WRITE the policy would be setting the ceiling it is later
bound by. `check_offer` would still refuse anything above the ceiling, the
ablation would still show NO_POLICY approving 60% - and none of it would mean
anything, because the ceiling itself would be model output. The guardrail
would have moved one level up and disappeared.

So the endpoint proposes and writes nothing. The merchant sees before ->
after and confirms, and the existing PUT applies it. These tests are about
that boundary, not about how well the model reads English.
"""
import pytest
from pydantic import ValidationError

from app.api.merchant_settings import (
    _PROPOSABLE_FIELDS,
    PolicyProposalRequest,
    PolicyUpdateRequest,
)


class TestTheProposalCannotExceedWhatAHumanCouldSet:
    """The model's output goes through the SAME validation as the sliders.

    Not a second set of bounds written beside it - the same one. Two copies
    of a money limit drift, and the copy that drifts is the one nobody is
    looking at.
    """

    @pytest.mark.parametrize(
        "field,value",
        [
            ("max_discount_percent", 500),
            ("max_discount_percent", -10),
            ("minimum_margin_percent", 101),
            ("calling_start_hour", 25),
            ("calling_end_hour", 99),
            ("auto_approval_threshold_inr", -1),
        ],
    )
    def test_an_out_of_range_value_is_rejected(self, field, value):
        """A hallucinated 500% ceiling has to fail here, not on the call."""
        with pytest.raises(ValidationError):
            PolicyUpdateRequest(**{field: value})

    def test_the_values_a_merchant_could_set_are_accepted(self):
        ok = PolicyUpdateRequest(
            max_discount_percent=10,
            minimum_margin_percent=15,
            calling_start_hour=9,
            calling_end_hour=21,
            auto_approval_threshold_inr=500,
            emi_available=True,
        )
        assert ok.max_discount_percent == 10
        assert ok.emi_available is True

    def test_round_the_clock_stays_reachable(self):
        """0-24 is the one shape that means 'any hour', and the end hour
        allows 24 only for that reason."""
        assert PolicyUpdateRequest(calling_start_hour=0, calling_end_hour=24).calling_end_hour == 24


class TestOnlyKnownFieldsAreProposable:
    def test_the_proposable_set_is_exactly_the_policy_fields(self):
        """A policy is the one object here where an unexpected key is a
        security question rather than a convenience, so the allowlist is
        asserted rather than assumed."""
        assert _PROPOSABLE_FIELDS == {
            "max_discount_percent",
            "minimum_margin_percent",
            "calling_start_hour",
            "calling_end_hour",
            "auto_approval_threshold_inr",
            "emi_available",
        }

    @pytest.mark.parametrize(
        "forbidden",
        ["merchant_id", "excluded_products", "offer_ladder", "free_shipping_allowed", "updated_at"],
    )
    def test_fields_a_model_must_not_touch_are_not_proposable(self, forbidden):
        """merchant_id is the sharp one: a proposal that could set it would
        let one merchant's instruction rewrite another's policy."""
        assert forbidden not in _PROPOSABLE_FIELDS

    def test_an_unknown_key_never_reaches_the_policy(self):
        """The endpoint filters to _PROPOSABLE_FIELDS before validating.

        This mirrors that filter, so the two cannot silently disagree about
        what a model is allowed to name.
        """
        model_output = {"max_discount_percent": 10, "merchant_id": "mch_someone_else", "is_admin": True}
        filtered = {k: v for k, v in model_output.items() if k in _PROPOSABLE_FIELDS}
        assert filtered == {"max_discount_percent": 10}


class TestTheInstructionItself:
    def test_an_empty_instruction_is_refused(self):
        with pytest.raises(ValidationError):
            PolicyProposalRequest(instruction="")

    def test_an_absurdly_long_instruction_is_refused(self):
        """A bounded field, because this one is interpolated into a prompt.

        Unbounded merchant text going into a model is a prompt-injection
        surface and a cost surface at once.
        """
        with pytest.raises(ValidationError):
            PolicyProposalRequest(instruction="x" * 5000)

    def test_a_normal_instruction_is_accepted(self):
        req = PolicyProposalRequest(instruction="never discount more than 10% and don't call before 9am")
        assert "10%" in req.instruction
