"""
================================================================================
FILE: app/models/growth.py
MODULE: Module 1 - Merchant Revenue Growth & Campaign Domain Models
--------------------------------------------------------------------------------
WHAT THIS FILE DOES:
Defines Pydantic v2 schemas for:
  1. AI Upsell & Cross-Sell Recommendations
  2. FIFO Perishable Yield Management & Dynamic Markdowns
  3. AI Campaign Orchestrator (Promotional Drops to AI Buyers)
  4. Razorpay Payment Links API with QR & SMS metadata
================================================================================
"""
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime


class UpsellRule(BaseModel):
    rule_id: str
    primary_sku: str
    recommended_sku: str
    recommended_name: str
    recommended_category: str
    discount_pct: float = Field(default=0.10, description="Bundle discount percentage")
    margin_preserved_pct: float = Field(default=0.20, description="Gross profit margin preserved")
    rationale: str


class UpsellOpportunity(BaseModel):
    sku: str
    name: str
    unit: str
    original_price: float
    discounted_bundle_price: float
    savings_inr: float
    margin_pct: float
    affinity_score: float
    rationale: str


class YieldDiscountResult(BaseModel):
    sku: str
    name: str
    batch_age_days: int
    shelf_life_days: int
    age_ratio: float
    original_price: float
    recommended_markdown_price: float
    discount_amount: float
    margin_pct: float
    is_spoilage_risk: bool
    rationale: str


class CreateCampaignRequest(BaseModel):
    campaign_name: str
    merchant_id: str = "supp_dairy_direct"
    target_category: str
    discount_pct: float = Field(ge=5.0, le=40.0, default=15.0)
    duration_hours: float = 24.0
    featured_skus: List[str] = []
    broadcast_to_network: bool = True


class Campaign(BaseModel):
    campaign_id: str
    merchant_id: str
    campaign_name: str
    target_category: str
    discount_pct: float
    status: str = "ACTIVE"  # ACTIVE, EXPIRED, PAUSED
    duration_hours: float
    created_at: str
    expires_at: str
    total_rfqs_received: int = 0
    total_converted_orders: int = 0
    revenue_generated_inr: float = 0.0


class CreatePaymentLinkRequest(BaseModel):
    proposal_id: str
    amount_inr: float
    customer_name: str = "Business Owner"
    customer_email: str = "owner@business.local"
    customer_phone: str = "9876543210"
    description: str = "Kinato Autonomous Restock Order"
    expiry_minutes: int = 30


class PaymentLinkResponse(BaseModel):
    payment_link_id: str
    short_url: str
    amount_inr: float
    currency: str = "INR"
    status: str
    qr_code_url: Optional[str] = None
    created_at: str
    expires_at: Optional[str] = None
    proposal_id: str
