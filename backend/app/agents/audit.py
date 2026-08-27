"""
The single choke point every tool call passes through, regardless of which
agent or which graph edge got it here. This is where the offer-token gate's
guarantees actually get enforced - not in the graph's edges, which just
decide *whether* to call a tool, never *whether it's allowed to*.

Four things happen here, in order, for every tool call:
  1. Reject any argument name a tool schema should never have exposed
     (defense in depth - tools.py already asserts this at import time).
  2. Reject a mutating tool outright if the agent is degraded
     (ctx.allow_mutations is False) - a heuristic fallback can observe and
     recommend, but cannot make money move.
  3. Run the tool, timing it.
  4. Write an audit_log row and publish `agent.tool_called` - which is what
     gives the dashboard's live agent feed and the recovery drawer's audit
     timeline their data, for free, from every agent that goes through here.
"""
import logging
import time
from typing import Any, Dict

from app.agents.state import AgentContext
from app.agents.tools import Tool, FORBIDDEN_ARG_NAMES
from app.db.repositories.audit import record_audit
from app.gateway.event_bus import bus

logger = logging.getLogger(__name__)


async def execute_tool(tool: Tool, args: Dict[str, Any], ctx: AgentContext) -> Dict[str, Any]:
    start = time.monotonic()
    offending = FORBIDDEN_ARG_NAMES & set(args.keys())
    if offending:
        result = {"status": "REJECTED", "reason": f"forbidden_argument: {sorted(offending)}"}
        await _audit(tool, args, ctx, result, start, decision="REJECTED")
        return result

    if tool.mutating and not ctx.allow_mutations:
        result = {"status": "REJECTED", "reason": "degraded_agent_cannot_mutate"}
        await _audit(tool, args, ctx, result, start, decision="REJECTED")
        return result

    try:
        result = await tool.fn(ctx, **args)
    except TypeError as e:
        # An unexpected/missing argument from a malformed model tool-call -
        # a REJECTED result, not a 500 that takes down the whole call.
        result = {"status": "REJECTED", "reason": f"bad_arguments: {e}"}
    except Exception as e:
        logger.error(f"Tool {tool.name!r} raised: {e}", exc_info=True)
        result = {"status": "ERROR", "reason": str(e)}

    decision = result.get("decision") or result.get("status")
    await _audit(tool, args, ctx, result, start, decision=decision)
    return result


async def _audit(
    tool: Tool, args: Dict[str, Any], ctx: AgentContext, result: Dict[str, Any], start: float, decision: Any
) -> None:
    latency_ms = int((time.monotonic() - start) * 1000)
    try:
        record_audit(
            actor=f"agent:{ctx.source}",
            action=tool.name,
            merchant_id=ctx.merchant_id,
            correlation_id=ctx.correlation_id,
            args=args,
            result=result,
            decision=str(decision) if decision is not None else None,
            degraded=ctx.degraded,
            latency_ms=latency_ms,
        )
    except Exception as e:
        # Audit persistence must never take down the tool call it's
        # describing - same "fire-and-forget, never fatal" rule the event
        # bus's own _persist() follows.
        logger.warning(f"audit_log write failed for {tool.name!r} (non-fatal): {e}")

    await bus.publish(
        event_type="agent.tool_called",
        payload={
            "tool": tool.name,
            "mutating": tool.mutating,
            "args": args,
            "result": result,
            "degraded": ctx.degraded,
            "source": ctx.source,
            "latency_ms": latency_ms,
        },
        correlation_id=ctx.correlation_id,
        merchant_id=ctx.merchant_id,
    )
