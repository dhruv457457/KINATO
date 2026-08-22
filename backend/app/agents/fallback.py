"""
================================================================================
FILE: app/agents/fallback.py
MODULE: Module 2 - Deterministic Scripted Fallback Engine
--------------------------------------------------------------------------------
WHAT THIS FILE DOES:
Provides a zero-failure, deterministic multi-agent fallback engine.

WHY THIS IS CRITICAL FOR THE HACKATHON & VIDEO:
  If the OpenRouter API is experiencing rate limits, network latency, or if the
  evaluator does not have an API key, this engine executes the exact same A2A
  multi-agent negotiation flow deterministically in pure Python code with 0ms lag.

GUARANTEES:
  1. 100% demo uptime and deterministic reproducibility.
  2. Emits the full trace log identical to the LLM agent flow.
  3. Adheres to all mathematical formulas (DIR, 5-Factor Ranking, Floor Price, HMAC).
================================================================================
"""
import uuid
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List
from app.models.enums import BusinessProfileType, ExecutionMode
from app.models.a2a import A2A_RFQ, A2A_Quote, A2A_FinalOffer
from app.knowledge.inventory import inventory_repo
from app.knowledge.suppliers import supplier_repo
from app.agents.buyer_agent import buyer_agent
from app.agents.supplier_agent import supplier_agent
from app.policy.engine import policy_engine
from app.core.security import generate_proposal_hash
from app.agents.state import AgentState, TraceStep


