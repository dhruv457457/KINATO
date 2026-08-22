"""
================================================================================
FILE: app/models/a2a.py
MODULE: Module 1 - Data Contracts
--------------------------------------------------------------------------------
WHAT THIS FILE DOES:
Defines schemas for the Agent-to-Agent (A2A) Commerce Protocol handshake:
  - A2A_RFQ: Broadcast intent from Buyer Agent.
  - QuoteLineItem: Individual line item inside a quote.
  - A2A_Quote: Competitive proposal from a Supplier Agent.
  - A2A_CounterOffer: Bounded counter-offer from Buyer Agent.
  - A2A_FinalOffer: Mutually agreed, cryptographically signed proposal contract.
================================================================================
"""
from typing import List, Optional
from pydantic import BaseModel
from app.models.enums import BusinessProfileType


class A2A_RFQ(BaseModel):
    """Step 1: Broadcast Request For Quote emitted by Buyer Agent."""
    rfq_id: str
    buyer_id: str
    business_name: str
    profile_type: BusinessProfileType
    primary_sku: str
    primary_item_name: str
    requested_qty: float
    unit: str
    max_budget_inr: float
    delivery_deadline_hours: float
    timestamp: str


class QuoteLineItem(BaseModel):
    """An individual line item inside an A2A Quote."""
    sku: str
    name: str
    quantity: float
    unit: str
    unit_price: float
    total_price: float
    cost_price: float
    is_aging_upsell: bool = False
    batch_age_days: Optional[int] = None
    discount_applied: float = 0.0


class A2A_Quote(BaseModel):
    """Step 2: Competitive Quote submitted by a Supplier Agent."""
    quote_id: str
    rfq_id: str
    supplier_id: str
    supplier_name: str
    items: List[QuoteLineItem]
    subtotal: float
    total_discount: float
    final_total: float
    delivery_fee: float
    delivery_sla_hours: float
    distance_km: float
    trust_score: float
    is_preferred: bool = False
    bundle_rationale: Optional[str] = None
    utility_score: float = 0.0
    expires_at_timestamp: str


class A2A_CounterOffer(BaseModel):
    """Step 3: Bounded counter-offer from Buyer Agent to winning Supplier."""
    counter_id: str
    rfq_id: str
    quote_id: str
    target_budget: float
    gap_amount: float
    reason: str


class A2A_FinalOffer(BaseModel):
    """Step 4: Cryptographically signed agreed proposal."""
    proposal_id: str
    rfq_id: str
    winning_supplier_id: str
    winning_supplier_name: str
    items: List[QuoteLineItem]
    subtotal: float
    total_discount: float
    final_total: float
    currency: str = "INR"
    negotiation_summary: str
    proposal_hash: str
    created_at: str
    expires_at: str
