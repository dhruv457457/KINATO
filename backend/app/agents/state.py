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
    # How well we actually HEARD the customer this turn, 0.0-1.0, straight
    # from Twilio's Gather `Confidence` field. None means the input did not
    # come from speech recognition at all (a keypad press, an email reply,
    # a batch-scripted utterance) and so is not subject to the mishearing
    # gate - None is "not applicable", never "unknown, assume fine".
    #
    # Twilio has been sending this on every single turn since the first
    # call this project ever placed, and nothing read it: a 0.12-confidence
    # garble reached the agent with exactly the same authority as a clean
    # 0.95 utterance, and could move real money on the strength of it. The
    # gate lives in tools.py (money tools refuse under
    # LOW_CONFIDENCE_FLOOR) rather than in the prompt, for the same reason
    # the offer token exists: a guarantee a model is asked to honour is not
    # a guarantee.
    stt_confidence: Optional[float] = None
    # Whether this turn's input came from speech recognition at all.
    #
    # This is what SCOPES the barrier-confirmation rule below, and it is a
    # separate field rather than "stt_confidence is not None" on purpose.
    # Overloading the confidence value would mean that a Twilio payload
    # which happened to omit Confidence silently switched the rule off - a
    # guarantee that disappears when an upstream field goes missing is not
    # a guarantee. Default False because most callers (email, the merchant
    # dashboard, the keypad path) are not speech at all.
    input_is_speech: bool = False
    # How the payment failed, as classified by
    # app/services/failure_diagnosis.py - never the raw Razorpay error
    # object. A declined card and an abandoned cart are different
    # conversations, and which one this is must not be something the model
    # infers from a JSON blob. None means no failed payment was recorded
    # (the customer simply walked away), which is itself a real signal.
    failure_class: Optional[str] = None
    # True once the customer has confirmed, in their own words, the barrier
    # the agent read back to them ("so the price is the sticking point - is
    # that right?").
    #
    # Required before a DISCOUNT may be requested on a spoken turn, because
    # a barrier inferred from one possibly-misheard sentence is a bad
    # reason to spend the merchant's margin. NOT required to send a
    # full-price link - making someone confirm their way to a sale they
    # already asked for is the exact behaviour FINDINGS #1 is about.
    barrier_confirmed: bool = False


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
    # Structured refusals a tool returned this turn, as
    # {"tool": ..., "reason": "REJECTED_..."} - tracked separately from
    # tool_calls_made because the CALLER frequently needs to act on WHY a
    # tool refused, not merely that it ran. voice_runtime uses this to
    # drive the barrier-confirmation turn without scanning reply text for
    # keywords, which is the pattern this codebase deleted once already.
    refusals: List[Dict[str, str]] = field(default_factory=list)