class ScriptedFallbackEngine:
    """
    Deterministic fallback engine executing multi-agent commerce without LLM API calls.
    """
    @classmethod
    def run_negotiation(
        cls,
        profile_type: BusinessProfileType,
        execution_mode: ExecutionMode = ExecutionMode.ONE_CLICK_APPROVAL,
        target_sku: str = None
    ) -> AgentState:
        trace_steps: List[TraceStep] = []
        now_iso = datetime.now(timezone.utc).isoformat()

        # Step 1: Buyer Inventory Inspection
        buyer_ctx = inventory_repo.get_context(profile_type)
        critical_items = buyer_agent.identify_critical_restock(buyer_ctx)

        # Select target item
        target_item = None
        if target_sku:
            for item in buyer_ctx.inventory:
                if item.sku == target_sku:
                    target_item = item
                    break
        if not target_item:
            target_item = critical_items[0] if critical_items else buyer_ctx.inventory[0]

        trace_steps.append({
            "step_name": "Inventory Burn Evaluation",
            "actor": "BuyerAgent",
            "status": "COMPLETED",
            "message": f"Evaluated inventory for '{buyer_ctx.business_name}'. Item '{target_item.name}' has DIR = {target_item.days_remaining} days (Threshold: 1.5d). Triggering critical A2A-RFQ.",
            "timestamp": now_iso,
            "data": {
                "sku": target_item.sku,
                "current_stock": target_item.current_stock,
                "daily_burn": target_item.daily_burn_rate,
                "days_remaining": target_item.days_remaining
            }
        })

        # Step 2: Broadcast A2A-RFQ
        rfq = buyer_agent.create_rfq(buyer_ctx, target_item)
        trace_steps.append({
            "step_name": "A2A-RFQ Broadcast",
            "actor": "BuyerAgent",
            "status": "COMPLETED",
            "message": f"Broadcasted A2A-RFQ '{rfq.rfq_id}' for {rfq.requested_qty} {rfq.unit} of '{rfq.primary_item_name}' (Allocated budget: ₹{rfq.max_budget_inr}).",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": rfq.model_dump()
        })

        # Step 3: Collect Competing Supplier Bids
        available_suppliers = supplier_repo.get_suppliers(profile_type)
        quotes: List[A2A_Quote] = []

        for supp in available_suppliers:
            quote = supplier_agent.generate_quote(supp, rfq)
            if quote:
                quotes.append(quote)
                bundle_info = f" (Included aging bundle '{quote.items[1].name}' at ₹{quote.items[1].discount_applied} discount)" if len(quote.items) > 1 else ""
                trace_steps.append({
                    "step_name": "Supplier Bid Submitted",
                    "actor": f"SupplierAgent ({supp.name})",
                    "status": "COMPLETED",
                    "message": f"Submitted quote ₹{quote.final_total} ({supp.distance_km}km, SLA: {supp.delivery_sla_hours}h){bundle_info}.",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "data": quote.model_dump()
                })

        # Step 4: 5-Factor Ranking
        ranked_quotes = buyer_agent.score_and_rank_quotes(quotes, buyer_ctx)
        winning_quote = ranked_quotes[0] if ranked_quotes else None

        trace_steps.append({
            "step_name": "5-Factor Utility Ranking",
            "actor": "BuyerAgent",
            "status": "COMPLETED",
            "message": f"Evaluated {len(quotes)} quotes across Price (40%), Distance (20%), Preferred Status (15%), Trust (15%), and Freshness (10%). Winning supplier: '{winning_quote.supplier_name}' (Utility score: {winning_quote.utility_score}).",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": {"ranked": [q.model_dump() for q in ranked_quotes]}
        })

        # Step 5: Bounded Counter-Negotiation & Dynamic Concession
        winning_supplier_profile = supplier_repo.get_supplier_by_id(profile_type, winning_quote.supplier_id)
        needs_counter, counter = buyer_agent.evaluate_counter_need(winning_quote, rfq.max_budget_inr)

        agreed_quote = winning_quote
        if needs_counter and counter and winning_supplier_profile:
            trace_steps.append({
                "step_name": "A2A Counter-Offer Dispatched",
                "actor": "BuyerAgent",
                "status": "COMPLETED",
                "message": f"Winning quote (₹{winning_quote.final_total}) exceeds budget cap (₹{rfq.max_budget_inr}) by ₹{counter.gap_amount}. Emitted counter-offer request.",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "data": counter.model_dump()
            })

            # Supplier applies bounded dynamic concession
            agreed_quote = supplier_agent.apply_concession(winning_supplier_profile, winning_quote, counter)
            trace_steps.append({
                "step_name": "Dynamic Concession Applied",
                "actor": f"SupplierAgent ({winning_quote.supplier_name})",
                "status": "COMPLETED",
                "message": f"Supplier AI approved dynamic concession. New adjusted total: ₹{agreed_quote.final_total} (Within daily budget bounds & Floor Price protected).",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "data": agreed_quote.model_dump()
            })

        # Step 6: Generate Signed Proposal Contract
        proposal_id = f"prop_{uuid.uuid4().hex[:8]}"
        payload_for_hash = {
            "proposal_id": proposal_id,
            "rfq_id": rfq.rfq_id,
            "winning_supplier_id": agreed_quote.supplier_id,
            "subtotal": agreed_quote.subtotal,
            "total_discount": agreed_quote.total_discount,
            "final_total": agreed_quote.final_total,
            "items": [item.model_dump() for item in agreed_quote.items]
        }
        proposal_hash = generate_proposal_hash(payload_for_hash)

        final_offer = A2A_FinalOffer(
            proposal_id=proposal_id,
            rfq_id=rfq.rfq_id,
            winning_supplier_id=agreed_quote.supplier_id,
            winning_supplier_name=agreed_quote.supplier_name,
            items=agreed_quote.items,
            subtotal=agreed_quote.subtotal,
            total_discount=agreed_quote.total_discount,
            final_total=agreed_quote.final_total,
            negotiation_summary=f"Mutually agreed A2A deal with '{agreed_quote.supplier_name}' including dynamic aging discounts.",
            proposal_hash=proposal_hash,
            created_at=datetime.now(timezone.utc).isoformat(),
            expires_at=(datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()
        )

        # Step 7: Deterministic Policy Evaluation
        policy_eval = policy_engine.evaluate(final_offer, buyer_ctx, check_signature=True)
        trace_steps.append({
            "step_name": "Deterministic Policy Gate Evaluation",
            "actor": "PolicyEngine",
            "status": "COMPLETED" if policy_eval.allowed_execution else "BLOCKED",
            "message": f"Policy Verdict: {policy_eval.status.value} - {policy_eval.summary_reason}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": policy_eval.model_dump()
        })

        return {
            "profile_type": profile_type,
            "execution_mode": execution_mode,
            "buyer_context": buyer_ctx,
            "custom_query": target_sku,
            "critical_items": critical_items,
            "active_rfq": rfq,
            "supplier_quotes": quotes,
            "ranked_quotes": ranked_quotes,
            "winning_quote": agreed_quote,
            "counter_offer": counter if needs_counter else None,
            "final_offer": final_offer,
            "policy_evaluation": policy_eval,
            "trace_steps": trace_steps,
            "is_fallback_mode": True,
            "error": None
        }


fallback_engine = ScriptedFallbackEngine()
