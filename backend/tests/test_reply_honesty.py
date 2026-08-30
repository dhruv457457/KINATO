"""What the agent SAYS must be true about what actually happened.

FINDINGS #2 is the story of a real payment link created and never recorded,
while the customer was told someone would follow up. This file is the same
rule pointed the other way: the agent must not claim something happened when
it did not.

The bug these tests were written against:

`runtime.py` appends a tool's name to `tool_calls_made` immediately after it
runs, BEFORE looking at whether it succeeded - so a REFUSED `issue_offer` is
recorded exactly like a successful one. `voice_runtime` then branches on
`"issue_offer" in result.tool_calls_made` and, when the model produced no
closing sentence, says:

    "Great news - I've sent that offer to your email, you should see it any
     moment."

Usually the graph loops back to the model, which writes real prose, and that
line is never reached. But `max_iterations_reached` sets `content: None` with
`ok=True, degraded=False`, and a deadline sets `output=None` - and in both
cases the first branch matches with empty content. The customer is told their
offer was sent when nothing was sent.

`record_opt_out` is worse, because it is not only a false statement: the call
ENDS on "Understood, I won't contact you about this again" while the opt-out
was refused and never recorded. A promise never to call someone again, made
while failing to write it down, is the one this system least deserves to get
wrong.
"""
import pytest

from app.agents.state import AgentResult
from app.channels.voice_runtime import _tool_succeeded


def _refused(tool: str, reason: str = "REJECTED_OFFER_TOKEN_NOT_FOUND") -> AgentResult:
    """A turn where the tool ran and was refused, and the model then said
    nothing - the max_iterations / deadline shape."""
    return AgentResult(
        ok=True,
        output={"degraded": False, "reason": "max_iterations_reached", "content": None},
        degraded=False,
        tool_calls_made=[tool],
        refusals=[{"tool": tool, "reason": reason}],
    )


def _succeeded_result(tool: str) -> AgentResult:
    return AgentResult(
        ok=True,
        output={"degraded": False, "content": None},
        degraded=False,
        tool_calls_made=[tool],
        refusals=[],
    )


class TestToolSucceeded:
    """tool_calls_made means ATTEMPTED. The refusals list is what says whether
    it worked, and it is on the same object, fifteen lines from where the
    reply is chosen."""

    def test_a_refused_tool_did_not_succeed(self):
        assert _tool_succeeded(_refused("issue_offer"), "issue_offer") is False

    def test_an_executed_tool_with_no_refusal_succeeded(self):
        assert _tool_succeeded(_succeeded_result("issue_offer"), "issue_offer") is True

    def test_a_tool_that_never_ran_did_not_succeed(self):
        result = AgentResult(ok=True, output={"content": "hello"}, tool_calls_made=[], refusals=[])
        assert _tool_succeeded(result, "issue_offer") is False

    def test_one_tool_refusing_does_not_taint_another(self):
        """check_offer can be refused in the same turn that issue_offer works."""
        result = AgentResult(
            ok=True,
            output={"content": None},
            tool_calls_made=["check_offer", "issue_offer"],
            refusals=[{"tool": "check_offer", "reason": "REJECTED_CEILING"}],
        )
        assert _tool_succeeded(result, "issue_offer") is True
        assert _tool_succeeded(result, "check_offer") is False

    def test_the_same_tool_refused_then_retried_is_not_treated_as_success(self):
        """The live call refused issue_offer four times in one turn.

        Conservative on purpose: if any attempt of this tool was refused this
        turn, do not claim it worked. Overstating a send is worse than
        understating it - the customer can be told again, but cannot be
        un-told.
        """
        result = AgentResult(
            ok=True,
            output={"content": None},
            tool_calls_made=["issue_offer", "issue_offer"],
            refusals=[{"tool": "issue_offer", "reason": "REJECTED_OFFER_TOKEN_NOT_FOUND"}],
        )
        assert _tool_succeeded(result, "issue_offer") is False


class TestRefusalsAreVisibleAtAll:
    """The gate above is only as good as what reaches `refusals`.

    runtime.py only records a refusal whose reason starts with "REJECTED_".
    A Razorpay failure comes back as `payment_execution_failed: ...` and a
    degraded agent as `degraded_agent_cannot_mutate` - neither of which
    matched, so the exact failure that happened on the 30-payment-link call
    was invisible to every refusal check in the system.
    """

    async def test_a_payment_execution_failure_is_recorded_as_a_refusal(self, monkeypatch):
        import types

        from app.agents import runtime as agent_runtime
        from app.agents.state import AgentContext
        from app.agents.tools import Tool

        async def _fails(ctx, **kwargs):
            return {"status": "REJECTED", "reason": "payment_execution_failed: gateway said no"}

        tool = Tool(
            name="issue_offer",
            description="x",
            parameters={},
            required=[],
            fn=_fails,
            mutating=True,
            terminal=True,
        )

        calls = {"n": 0}

        class _Completions:
            @staticmethod
            async def create(**kwargs):
                calls["n"] += 1
                if calls["n"] == 1:
                    tc = types.SimpleNamespace(
                        id="c1", function=types.SimpleNamespace(name="issue_offer", arguments="{}")
                    )
                    msg = types.SimpleNamespace(content="", tool_calls=[tc])
                else:
                    msg = types.SimpleNamespace(content="Sorry about that.", tool_calls=None)
                return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])

        monkeypatch.setattr(
            agent_runtime,
            "_get_llm_client",
            lambda: types.SimpleNamespace(chat=types.SimpleNamespace(completions=_Completions())),
        )

        result = await agent_runtime.run_agent(
            system_prompt="s",
            user_message="u",
            ctx=AgentContext(merchant_id="mch_x", correlation_id="corr_x"),
            tools=[tool],
            deadline_s=10.0,
        )

        assert "issue_offer" in result.tool_calls_made, "the tool did run"
        assert any(r["tool"] == "issue_offer" for r in result.refusals), (
            "a rejected tool must reach refusals, or the reply guard cannot see it"
        )
        assert _tool_succeeded(result, "issue_offer") is False
