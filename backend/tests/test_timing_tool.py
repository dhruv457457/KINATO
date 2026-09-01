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


class TestThePromptTellsTheAgentHowToUseIt:
    """Mirrors the say_amount prompt test. A tool the prompt never mentions
    is a tool the model will not reach for, and guidance that lives only in
    the tool's return value arrives too late to shape the turn."""

    def test_the_prompt_names_the_tool_and_the_field_to_read(self):
        from app.channels.voice_runtime import SYSTEM_PROMPT_TEMPLATE

        assert "get_timing_plan" in SYSTEM_PROMPT_TEMPLATE
        assert "say_window" in SYSTEM_PROMPT_TEMPLATE

    def test_the_prompt_forbids_claiming_an_automatic_retry(self):
        """The single most dangerous thing the agent could say about this
        feature is that it does something it does not do."""
        from app.channels.voice_runtime import SYSTEM_PROMPT_TEMPLATE

        assert "NOTHING IS SCHEDULED" in SYSTEM_PROMPT_TEMPLATE
        assert "record_promise_to_pay" in SYSTEM_PROMPT_TEMPLATE

    def test_the_prompt_routes_cashflow_away_from_discounting(self):
        """"I can't pay till payday" is the one objection where waiting is
        the right instrument and money off is the wrong one."""
        from app.channels.voice_runtime import SYSTEM_PROMPT_TEMPLATE

        assert "timing \
problem, not a price problem" in SYSTEM_PROMPT_TEMPLATE or                "not a price problem" in SYSTEM_PROMPT_TEMPLATE


class TestTheDrawerGetsTheSamePlanTheAgentGot:
    """The dashboard recomputes rather than reading a stored copy.

    plan_windows is pure and anchored on the failure's own timestamp, so the
    drawer shows what the agent was actually offered on the call - not a
    snapshot taken at write time that has since drifted from the row it came
    from. Two sources of truth for one fact is how a merchant ends up
    reading a plan the agent never had.
    """

    def test_the_endpoint_helper_matches_the_planner(self, connected_merchant_id):
        from datetime import datetime, timezone

        from app.api.dashboard import _timing_plan_for
        from app.services.policy_engine import policy_engine
        from app.services.timing_planner import plan_windows

        anchor = datetime(2026, 9, 2, 11, 0, tzinfo=timezone.utc)
        checkout = {"abandoned_at": anchor, "failure_class": "INSUFFICIENT_FUNDS"}

        payload = _timing_plan_for(checkout, connected_merchant_id)
        policy = policy_engine.get_policy(connected_merchant_id)
        direct = plan_windows(
            "INSUFFICIENT_FUNDS",
            anchor,
            calling_start_hour=int(policy.get("calling_start_hour", 10)),
            calling_end_hour=int(policy.get("calling_end_hour", 20)),
        )
        assert [w["say_window"] for w in payload["windows"]] == [w.say_window for w in direct]

    def test_a_hard_decline_reaches_the_drawer_as_a_visible_reason(self, connected_merchant_id):
        """Not as a bare empty list. A stopping rule the merchant cannot see
        is a stopping rule they have to take on trust."""
        from datetime import datetime, timezone

        from app.api.dashboard import _timing_plan_for

        payload = _timing_plan_for(
            {"abandoned_at": datetime(2026, 9, 2, 11, 0, tzinfo=timezone.utc),
             "failure_class": "HARD_DECLINE"},
            connected_merchant_id,
        )
        assert payload["windows"] == []
        assert payload["no_window_reason"]

    def test_a_checkout_with_no_timestamp_does_not_explode(self, connected_merchant_id):
        from app.api.dashboard import _timing_plan_for

        payload = _timing_plan_for({"failure_class": "USER_ABANDON"}, connected_merchant_id)
        assert payload["windows"] == []
