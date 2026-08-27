"""
Shared state contract for every agent's bounded reasoning loop
(see runtime.py). One AgentContext is built by the *caller* (never the
model) per invocation and carries every identity/money-adjacent field a
tool needs - merchant_id, customer_id, checkout_id. Tool schemas exposed to
the LLM are forbidden from declaring any of these as arguments (enforced in
audit.py); a tool function instead receives ctx as a plain Python parameter
that never passes through the model's JSON tool-call arguments at all.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TypedDict


@dataclass(frozen=True)
class AgentContext:
    merchant_id: str
    correlation_id: str
    customer_id: Optional[str] = None
    checkout_id: Optional[str] = None
    recovery_attempt_id: Optional[str] = None
    # False whenever the agent is degraded (see runtime.py) - a heuristic
    # path can observe and recommend, but structurally cannot call a
    # mutating tool. This is enforced in audit.py's execute_tool gate, not
    # just documented here.
    allow_mutations: bool = True
    degraded: bool = False
    # "llm" | "heuristic" - carried onto every audit_log row and the
    # agent.tool_called event so the dashboard never shows a heuristic
    # guess dressed up as a model decision (see the honesty-pass note in
    # the rebuild plan about confidence numbers on fallback paths).
    source: str = "llm"


class AgentState(TypedDict):
    messages: List[Dict[str, Any]]
    iterations: int
    ctx: AgentContext
    final: Optional[Dict[str, Any]]
    tool_calls_made: List[str]


@dataclass
class AgentResult:
    """What runtime.run_agent() returns. The graph invocation itself never
    raises - a timeout, a recursion-limit halt, or an unexpected exception
    all become `ok=False` with a reason, never an uncaught exception
    propagating into a voice call or webhook handler."""

    ok: bool
    output: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    degraded: bool = False
    iterations: int = 0
    tool_calls_made: List[str] = field(default_factory=list)
