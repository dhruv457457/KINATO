"""
================================================================================
FILE: app/main.py
MODULE: Module 3 - FastAPI Application Entry Point & API Gateway
--------------------------------------------------------------------------------
WHAT THIS FILE DOES:
Main FastAPI server for Kinato. Provides all REST, SSE, Webhook, and MCP endpoints:

ENDPOINTS EXPOSED:
  1. `GET /health`: Health check and system status.
  2. `GET /.well-known/agent-catalog.json`: UAP/ACP Agent-Readable Catalog standard.
  3. `GET /api/v1/inventory/status`: Live stock levels, burn rates, and DIR calculations.
  4. `POST /api/v1/a2a/negotiate`: Trigger A2A multi-agent reverse bidding.
  5. `GET /api/v1/a2a/stream`: Server-Sent Events (SSE) live negotiation stream.
  6. `POST /api/create-order` & `POST /api/v1/payments/create-order`: Razorpay Orders API.
  7. `POST /api/verify-payment` & `POST /api/v1/payments/verify`: HMAC signature verification.
  8. `POST /api/webhooks/razorpay`: Razorpay asynchronous webhook listener.
  9. `GET /api/v1/proofs/list` & `GET /api/v1/proofs/{id}`: Cryptographic proof receipts.
  10. `POST /mcp/tools/list` & `POST /mcp/tools/call`: FastMCP tool execution.
================================================================================
"""
import json
from contextlib import asynccontextmanager
from typing import Dict, Any, Optional
from fastapi import FastAPI, Request, HTTPException, Query, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from app.core.config import settings
from app.core.security import verify_razorpay_payment_signature
from app.db.init_db import init_db
from app.db.database import get_db
from app.models.enums import BusinessProfileType, ExecutionMode
from app.models.payment import CreateRazorpayOrderRequest, VerifyPaymentRequest
from app.knowledge.inventory import inventory_repo
from app.knowledge.suppliers import supplier_repo
from app.agents.service import agent_service
from app.payments.razorpay_client import razorpay_rails
from app.payments.webhooks import webhook_handler
from app.payments.state_machine import state_machine, TransactionState
from app.mcp.server import mcp_server


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize SQLite database and seed data on boot
    init_db()
    yield


app = FastAPI(
    title="Kinato A2A Commerce & Restock Protocol",
    description="Autonomous B2B Micro-Procurement & Agent-to-Agent Commerce Protocol on Razorpay",
    version="1.0.0",
    lifespan=lifespan
)

# ------------------------------------------------------------------------------
# CORS Middleware Configuration
# ------------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==============================================================================
# 1. Health & Open Agent Catalog Manifest (UAP / ACP Standard)
# ==============================================================================
@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "service": "Kinato A2A Commerce Protocol",
        "environment": settings.ENVIRONMENT,
        "razorpay_mode": "test_sandbox",
        "llm_engine": settings.LLM_MODEL if settings.OPENROUTER_API_KEY else "deterministic_scripted_fallback"
    }


@app.get("/.well-known/agent-catalog.json")
def get_agent_readable_catalog():
    """Exposes standardized NPCI UAP / ACP schema for external AI agent discovery."""
    return {
        "protocol": "UAP/1.0",
        "standard": "Agentic Commerce Protocol (ACP)",
        "platform": "Kinato",
        "merchant_network": "Bangalore B2B Wholesalers",
        "razorpay_key_id": settings.RAZORPAY_KEY_ID,
        "capabilities": {
            "accepts_a2a_rfq": True,
            "supports_dynamic_fifo_pricing": True,
            "supports_autopay_mandates": True,
            "supports_mcp_tools": True
        },
        "endpoints": {
            "catalog_status": "/api/v1/inventory/status",
            "rfq_broadcast": "/api/v1/a2a/negotiate",
            "sse_stream": "/api/v1/a2a/stream",
            "mcp_server": "/mcp",
            "proof_verification": "/api/v1/proofs/list"
        },
        "supported_currencies": ["INR"]
    }


# ==============================================================================
# 2. Inventory & Multi-Supplier Status APIs
# ==============================================================================
@app.get("/api/v1/inventory/status")
def get_inventory_status(profile: str = Query("CLOUD_KITCHEN")):
    try:
        profile_type = BusinessProfileType(profile.upper())
    except ValueError:
        profile_type = BusinessProfileType.CLOUD_KITCHEN

    ctx = inventory_repo.get_context(profile_type)
    suppliers = supplier_repo.get_suppliers(profile_type)

    return {
        "buyer": ctx.model_dump(),
        "suppliers": [s.model_dump() for s in suppliers],
        "critical_count": len(inventory_repo.get_critical_items(profile_type))
    }


