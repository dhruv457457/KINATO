"""The timing plan reaches the agent as words, and only as a suggestion.

Two things have to hold for this tool to be safe to put in front of a model.

  * It must not be able to act. It writes nothing and contacts nobody; the
    only route from "a date was discussed" to "a date is in the system" is
    still record_promise_to_pay, which was already built, gated and tested.
  * It must not let the agent claim something is scheduled. Nothing retries
    anything in Tier 1, so "we'll try your card again Wednesday" describes
    machinery that does not exist - the same shape as the false "I've sent
    that offer" in FINDINGS #2, which is the bug this codebase most wants
    never to ship again.
"""
import pytest

from app.agents.state import AgentContext
from app.agents.tools import ALL_TOOLS, get_timing_plan, _get_timing_plan
from app.db.repositories import checkouts as checkouts_repo


class TestItCannotAct:
    def test_the_tool_is_declared_non_mutating(self):
        assert get_timing_plan.mutating is False

    def test_it_takes_nothing_from_the_model(self):
        """No arguments at all. Every identity field comes from ctx, which
        never passes through the model's tool-call JSON."""
        assert get_timing_plan.parameters == {}
        assert get_timing_plan.required == []

    def test_it_is_registered_once(self):
        names = [t.name for t in ALL_TOOLS]
        assert names.count("get_timing_plan") == 1


class TestWhatTheAgentIsHandedBack:
    async def test_windows_come_back_as_finished_phrases(
        self, connected_merchant_id, unique_checkout_id
    ):
        checkouts_repo.create_checkout(
            merchant_id=connected_merchant_id,
            amount_paise=129_000,
            cogs_paise=50_000,
            checkout_id=unique_checkout_id,
        )
        result = await _get_timing_plan(
            AgentContext(
                merchant_id=connected_merchant_id,
                correlation_id="corr_timing",
                checkout_id=unique_checkout_id,
            )
        )
        assert "error" not in result
        assert result["windows"], "no windows for an unclassified checkout"
        for w in result["windows"]:
            assert w["say_window"] and w["say_reason"]
            # The model reads the phrase; it is never asked to build a date.
            assert "2026-" not in w["say_window"]
            assert "T" not in w["say_window"].replace("Tuesday", "").replace(
                "Thursday", ""
            ).replace("Wednesday", "").replace("September", "").replace("October", "")

    async def test_it_never_tells_the_agent_something_is_automatic(
        self, connected_merchant_id, unique_checkout_id
    ):
        checkouts_repo.create_checkout(
            merchant_id=connected_merchant_id,
            amount_paise=129_000,
            cogs_paise=50_000,
            checkout_id=unique_checkout_id,
        )
        result = await _get_timing_plan(
            AgentContext(
                merchant_id=connected_merchant_id,
                correlation_id="corr_timing",
                checkout_id=unique_checkout_id,
            )
        )
        blob = str(result).lower()
        assert "automatically" in blob or "never tell them" in blob, (
            "the guidance must actively warn the agent off claiming a retry"
        )
        # And the tool's own description must not promise dispatch either.
        desc = get_timing_plan.description.lower()
        for banned in ("we will retry", "automatically retry", "charges", "charge them"):
            assert banned not in desc

    async def test_a_missing_checkout_is_an_error_not_an_invented_plan(
        self, connected_merchant_id
    ):
        result = await _get_timing_plan(
            AgentContext(
                merchant_id=connected_merchant_id,
                correlation_id="corr_timing",
                checkout_id="chk_does_not_exist_at_all",
            )
        )
        assert result.get("error") == "checkout_not_found"
        assert "windows" not in result


class TestAHardDeclineTellsTheAgentToStop:
    async def test_no_windows_and_an_explicit_instruction(
        self, connected_merchant_id, unique_checkout_id
    ):
        """An empty list on its own is dangerous: a model handed nothing will
        fill the silence with a plausible date. It has to be told why."""
        checkouts_repo.create_checkout(
            merchant_id=connected_merchant_id,
            amount_paise=129_000,
            cogs_paise=50_000,
            checkout_id=unique_checkout_id,
        )
        checkouts_repo.record_failure(
            checkout_id=unique_checkout_id,
            failure={"error_code": "BAD_REQUEST_ERROR", "error_reason": "card_blocked"},
            failure_class="HARD_DECLINE",
        )
        result = await _get_timing_plan(
            AgentContext(
                merchant_id=connected_merchant_id,
                correlation_id="corr_timing",
                checkout_id=unique_checkout_id,
            )
        )
        assert result["windows"] == []
        assert "do not suggest" in result["guidance"].lower()
