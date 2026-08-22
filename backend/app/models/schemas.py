"""
================================================================================
FILE: app/models/schemas.py
MODULE: Module 1 - Data Contracts & Pydantic v2 Models
--------------------------------------------------------------------------------
WHAT THIS FILE DOES:
Defines the strict, type-safe data contracts for the entire Kinato platform.

It categorizes schemas into 6 logical domains:
  1. Business Profiles & Enums (Cloud Kitchen, Tech Pantry, Retail Store).
  2. Buyer Inventory & Consumption (Stock on hand, daily burn rate, DIR, reorder qty).
  3. Supplier Warehouse & Catalog (Cost price, list price, FIFO batch age, floor price).
  4. A2A Protocol Handshake (A2A_RFQ, A2A_Quote, A2A_Counter, A2A_FinalOffer).
  5. Deterministic Policy Evaluation (Checks, status, actionable recommendations).
  6. Razorpay Payment Rails & Proof Receipts (Orders API, Verify request, Proof of Intent).

KEY MATHEMATICAL PROPERTIES EMBEDDED IN MODELS:
  - InventoryItem.days_remaining: DIR = current_stock / daily_burn_rate
  - InventoryItem.is_critical: DIR <= 1.5 days (triggers A2A-RFQ automatically)
  - SupplierProduct.floor_price: Cost Price * (1 + 0.15) [Minimum Margin Guard]
  - SupplierProduct.aging_ratio: batch_age_days / shelf_life_days [FIFO Aging Curve]
================================================================================
"""
from enum import Enum
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


# ==============================================================================
# 1. Enums
# ==============================================================================

class BusinessProfileType(str, Enum):
    """The 3 selectable business verticals supported by Kinato."""
    CLOUD_KITCHEN = "CLOUD_KITCHEN"
    TECH_PANTRY = "TECH_PANTRY"
    RETAIL_STORE = "RETAIL_STORE"


class ExecutionMode(str, Enum):
    """Execution authorization modes."""
    ONE_CLICK_APPROVAL = "ONE_CLICK_APPROVAL"
    AUTONOMOUS_AUTOPAY = "AUTONOMOUS_AUTOPAY"


class PolicyStatus(str, Enum):
    """Deterministic policy gate verdict statuses."""
    PASSED = "PASSED"
    BLOCKED = "BLOCKED"
    INVALIDATED = "INVALIDATED"


# ==============================================================================
# 2. Buyer Inventory & Consumption Models
# ==============================================================================

class InventoryItem(BaseModel):
    """
    Represents an inventory item tracked by the consuming business.
    Calculates Days of Inventory Remaining (DIR) and Restock Urgency.
    """
    sku: str = Field(description="Unique SKU identifier")
    name: str = Field(description="Product name")
    category: str = Field(description="Inventory category")
    current_stock: float = Field(description="Current units in storage")
    unit: str = Field(description="Measurement unit (kg, litres, packs, pcs)")
    daily_burn_rate: float = Field(description="Average units consumed per day")
    reorder_threshold_days: float = Field(default=1.5, description="DIR threshold to trigger RFQ")
    target_restock_days: float = Field(default=4.0, description="Target buffer days to replenish")

    @property
    def days_remaining(self) -> float:
        """DIR = Current Stock / Daily Burn Rate"""
        if self.daily_burn_rate <= 0:
            return 999.0
        return round(self.current_stock / self.daily_burn_rate, 2)

    @property
    def is_critical(self) -> bool:
        """Returns True if stock will deplete in <= reorder_threshold_days."""
        return self.days_remaining <= self.reorder_threshold_days

    @property
    def reorder_quantity(self) -> float:
        """Computes quantity needed to reach target_restock_days buffer."""
        target_stock = self.target_restock_days * self.daily_burn_rate
        needed = target_stock - self.current_stock
        return max(0.0, round(needed, 2))


class BuyerContext(BaseModel):
    """Represents the operational context, budgets, and inventory of a buyer."""
    business_id: str
    business_name: str
    profile_type: BusinessProfileType
    daily_budget_limit: float
    weekly_budget_limit: float
    weekly_spent_so_far: float
    preferred_supplier_ids: List[str] = Field(default_factory=list)
    inventory: List[InventoryItem]


# ==============================================================================
# 3. Supplier Warehouse & Catalog Models
# ==============================================================================

class SupplierProduct(BaseModel):
    """
    Represents a wholesale product in a supplier's warehouse.
    Tracks batch age, shelf-life, cost prices, and margin floors.
    """
    sku: str
    name: str
    category: str
    unit: str
    cost_price: float         # Wholesale procurement cost (CP)
    list_price: float         # Standard selling price (SP)
    minimum_margin_pct: float = 0.15  # Minimum 15% margin floor
    available_stock: float
    batch_age_days: int       # Days since harvest / manufacture
    shelf_life_days: int      # Total shelf life in days

    @property
    def floor_price(self) -> float:
        """Deterministic mathematical price floor: Cost Price * (1 + Min Margin)"""
        return round(self.cost_price * (1.0 + self.minimum_margin_pct), 2)

    @property
    def aging_ratio(self) -> float:
        """Ratio of batch age to total shelf life (0.0 to 1.0)"""
        if self.shelf_life_days <= 0:
            return 0.0
        return min(1.0, round(self.batch_age_days / self.shelf_life_days, 2))

    @property
    def is_aging_batch(self) -> bool:
        """True if batch has consumed >= 60% of shelf life (FIFO discount candidate)."""
        return self.aging_ratio >= 0.60


class SupplierProfile(BaseModel):
    """Represents a registered wholesale merchant in the Kinato network."""
    supplier_id: str
    name: str
    trust_score: float        # Historical fulfillment rate (0.0 to 1.0)
    distance_km: float        # Distance to buyer in kilometers
    delivery_sla_hours: float # Delivery SLA in hours
    is_razorpay_verified: bool = True
    catalog: List[SupplierProduct]


# ==============================================================================
# 4. A2A Commerce Protocol Handshake Models
# ==============================================================================

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


# ==============================================================================
# 5. Policy Engine & Guardrail Models
# ==============================================================================

class PolicyCheckResult(BaseModel):
    """Individual assertion check in policy evaluation."""
    check_name: str
    passed: bool
    details: str


class PolicyEvaluation(BaseModel):
    """Complete policy evaluation verdict with actionable suggestions."""
    proposal_id: str
    status: PolicyStatus
    allowed_execution: bool
    summary_reason: str
    checks: List[PolicyCheckResult]
    actionable_suggestion: Optional[str] = None


# ==============================================================================
# 6. Razorpay Rails & Proof Models
# ==============================================================================

class CreateRazorpayOrderRequest(BaseModel):
    proposal_id: str
    amount_inr: float
    proposal_hash: str
    business_id: str
    supplier_id: str
    mode: ExecutionMode = ExecutionMode.ONE_CLICK_APPROVAL


class CreateRazorpayOrderResponse(BaseModel):
    order_id: str
    amount_paise: int
    currency: str = "INR"
    key_id: str
    receipt_id: str
    proposal_id: str
    mode: ExecutionMode


class VerifyPaymentRequest(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str
    proposal_id: str


class ProofReceipt(BaseModel):
    """Immutable Proof of Intent & Settlement Receipt."""
    receipt_id: str
    timestamp: str
    business_name: str
    supplier_name: str
    items: List[QuoteLineItem]
    total_amount_inr: float
    payment_method: str
    razorpay_order_id: str
    razorpay_payment_id: str
    policy_hash: str
    signature_verified: bool
    status: str
