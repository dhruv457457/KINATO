"""
================================================================================
FILE: app/agents/graph.py
MODULE: Module 2 - LangGraph Multi-Agent StateGraph
--------------------------------------------------------------------------------
WHAT THIS FILE DOES:
Constructs and compiles the official LangGraph StateGraph workflow for
the Kinato A2A Commerce Engine.

LANGGRAPH WORKFLOW NODES:
  1. `inspect_inventory_node`: Evaluates DIR & restock triggers for buyer.
  2. `broadcast_rfq_node`: Emits standardized A2A_RFQ to supplier network.
  3. `collect_supplier_bids_node`: Queries competing suppliers for quotes & FIFO aging bundles.
  4. `rank_quotes_node`: Ranks quotes via 5-factor utility function.
  5. `negotiate_concession_node`: Runs bounded counter-negotiation with winner.
  6. `sign_proposal_node`: Mints server-owned HMAC-SHA256 proposal contract.
  7. `evaluate_policy_node`: Deterministic policy gate verification.
================================================================================
"""
import uuid
from datetime import datetime, timezone, timedelta
from typing import Dict, Any
from langgraph.graph import StateGraph, END
from app.agents.state import AgentState, TraceStep
from app.agents.buyer_agent import buyer_agent
from app.agents.supplier_agent import supplier_agent
from app.policy.engine import policy_engine
from app.core.security import generate_proposal_hash
from app.knowledge.inventory import inventory_repo
from app.knowledge.suppliers import supplier_repo
from app.models.a2a import A2A_Quote, A2A_FinalOffer


# ==============================================================================
# Node 1: Inspect Inventory & Detect Critical Needs
# ==============================================================================
def inspect_inventory_node(state: AgentState) -> Dict[str, Any]:
    profile_type = state["profile_type"]
    buyer_ctx = inventory_repo.get_context(profile_type)
    critical_items = buyer_agent.identify_critical_restock(buyer_ctx)

    target_item = None
    if state.get("custom_query"):
        for it in buyer_ctx.inventory:
            if it.sku == state["custom_query"] or state["custom_query"].lower() in it.name.lower():
                target_item = it
                break
    if not target_item:
        target_item = critical_items[0] if critical_items else buyer_ctx.inventory[0]

    trace = list(state.get("trace_steps", []))
    trace.append({
        "step_name": "Inventory Inspection",
        "actor": "BuyerAgent",
        "status": "COMPLETED",
        "message": f"DIR check for '{buyer_ctx.business_name}': Item '{target_item.name}' has DIR={target_item.days_remaining} days (Threshold: 1.5d). Triggering critical RFQ.",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": {
            "sku": target_item.sku,
            "days_remaining": target_item.days_remaining,
            "is_critical": target_item.is_critical
        }
    })

    return {
        "buyer_context": buyer_ctx,
        "critical_items": critical_items,
        "custom_query": target_item.sku,
        "trace_steps": trace
    }


# ==============================================================================
# Node 2: Broadcast A2A-RFQ
# ==============================================================================
def broadcast_rfq_node(state: AgentState) -> Dict[str, Any]:
    buyer_ctx = state["buyer_context"]
    target_sku = state["custom_query"]
    target_item = next((i for i in buyer_ctx.inventory if i.sku == target_sku), buyer_ctx.inventory[0])

    rfq = buyer_agent.create_rfq(buyer_ctx, target_item)

    trace = list(state.get("trace_steps", []))
    trace.append({
        "step_name": "A2A-RFQ Broadcast",
        "actor": "BuyerAgent",
        "status": "COMPLETED",
        "message": f"Broadcasted A2A-RFQ '{rfq.rfq_id}' for {rfq.requested_qty} {rfq.unit} of '{rfq.primary_item_name}' (Budget cap: ₹{rfq.max_budget_inr}).",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": rfq.model_dump()
    })

    return {
        "active_rfq": rfq,
        "trace_steps": trace
    }


# ==============================================================================
# Node 3: Collect Competing Supplier Bids
# ==============================================================================
def collect_supplier_bids_node(state: AgentState) -> Dict[str, Any]:
    profile_type = state["profile_type"]
    rfq = state["active_rfq"]
    available_suppliers = supplier_repo.get_suppliers(profile_type)

    quotes = []
    trace = list(state.get("trace_steps", []))

    for supp in available_suppliers:
        quote = supplier_agent.generate_quote(supp, rfq)
        if quote:
            quotes.append(quote)
            bundle_msg = f" (Included aging bundle '{quote.items[1].name}' at -₹{quote.items[1].discount_applied})" if len(quote.items) > 1 else ""
            trace.append({
                "step_name": "Supplier Bid Submitted",
                "actor": f"SupplierAgent ({supp.name})",
                "status": "COMPLETED",
                "message": f"Submitted quote ₹{quote.final_total} ({supp.distance_km}km, SLA: {supp.delivery_sla_hours}h){bundle_msg}.",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "data": quote.model_dump()
            })

    return {
        "supplier_quotes": quotes,
        "trace_steps": trace
    }


# ==============================================================================
# Node 4: 5-Factor Utility Ranking
# ==============================================================================
def rank_quotes_node(state: AgentState) -> Dict[str, Any]:
    quotes = state["supplier_quotes"]
    buyer_ctx = state["buyer_context"]

    ranked = buyer_agent.score_and_rank_quotes(quotes, buyer_ctx)
    winner = ranked[0] if ranked else None

    trace = list(state.get("trace_steps", []))
    trace.append({
        "step_name": "5-Factor Utility Ranking",
        "actor": "BuyerAgent",
        "status": "COMPLETED",
        "message": f"Evaluated {len(quotes)} quotes across Price, Distance, Preferred Status, Trust, and Freshness. Winning supplier: '{winner.supplier_name}' (Utility score: {winner.utility_score}).",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": {"ranked": [q.model_dump() for q in ranked]}
    })

    return {
        "ranked_quotes": ranked,
        "winning_quote": winner,
        "trace_steps": trace
    }


