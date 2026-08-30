"""
The generic bounded reasoning loop every agent runs on: call_model ->
execute_tools -> back to call_model or END. LangGraph gives us the graph
scaffolding (StateGraph, recursion limiting); everything money- or
identity-adjacent is enforced in app/agents/audit.py's execute_tool, not in
the graph's edges - the edges only decide *whether* to call a tool, never
*whether it's allowed to*.

Two deliberate deviations from the "obvious" LangGraph setup, both because
our tool-calling messages are plain OpenAI-format dicts (not langchain
BaseMessage objects) and our tool-execution semantics don't match the
built-ins' defaults:

  - We don't use LangGraph's prebuilt ToolNode. It runs every requested
    tool call concurrently by default; we need mutating tools to run
    strictly sequentially (the plan's explicit requirement, and the reason
    the offer-token gate can't race itself), so execute_tools is our own
    node with its own read/mutating split.
  - We don't use LangGraph's checkpointer/add_messages reducer for
    multi-turn state (e.g. a voice call's negotiation history across
    Twilio's per-turn Gather+Play requests). That reducer expects
    langchain message objects; converting our raw OpenAI dicts back and
    forth for every turn is more moving parts than the problem needs. A
    plain in-memory dict keyed by thread_id does the same job for a single
    process, documented explicitly here rather than silently deviating.

The graph invocation NEVER raises: a timeout, a recursion-limit halt, or an
unexpected exception all become AgentResult(ok=False, ...), because this
runs inside voice calls and webhook handlers that must not go down with it.
"""
import asyncio
import dataclasses
import json
import logging
from typing import Any, Dict, List, Optional

from langgraph.errors import GraphRecursionError
from langgraph.graph import StateGraph, END

from app.core.config import settings
from app.agents.state import AgentContext, AgentState, AgentResult
from app.agents.tools import Tool, ALL_TOOLS
from app.agents.audit import execute_tool

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 4

# Live calls want a little warmth so replies don't sound canned. The batch
# scoreboard sets this to 0.0: a measurement harness whose cases flip
# between runs is not a measurement. Three cases changed verdict between
# two consecutive scoreboard runs at 0.2, which made it impossible to tell
# a real regression from sampling noise.
LLM_TEMPERATURE = 0.2

# Latency scales with how much the model decides to write, and this was
# unbounded. A line that is about to be READ ALOUD to someone on a phone
# needs very few tokens; a paragraph is a worse call as well as a slower
# one.
#
# Generous rather than tight on purpose. Truncating mid-tool-call produces
# an unparseable arguments blob, which json.loads turns into `{}`, which
# becomes a TypeError on a missing required argument, which execute_tool
# files as a quiet "REJECTED: bad_arguments" - recoveries would simply stop
# happening with nothing in the logs pointing at why. See the finish_reason
# check below, which refuses to guess at a truncated response.
LLM_MAX_TOKENS = 400
DEFAULT_DEADLINE_S = 8.0

# Multi-turn history per conversation (see module docstring for why this is
# a plain dict rather than LangGraph's own checkpointer). Never grows
# unbounded in practice - a voice call's thread_id is discarded when the
# call ends; this is not meant to survive a process restart, matching the
# "LangGraph for the bounded reasoning session, not durable coordination"
# split from the rebuild plan.
_conversation_store: Dict[str, List[Dict[str, Any]]] = {}

# Strong references to money actions that are still running.
#
# `inflight_mutations` below is owned by a run_agent frame. When the
# CALLER's outer timeout (voice_runtime's TURN_HARD_TIMEOUT_S) fires, that
# frame is cancelled and destroyed - and asyncio holds only WEAK references
# to running tasks, so an issue_offer that is mid-Razorpay-call could
# simply be garbage collected. Shielding does not help here: a shield
# protects against cancellation, not against nobody holding the task at
# all.
#
# This set is that holder, taken at creation and released on completion. It
# is what makes "a money action that has started always finishes" true for
# the outer clock as well as the inner one.
_MONEY_TASKS: set = set()


