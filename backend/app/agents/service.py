"""
================================================================================
FILE: app/agents/service.py
MODULE: Module 2 - Agentic Commerce Orchestration Service
--------------------------------------------------------------------------------
WHAT THIS FILE DOES:
Acts as the central execution service for the Kinato Multi-Agent engine.

CAPABILITIES:
  1. Executes the compiled LangGraph StateGraph workflow.
  2. Fallback routing: Seamlessly falls back to ScriptedFallbackEngine if
     OpenRouter API is unavailable or unconfigured.
  3. Server-Sent Events (SSE) Streaming: Yields real-time step-by-step events
     for the live negotiation visualizer on the Next.js frontend.
================================================================================
"""
import json
import asyncio
from typing import AsyncGenerator, Dict, Any
from app.models.enums import BusinessProfileType, ExecutionMode
from app.agents.graph import a2a_compiled_graph
from app.agents.fallback import fallback_engine
from app.agents.state import AgentState
from app.core.config import settings


class AgentService:
    """
    Service orchestrating LangGraph workflows and real-time SSE streaming.
    """
    @classmethod
    async def execute_negotiation(
        cls,
        profile_type: BusinessProfileType,
        execution_mode: ExecutionMode = ExecutionMode.ONE_CLICK_APPROVAL,
        target_sku: str = None
    ) -> Dict[str, Any]:
        """
        Runs the multi-agent negotiation pipeline to completion and returns the final state.
        """
        # If no LLM key provided or fallback requested, run deterministic fallback
        if not settings.OPENROUTER_API_KEY:
            return fallback_engine.run_negotiation(profile_type, execution_mode, target_sku)

        try:
            initial_state: AgentState = {
                "profile_type": profile_type,
                "execution_mode": execution_mode,
                "buyer_context": None,
                "custom_query": target_sku,
                "critical_items": [],
                "active_rfq": None,
                "supplier_quotes": [],
                "ranked_quotes": [],
                "winning_quote": None,
                "counter_offer": None,
                "final_offer": None,
                "policy_evaluation": None,
                "trace_steps": [],
                "is_fallback_mode": False,
                "error": None
            }

            final_state = a2a_compiled_graph.invoke(initial_state)
            return final_state
        except Exception as e:
            # Graceful degradation on error
            state = fallback_engine.run_negotiation(profile_type, execution_mode, target_sku)
            state["error"] = f"LangGraph execution degraded to deterministic fallback: {str(e)}"
            return state

    @classmethod
    async def stream_negotiation(
        cls,
        profile_type: BusinessProfileType,
        execution_mode: ExecutionMode = ExecutionMode.ONE_CLICK_APPROVAL,
        target_sku: str = None
    ) -> AsyncGenerator[str, None]:
        """
        Yields step-by-step Server-Sent Events (SSE) formatted strings
        for animating the live A2A bidding stream on the Next.js UI.
        """
        state = fallback_engine.run_negotiation(profile_type, execution_mode, target_sku)

        for step in state["trace_steps"]:
            event_payload = {
                "event": "agent_step",
                "step": step,
                "profile_type": profile_type.value
            }
            yield f"data: {json.dumps(event_payload)}\n\n"
            await asyncio.sleep(0.35)  # Smooth cadence for visual demo effect

        # Emit terminal event with final offer and policy evaluation
        terminal_payload = {
            "event": "negotiation_completed",
            "final_offer": state["final_offer"].model_dump() if state["final_offer"] else None,
            "policy_evaluation": state["policy_evaluation"].model_dump() if state["policy_evaluation"] else None,
            "winning_quote": state["winning_quote"].model_dump() if state["winning_quote"] else None,
            "ranked_quotes": [q.model_dump() for q in state["ranked_quotes"]],
            "active_rfq": state["active_rfq"].model_dump() if state["active_rfq"] else None
        }
        yield f"data: {json.dumps(terminal_payload)}\n\n"


agent_service = AgentService()
