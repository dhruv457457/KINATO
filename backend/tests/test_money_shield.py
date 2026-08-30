"""A money action that has started must finish, whatever happens to the turn.

The runtime already said this in a comment, and the code did the opposite in
two different ways:

  1. `_settle_inflight_mutations` waited with
     `asyncio.wait_for(asyncio.gather(...))`. gather propagates cancellation
     to its children, so when the grace elapsed it CANCELLED the issue_offer
     tasks it was there to protect - and then logged "did not settle" about
     tasks it had just killed.

  2. The tasks were referenced only by a list owned by the run_agent frame.
     When the caller's outer timeout cancelled that frame, asyncio's own
     reference is weak, so an in-flight Razorpay call could simply be
     garbage collected.

Both produce the same outcome on a live call: a real payment link created and
never recorded, while the customer is told someone will follow up. These
tests fail on the old code.
"""
import asyncio
import types

import pytest

from app.agents import runtime as agent_runtime
from app.agents.state import AgentContext
from app.agents.tools import Tool


def _fake_llm_returning_tool_call(tool_name: str):
    """Minimal stand-in for the OpenAI client, shaped like what call_model reads."""
    calls = {"n": 0}

    class _Completions:
        @staticmethod
        async def create(**kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                tc = types.SimpleNamespace(
                    id="call_1",
                    function=types.SimpleNamespace(name=tool_name, arguments="{}"),
                )
                message = types.SimpleNamespace(content="", tool_calls=[tc])
            else:
                message = types.SimpleNamespace(content="All done.", tool_calls=None)
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])

    return types.SimpleNamespace(chat=types.SimpleNamespace(completions=_Completions()))


def _ctx() -> AgentContext:
    return AgentContext(
        merchant_id="mch_shield_test",
        correlation_id="corr_shield_test",
        checkout_id="chk_shield_test",
    )


@pytest.fixture
def slow_money_tool():
    """A mutating tool that takes longer than the settle grace allows."""
    state = {"completed": False}

    async def _fn(ctx, **kwargs):
        # Deliberately longer than _settle_inflight_mutations' 2.0s grace, so
        # the turn gives up waiting while the "money" is still moving.
        await asyncio.sleep(3.0)
        state["completed"] = True
        return {"status": "ISSUED"}

    tool = Tool(
        name="slow_money_tool",
        description="A money action that takes its time.",
        parameters={},
        required=[],
        fn=_fn,
        mutating=True,
    )
    return tool, state


class TestMoneyActionsAreNeverCancelled:
    async def test_a_money_action_outliving_the_grace_still_completes(
        self, monkeypatch, slow_money_tool
    ):
        """The grace ends the WAIT, never the work.

        On the old `wait_for(gather(...))` the 2s grace cancelled the task
        and `completed` stayed False forever - a payment link half-created
        with nothing recording it.
        """
        tool, state = slow_money_tool
        monkeypatch.setattr(
            agent_runtime, "_get_llm_client", lambda: _fake_llm_returning_tool_call(tool.name)
        )

        result = await agent_runtime.run_agent(
            system_prompt="s",
            user_message="u",
            ctx=_ctx(),
            tools=[tool],
            deadline_s=0.3,  # the turn is over almost immediately
        )

        # The turn itself gave up - that part is expected and fine.
        assert result.ok is False
        assert result.error == "deadline_exceeded"
        assert state["completed"] is False, "the tool should still be running at this point"

        # But the money action was detached, not killed. Give it the time it
        # asked for and it lands.
        await asyncio.sleep(3.2)
        assert state["completed"] is True, (
            "the money action was cancelled instead of detached - this is the "
            "half-applied effect the shield exists to prevent"
        )

    async def test_the_task_survives_outer_cancellation(self, monkeypatch, slow_money_tool):
        """voice_runtime's TURN_HARD_TIMEOUT_S cancels the whole response.

        That destroys the run_agent frame, and with it the only strong
        reference the old code kept to an in-flight money task. asyncio holds
        tasks weakly, so it could be garbage collected mid-Razorpay-call.
        _MONEY_TASKS is the strong reference that makes this survivable.
        """
        tool, state = slow_money_tool
        monkeypatch.setattr(
            agent_runtime, "_get_llm_client", lambda: _fake_llm_returning_tool_call(tool.name)
        )

        # Exactly what voice_runtime does: a hard ceiling around the turn.
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(
                agent_runtime.run_agent(
                    system_prompt="s",
                    user_message="u",
                    ctx=_ctx(),
                    tools=[tool],
                    deadline_s=0.3,
                ),
                timeout=0.6,
            )

        assert agent_runtime._MONEY_TASKS, "nothing is holding the in-flight money task"

        await asyncio.sleep(3.2)
        assert state["completed"] is True, (
            "the money action died with the frame that started it"
        )


class TestTerminalToolsEndTheTurn:
    async def test_a_terminal_tool_does_not_pay_for_a_second_model_round_trip(
        self, monkeypatch
    ):
        """issue_offer ends the turn; asking the model for a closing line is
        a full round trip whose output voice_runtime discards anyway."""
        model_calls = {"n": 0}

        async def _fn(ctx, **kwargs):
            return {"status": "ISSUED"}

        tool = Tool(
            name="terminal_tool",
            description="Ends the turn.",
            parameters={},
            required=[],
            fn=_fn,
            mutating=True,
            terminal=True,
        )

        inner = _fake_llm_returning_tool_call(tool.name)
        original_create = inner.chat.completions.create

        async def _counting_create(**kwargs):
            model_calls["n"] += 1
            return await original_create(**kwargs)

        inner.chat.completions.create = _counting_create
        monkeypatch.setattr(agent_runtime, "_get_llm_client", lambda: inner)

        result = await agent_runtime.run_agent(
            system_prompt="s", user_message="u", ctx=_ctx(), tools=[tool], deadline_s=10.0
        )

        assert result.ok is True
        assert "terminal_tool" in result.tool_calls_made
        assert model_calls["n"] == 1, (
            f"expected one model round trip, got {model_calls['n']} - the "
            "terminal-tool short circuit is not firing"
        )
        # content is None on purpose: the caller supplies the line for these
        # tools, because what is said after money moves has to be true.
        assert result.output.get("content") is None
        assert result.degraded is False