# One OpenAI client per event loop, not per model round trip.
#
# This used to build a fresh AsyncOpenAI - and with it a fresh httpx pool -
# on every single call_model node execution, so each of the two-to-three
# model round trips in a turn paid DNS + TCP + TLS before sending a byte.
# Keyed by loop for the same reason as net.py's cache: a client binds its
# pool to the loop that first used it, and the test suite runs one loop per
# test.
# Keyed on the credentials too, not just the loop: a test (or a provider
# swap at runtime) that changes LLM_BASE_URL or the key must not silently
# keep talking to the old endpoint through a cached client.
_LLM_CLIENTS: Dict[tuple, Any] = {}


def _get_llm_client():
    # Read the key on every call, deliberately. Returning None here is the
    # DEGRADATION GATE - it is what forces the heuristic, non-mutating path
    # in call_model - and a client built once at import time would read the
    # setting once at import time, breaking both that gate and every test
    # that patches settings.
    if not settings.OPENROUTER_API_KEY:
        return None

    from openai import AsyncOpenAI
    from app.core.net import ipv4_client, shared_ipv4_client

    try:
        loop_key = id(asyncio.get_running_loop())
    except RuntimeError:
        # ipv4_client (not shared): diagnosed live - repeated OpenRouter
        # ConnectTimeouts, same black-holed-IPv6 root cause as ElevenLabs's
        # (see app/core/net.py).
        return AsyncOpenAI(
            base_url=settings.LLM_BASE_URL, api_key=settings.OPENROUTER_API_KEY, http_client=ipv4_client()
        )

    key = (loop_key, settings.LLM_BASE_URL, settings.OPENROUTER_API_KEY)
    client = _LLM_CLIENTS.get(key)
    if client is None:
        client = AsyncOpenAI(
            base_url=settings.LLM_BASE_URL,
            api_key=settings.OPENROUTER_API_KEY,
            http_client=shared_ipv4_client("llm"),
        )
        _LLM_CLIENTS[key] = client
    return client