# ==============================================================================
# Node 5: Negotiate Dynamic Concession
# ==============================================================================
def negotiate_concession_node(state: AgentState) -> Dict[str, Any]:
    winner = state["winning_quote"]
    rfq = state["active_rfq"]
    profile_type = state["profile_type"]
    trace = list(state.get("trace_steps", []))

    needs_counter, counter = buyer_agent.evaluate_counter_need(winner, rfq.max_budget_inr)
    agreed_quote = winner

    if needs_counter and counter:
        supp_profile = supplier_repo.get_supplier_by_id(profile_type, winner.supplier_id)
        if supp_profile:
            trace.append({
                "step_name": "A2A Counter-Offer Dispatched",
                "actor": "BuyerAgent",
                "status": "COMPLETED",
                "message": f"Winning quote (₹{winner.final_total}) exceeds budget (₹{rfq.max_budget_inr}) by ₹{counter.gap_amount}. Emitted counter-offer.",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "data": counter.model_dump()
            })

            agreed_quote = supplier_agent.apply_concession(supp_profile, winner, counter)
            trace.append({
                "step_name": "Dynamic Concession Applied",
                "actor": f"SupplierAgent ({winner.supplier_name})",
                "status": "COMPLETED",
                "message": f"Supplier AI approved dynamic concession. New adjusted total: ₹{agreed_quote.final_total} (Floor price protected).",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "data": agreed_quote.model_dump()
            })

    return {
        "counter_offer": counter if needs_counter else None,
        "winning_quote": agreed_quote,
        "trace_steps": trace
    }


# ==============================================================================
# Node 6: Sign Proposal Contract (HMAC Digest)
# ==============================================================================
def sign_proposal_node(state: AgentState) -> Dict[str, Any]:
    quote = state["winning_quote"]
    rfq = state["active_rfq"]
    proposal_id = f"prop_{uuid.uuid4().hex[:8]}"

    payload_for_hash = {
        "proposal_id": proposal_id,
        "rfq_id": rfq.rfq_id,
        "winning_supplier_id": quote.supplier_id,
        "subtotal": quote.subtotal,
        "total_discount": quote.total_discount,
        "final_total": quote.final_total,
        "items": [item.model_dump() for item in quote.items]
    }
    proposal_hash = generate_proposal_hash(payload_for_hash)

    final_offer = A2A_FinalOffer(
        proposal_id=proposal_id,
        rfq_id=rfq.rfq_id,
        winning_supplier_id=quote.supplier_id,
        winning_supplier_name=quote.supplier_name,
        items=quote.items,
        subtotal=quote.subtotal,
        total_discount=quote.total_discount,
        final_total=quote.final_total,
        negotiation_summary=f"Mutually agreed A2A deal with '{quote.supplier_name}'.",
        proposal_hash=proposal_hash,
        created_at=datetime.now(timezone.utc).isoformat(),
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()
    )

    trace = list(state.get("trace_steps", []))
    trace.append({
        "step_name": "Cryptographic Proposal Digest Minted",
        "actor": "System",
        "status": "COMPLETED",
        "message": f"Generated immutable proposal '{proposal_id}' signed with HMAC-SHA256 digest ({proposal_hash[:12]}...).",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": final_offer.model_dump()
    })

    return {
        "final_offer": final_offer,
        "trace_steps": trace
    }


# ==============================================================================
# Node 7: Evaluate Deterministic Policy Gate
# ==============================================================================
def evaluate_policy_node(state: AgentState) -> Dict[str, Any]:
    proposal = state["final_offer"]
    buyer_ctx = state["buyer_context"]

    policy_eval = policy_engine.evaluate(proposal, buyer_ctx, check_signature=True)

    trace = list(state.get("trace_steps", []))
    trace.append({
        "step_name": "Deterministic Policy Gate Evaluation",
        "actor": "PolicyEngine",
        "status": "COMPLETED" if policy_eval.allowed_execution else "BLOCKED",
        "message": f"Policy Verdict: {policy_eval.status.value} - {policy_eval.summary_reason}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": policy_eval.model_dump()
    })

    return {
        "policy_evaluation": policy_eval,
        "trace_steps": trace
    }


# ==============================================================================
# StateGraph Assembly & Compilation
# ==============================================================================
def build_a2a_graph() -> StateGraph:
    workflow = StateGraph(AgentState)

    workflow.add_node("inspect_inventory", inspect_inventory_node)
    workflow.add_node("broadcast_rfq", broadcast_rfq_node)
    workflow.add_node("collect_bids", collect_supplier_bids_node)
    workflow.add_node("rank_quotes", rank_quotes_node)
    workflow.add_node("negotiate_concession", negotiate_concession_node)
    workflow.add_node("sign_proposal", sign_proposal_node)
    workflow.add_node("evaluate_policy", evaluate_policy_node)

    workflow.set_entry_point("inspect_inventory")
    workflow.add_edge("inspect_inventory", "broadcast_rfq")
    workflow.add_edge("broadcast_rfq", "collect_bids")
    workflow.add_edge("collect_bids", "rank_quotes")
    workflow.add_edge("rank_quotes", "negotiate_concession")
    workflow.add_edge("negotiate_concession", "sign_proposal")
    workflow.add_edge("sign_proposal", "evaluate_policy")
    workflow.add_edge("evaluate_policy", END)

    return workflow.compile()


# Export compiled graph
a2a_compiled_graph = build_a2a_graph()
