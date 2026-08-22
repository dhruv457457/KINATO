"""
================================================================================
FILE: app/models/payment.py
MODULE: Module 1 - Data Contracts
--------------------------------------------------------------------------------
WHAT THIS FILE DOES:
Defines schemas for Razorpay Orders API, Payment Verification, and Proof Receipts.
================================================================================
"""
from typing import List
from pydantic import BaseModel
from app.models.enums import ExecutionMode
from app.models.a2a import QuoteLineItem


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