def _build_graph(
    tools: List[Tool],
    max_iterations: int,
    tool_calls_tracker: List[str],
    inflight_mutations: list,
    refusals_tracker: List[Dict[str, str]],
):
    """tool_calls_tracker is a plain list owned by the caller (run_agent),
    appended to directly here rather than only through the graph's own
    state - a real, confirmed bug: when the overall deadline_s wrapper in
    run_agent times out mid-run, LangGraph's returned state is discarded
    entirely, which silently threw away the record of tool calls that had
    ALREADY executed for real (real audit_log rows, in one observed case a
    real issue_offer that had already sent a real payment link) before the
    final "say something back" LLM call was what actually got cut off.
    Appending here means run_agent can report real tool activity even on
    a timeout, instead of a misleadingly empty result."""
    tools_by_name = {t.name: t for t in tools}

    async def call_model(state: AgentState) -> AgentState:
        ctx = state["ctx"]
        if state.get("final") is not None:
            return state

        client = _get_llm_client()
        if client is None:
            state["ctx"] = dataclasses.replace(ctx, degraded=True, allow_mutations=False, source="heuristic")
            state["final"] = {"degraded": True, "reason": "no_llm_api_key_configured"}
            return state

        try:
            response = await client.chat.completions.create(
                model=settings.LLM_MODEL,
                messages=state["messages"],
                tools=[t.to_openai_schema() for t in tools],
                temperature=LLM_TEMPERATURE,
                max_tokens=LLM_MAX_TOKENS,
                timeout=6.0,
            )
        except Exception as e:
            logger.warning(f"Agent LLM call failed, degrading this run: {e}")
            state["ctx"] = dataclasses.replace(ctx, degraded=True, allow_mutations=False, source="heuristic")
            state["final"] = {"degraded": True, "reason": f"llm_call_failed: {e.__class__.__name__}"}
            return state

        choice = response.choices[0]
        message = choice.message

        # A response cut off by max_tokens is not a response. If it was
        # cut off MID TOOL CALL the arguments JSON is unparseable, and the
        # failure is silent all the way down: json.loads -> {} -> TypeError
        # on a missing argument -> a "REJECTED: bad_arguments" audit row
        # that looks like the model choosing badly rather than a truncated
        # payload. Degrade here, where it can still be named.
        if getattr(choice, "finish_reason", None) == "length" and message.tool_calls:
            logger.warning(
                "Agent response was truncated by max_tokens mid tool call - degrading rather "
                "than acting on a partial tool-call payload."
            )
            state["ctx"] = dataclasses.replace(ctx, degraded=True, allow_mutations=False, source="heuristic")
            state["final"] = {"degraded": True, "reason": "llm_response_truncated"}
            return state

        message_dict: Dict[str, Any] = {"role": "assistant", "content": message.content or ""}
        if message.tool_calls:
            message_dict["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in message.tool_calls
            ]
        state["messages"].append(message_dict)

        if not message.tool_calls:
            state["final"] = {"content": message.content, "degraded": False}
        return state

    async def execute_tools(state: AgentState) -> AgentState:
        ctx = state["ctx"]
        last_message = state["messages"][-1]
        tool_calls = last_message.get("tool_calls") or []
        state["iterations"] += 1

        terminal_tools: List[str] = []
        read_calls, mutating_calls = [], []
        for tc in tool_calls:
            name = tc["function"]["name"]
            tool = tools_by_name.get(name)
            (mutating_calls if (tool is None or tool.mutating) else read_calls).append(tc)

        async def run_one(tc: Dict[str, Any]) -> Dict[str, Any]:
            name = tc["function"]["name"]
            tool = tools_by_name.get(name)
            try:
                args = json.loads(tc["function"]["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            if tool is None:
                result = {"status": "REJECTED", "reason": f"unknown_tool: {name}"}
            else:
                if tool.mutating:
                    # A mutating tool MOVES MONEY. asyncio.wait_for cancels
                    # whatever it is waiting on when the deadline passes, and
                    # on a real call that killed issue_offer *after* it had
                    # created a live Razorpay payment link but before it
                    # recorded the call or dispatched the email - so the
                    # customer was told "someone will follow up" while a real
                    # link sat unsent. Shielding means a money action that has
                    # started always finishes; the deadline may end the
                    # conversation turn, never a half-applied effect.
                    # Track the INNER task, not the shield wrapper: cancelling
                    # a shield cancels only the wrapper, and if that is all we
                    # kept a handle to, the real work becomes unreachable and
                    # unawaitable - which is precisely the half-applied effect
                    # this is meant to prevent.
                    inner = asyncio.create_task(execute_tool(tool, args, ctx))
                    _MONEY_TASKS.add(inner)
                    inner.add_done_callback(_MONEY_TASKS.discard)
                    inflight_mutations.append((tool.name, inner))
                    result = await asyncio.shield(inner)
                else:
                    result = await execute_tool(tool, args, ctx)
                state["tool_calls_made"].append(tool.name)
                tool_calls_tracker.append(tool.name)  # survives even if the overall run later times out
                # A structured refusal is information the CALLER needs, not
                # just the model: voice_runtime decides whether to run a
                # barrier-confirmation turn from this, rather than by
                # reading the model's own prose back. Same survives-a-
                # timeout reasoning as tool_calls_tracker above.
                _reason = result.get("reason") if isinstance(result, dict) else None
                if isinstance(_reason, str) and _reason.startswith("REJECTED_"):
                    refusals_tracker.append({"tool": tool.name, "reason": _reason})
                elif tool.terminal and isinstance(result, dict) and result.get("status") not in ("REJECTED", "ERROR"):
                    # This tool ENDS the turn, so the graph must not route
                    # back to the model for a closing sentence. That round
                    # trip is what pushed the "yes, send it" turn past
                    # Twilio's window on a live call - and the caller throws
                    # the sentence away regardless: voice_runtime prefers its
                    # own line for exactly these tools, because what is said
                    # here has to be true about money that has already moved.
                    # Paying a model to write text nobody reads is the
                    # cheapest second in this system to give back.
                    terminal_tools.append(tool.name)
            return {"tool_call_id": tc["id"], "role": "tool", "name": name, "content": json.dumps(result)}

        # Read-only tools run concurrently; mutating tools run strictly
        # sequentially, one at a time, in the order the model requested
        # them - this is the actual enforcement point for "mutating tools
        # strictly sequential" from the plan.
        if read_calls:
            for msg in await asyncio.gather(*(run_one(tc) for tc in read_calls)):
                state["messages"].append(msg)
        for tc in mutating_calls:
            state["messages"].append(await run_one(tc))

        # This has to be decided here, inside a node, not in the
        # conditional-edge function below: LangGraph conditional edges are
        # pure routing functions, and mutating the `state` object they're
        # handed is not guaranteed to propagate back into the graph's real
        # state (it silently didn't, in an earlier version of this file -
        # only a node's *return value* is merged into state).
        if state["iterations"] >= max_iterations:
            state["final"] = {"degraded": False, "reason": "max_iterations_reached", "content": None}

        # `content: None` is deliberate. The CALLER owns the line for a
        # terminal tool (voice_runtime's issue_offer / record_opt_out
        # branches, which already prefer their own wording over the
        # model's), and None routes straight to it without the model being
        # asked at all. Not degraded: the turn did exactly what it set out
        # to do, which is the opposite of degraded.
        if terminal_tools and state.get("final") is None:
            state["final"] = {
                "degraded": False,
                "reason": "terminal_tool",
                "content": None,
                "terminal_tool": terminal_tools[0],
            }

        return state

    def route_after_model(state: AgentState) -> str:
        return END if state.get("final") is not None else "execute_tools"

    def route_after_tools(state: AgentState) -> str:
        return END if state.get("final") is not None else "call_model"

    graph = StateGraph(AgentState)
    graph.add_node("call_model", call_model)
    graph.add_node("execute_tools", execute_tools)
    graph.set_entry_point("call_model")
    graph.add_conditional_edges("call_model", route_after_model, {"execute_tools": "execute_tools", END: END})
    graph.add_conditional_edges("execute_tools", route_after_tools, {"call_model": "call_model", END: END})
    return graph.compile()


async def run_agent(
    system_prompt: str,
    user_message: str,
    ctx: AgentContext,
    tools: Optional[List[Tool]] = None,
    thread_id: Optional[str] = None,
    max_iterations: int = MAX_ITERATIONS,
    deadline_s: float = DEFAULT_DEADLINE_S,
) -> AgentResult:
    """Runs one bounded reasoning turn. If thread_id is given, prior turns'
    messages for that thread are replayed first (see module docstring) -
    this is what lets a voice call's negotiation carry context across
    Twilio's separate per-turn HTTP requests."""
    tools = tools if tools is not None else ALL_TOOLS
    history = _conversation_store.get(thread_id, []) if thread_id else []
    messages = history or [{"role": "system", "content": system_prompt}]
    messages = messages + [{"role": "user", "content": user_message}]

    initial_state: AgentState = {
        "messages": messages,
        "iterations": 0,
        "ctx": ctx,
        "final": None,
        "tool_calls_made": [],
    }

    # Owned here, not just inside the graph's own state - see
    # _build_graph's docstring for why: a timeout must not make real,
    # already-executed tool calls (real audit rows, real money moved)
    # disappear from the result as if they never happened.
    tool_calls_tracker: List[str] = []
    # Money actions started but not yet finished when the deadline fires.
    # They are shielded from cancellation (see run_one), so they WILL
    # complete - we just have to wait for them before reporting, or we would
    # tell the customer nothing happened while a real payment link was being
    # created behind them. That exact contradiction happened on a live call.
    inflight_mutations: list = []
    refusals_tracker: List[Dict[str, str]] = []
    graph = _build_graph(tools, max_iterations, tool_calls_tracker, inflight_mutations, refusals_tracker)

    async def _settle_inflight_mutations(grace_s: float = 2.0) -> None:
        """Give a money action a moment to land, then DETACH - never cancel.

        This used to be `asyncio.wait_for(asyncio.gather(...))`, which is a
        trap: gather propagates cancellation to its children, so when the
        timeout fired it cancelled the very issue_offer tasks the shield
        exists to protect, and then logged "did not settle" about tasks it
        had just killed. That is the half-applied effect described in
        run_one's comment, produced by the code written to prevent it.

        `asyncio.wait` does not cancel on timeout. Anything still running
        when the grace elapses keeps running to completion, held by
        _MONEY_TASKS, and is simply no longer waited on - the conversation
        turn ends, the money action does not. The grace is short for the
        same reason: the customer is on the phone, and Twilio is counting.
        """
        pending = [(n, t) for n, t in inflight_mutations if not t.done()]
        if not pending:
            return
        logger.warning(
            f"Deadline hit with {len(pending)} money action(s) still running "
            f"({[n for n, _ in pending]}) - waiting for them rather than reporting a half-applied effect."
        )
        await asyncio.wait([t for _, t in pending], timeout=grace_s)
        still_running = [n for n, t in inflight_mutations if not t.done()]
        if still_running:
            logger.error(
                f"Money action(s) {still_running} did not settle within {grace_s}s - "
                "detaching; they keep running and will complete."
            )
        for name, task in inflight_mutations:
            if task.done() and not task.cancelled() and task.exception() is None and name not in tool_calls_tracker:
                tool_calls_tracker.append(name)

    try:
        final_state = await asyncio.wait_for(
            graph.ainvoke(initial_state, config={"recursion_limit": max_iterations * 3 + 4}),
            timeout=deadline_s,
        )
    except asyncio.TimeoutError:
        await _settle_inflight_mutations()
        return AgentResult(
            ok=False, error="deadline_exceeded", degraded=True,
            tool_calls_made=list(tool_calls_tracker), refusals=list(refusals_tracker),
        )
    except GraphRecursionError:
        return AgentResult(
            ok=False, error="recursion_limit_reached", degraded=True,
            tool_calls_made=list(tool_calls_tracker), refusals=list(refusals_tracker),
        )
    except Exception as e:
        logger.error(f"Agent run raised unexpectedly: {e}", exc_info=True)
        return AgentResult(
            ok=False, error=f"unexpected_error: {e.__class__.__name__}", degraded=True,
            tool_calls_made=list(tool_calls_tracker), refusals=list(refusals_tracker),
        )

    if thread_id:
        _conversation_store[thread_id] = final_state["messages"]

    final = final_state.get("final") or {}
    return AgentResult(
        ok=True,
        output=final,
        degraded=bool(final.get("degraded") or final_state["ctx"].degraded),
        iterations=final_state["iterations"],
        tool_calls_made=final_state["tool_calls_made"],
        refusals=list(refusals_tracker),
    )


def has_thread(thread_id: str) -> bool:
    return thread_id in _conversation_store


def restore_thread(thread_id: str, system_prompt: str, turns: List[Dict[str, Any]]) -> None:
    """Rebuild an in-memory conversation thread from persisted turns.

    This is what makes a mid-call restart survivable. The store is still a
    plain in-process dict - that has not changed, and for a single turn it
    is the right shape - but it is no longer the only copy. When it is
    empty for a call that is demonstrably in progress, the transcript in
    the database is the source of truth and the cache is rebuilt from it.

    Tool calls are deliberately NOT replayed. What matters for the next
    turn is what was said; re-injecting old tool-call/tool-result message
    pairs would invite the model to treat a completed action as still
    pending - and one of those actions creates real payment links.
    """
    messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    for turn in turns:
        role = "assistant" if turn.get("speaker") == "agent" else "user"
        text = (turn.get("text") or "").strip()
        if text:
            messages.append({"role": role, "content": text})
    _conversation_store[thread_id] = messages
    logger.info(f"Restored conversation thread {thread_id!r} from {len(turns)} persisted turn(s).")


def seed_opening(thread_id: str, system_prompt: str, opening_line: str) -> None:
    """Tell the agent what it has already said.

    The opening line is generated before dialling and played to the
    customer by Twilio, so by the time the first reply arrives the agent
    HAS spoken - but nothing put that in its message thread. The model
    therefore opened every call by introducing itself a second time,
    immediately after the customer had answered the first introduction.

    On a short conversation this was not merely awkward, it was fatal: the
    agent spent its turns on a greeting it had already given while the
    customer asked to buy, and never reached a tool at all.
    """
    if not thread_id or not (opening_line or "").strip():
        return
    _conversation_store[thread_id] = [
        {"role": "system", "content": system_prompt},
        {"role": "assistant", "content": opening_line},
    ]


def discard_thread(thread_id: str) -> None:
    """Call when a conversation ends (e.g. the voice call hangs up) so the
    in-memory history doesn't linger for the life of the process."""
    _conversation_store.pop(thread_id, None)