# ==============================================================================
# 3. A2A Negotiation & Real-time SSE Live Streaming APIs
# ==============================================================================
@app.post("/api/v1/a2a/negotiate")
async def execute_negotiation(
    profile: str = Query("CLOUD_KITCHEN"),
    mode: str = Query("ONE_CLICK_APPROVAL"),
    target_sku: Optional[str] = Query(None)
):
    profile_type = BusinessProfileType(profile.upper())
    exec_mode = ExecutionMode(mode.upper())

    result = await agent_service.execute_negotiation(profile_type, exec_mode, target_sku)

    # Save proposal to SQLite for integrity checking
    final_offer = result.get("final_offer")
    if final_offer:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO proposals (
                    proposal_id, rfq_id, profile_type, winning_supplier_id,
                    winning_supplier_name, items_json, subtotal, total_discount,
                    final_total, proposal_hash, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OFFER_READY')
            """, (
                final_offer.proposal_id, final_offer.rfq_id, profile_type.value,
                final_offer.winning_supplier_id, final_offer.winning_supplier_name,
                json.dumps([i.model_dump() for i in final_offer.items]),
                final_offer.subtotal, final_offer.total_discount, final_offer.final_total,
                final_offer.proposal_hash
            ))

    return {
        "winning_quote": result["winning_quote"].model_dump() if result["winning_quote"] else None,
        "final_offer": final_offer.model_dump() if final_offer else None,
        "policy_evaluation": result["policy_evaluation"].model_dump() if result["policy_evaluation"] else None,
        "ranked_quotes": [q.model_dump() for q in result["ranked_quotes"]],
        "trace_steps": result["trace_steps"],
        "is_fallback_mode": result["is_fallback_mode"]
    }


@app.get("/api/v1/a2a/stream")
async def stream_negotiation(
    profile: str = Query("CLOUD_KITCHEN"),
    mode: str = Query("ONE_CLICK_APPROVAL"),
    target_sku: Optional[str] = Query(None)
):
    profile_type = BusinessProfileType(profile.upper())
    exec_mode = ExecutionMode(mode.upper())

    return StreamingResponse(
        agent_service.stream_negotiation(profile_type, exec_mode, target_sku),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


# ==============================================================================
# 4. Razorpay Orders & Payment Verification APIs
# ==============================================================================
@app.post("/api/create-order")
@app.post("/api/v1/payments/create-order")
def create_razorpay_order(req: CreateRazorpayOrderRequest):
    """
    Mints an immutable Razorpay Order with Idempotency Key deduplication.
    """
    try:
        res = razorpay_rails.create_order(
            proposal_id=req.proposal_id,
            amount_inr=req.amount_inr,
            business_id=req.business_id,
            supplier_id=req.supplier_id,
            mode=req.mode,
            proposal_hash=req.proposal_hash
        )
        return res.model_dump()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create Razorpay order: {str(e)}")


@app.post("/api/verify-payment")
@app.post("/api/v1/payments/verify")
def verify_payment(req: VerifyPaymentRequest):
    """
    Verifies Razorpay payment signature via HMAC-SHA256 and records state.
    """
    is_valid = verify_razorpay_payment_signature(
        order_id=req.razorpay_order_id,
        payment_id=req.razorpay_payment_id,
        signature=req.razorpay_signature
    )

    if not is_valid:
        raise HTTPException(
            status_code=400,
            detail="Signature verification failed. Payment is unauthorized or tampered with."
        )

    # Transition order state to SUCCESS
    state_machine.transition(
        req.razorpay_order_id,
        TransactionState.SUCCESS,
        payment_id=req.razorpay_payment_id
    )

    return {
        "status": "success",
        "verified": True,
        "order_id": req.razorpay_order_id,
        "payment_id": req.razorpay_payment_id,
        "message": "Payment signature verified successfully. Funds settled on Razorpay rails."
    }


# ==============================================================================
# 5. Razorpay Webhook Handler
# ==============================================================================
@app.post("/api/webhooks/razorpay")
async def handle_razorpay_webhook(
    request: Request,
    x_razorpay_signature: Optional[str] = Header(None)
):
    raw_body = await request.body()
    if not x_razorpay_signature:
        raise HTTPException(status_code=400, detail="Missing X-Razorpay-Signature header")

    success, msg, data = webhook_handler.process_webhook(raw_body, x_razorpay_signature)
    if not success:
        raise HTTPException(status_code=400, detail=msg)

    return {"status": "ok", "message": msg, "data": data}


# ==============================================================================
# 6. Cryptographic Proof of Intent & Settlement Receipts APIs
# ==============================================================================
@app.get("/api/v1/proofs/list")
def list_proof_receipts():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM proof_receipts ORDER BY created_at DESC LIMIT 20")
        rows = cursor.fetchall()
        return [dict(r) for r in rows]


# ==============================================================================
# 7. Model Context Protocol (MCP) Tool Suite
# ==============================================================================
@app.get("/mcp/tools")
@app.post("/mcp/tools/list")
def list_mcp_tools():
    return {"tools": mcp_server.get_tool_definitions()}


@app.post("/mcp/tools/call")
async def call_mcp_tool(req: Request):
    body = await req.json()
    name = body.get("name")
    arguments = body.get("arguments", {})
    return await mcp_server.call_tool(name, arguments)
