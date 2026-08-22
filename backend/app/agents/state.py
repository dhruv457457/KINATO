"""
================================================================================
FILE: app/agents/state.py
MODULE: Module 2 - LangGraph Agent State
--------------------------------------------------------------------------------
WHAT THIS FILE DOES:
Defines the typed state passed across all nodes in the LangGraph Multi-Agent
StateGraph workflow.

IT TRACKS:
  1. Business operational context & inventory.
  2. Active A2A-RFQ and list of critical restock items.
  3. Competing Supplier Quotes received during broadcast.
  4. 5-Factor ranked quotes and selected winning quote.
  5. 1-round bounded counter-offer and dynamic concession result.
  6. Final cryptographically signed A2A proposal.
  7. Deterministic policy evaluation result.
  8. Step-by-step trace log for live UI streaming.
================================================================================
"""
from typing import TypedDict, List, Optional, Dict, Any
from app.models.enums import BusinessProfileType, ExecutionMode
from app.models.inventory import BuyerContext, InventoryItem
from app.models.a2a import A2A_RFQ, A2A_Quote, A2A_CounterOffer, A2A_FinalOffer
from app.models.policy import PolicyEvaluation


class TraceStep(TypedDict):
    """Represents a single step in the agent reasoning and negotiation timeline."""
    step_name: str
    actor: str  # "BuyerAgent" | "SupplierAgent" | "PolicyEngine" | "System"
    status: str  # "IN_PROGRESS" | "COMPLETED" | "BLOCKED"
    message: str
    timestamp: str
    data: Optional[Dict[str, Any]]


class AgentState(TypedDict):
    """The central state schema for the LangGraph Agentic Commerce pipeline."""
    # 1. Inputs
    profile_type: BusinessProfileType
    execution_mode: ExecutionMode
    buyer_context: Optional[BuyerContext]
    custom_query: Optional[str]

    # 2. Inventory & RFQ
    critical_items: List[InventoryItem]
    active_rfq: Optional[A2A_RFQ]

    # 3. Multi-Supplier Bidding
    supplier_quotes: List[A2A_Quote]
    ranked_quotes: List[A2A_Quote]
    winning_quote: Optional[A2A_Quote]

    # 4. Negotiation & Concession
    counter_offer: Optional[A2A_CounterOffer]
    final_offer: Optional[A2A_FinalOffer]

    # 5. Deterministic Policy Gate
    policy_evaluation: Optional[PolicyEvaluation]

    # 6. Observability & Telemetry
    trace_steps: List[TraceStep]
    is_fallback_mode: bool
    error: Optional[str]
