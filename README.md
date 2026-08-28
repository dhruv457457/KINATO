# KINATO

> **Razorpay AI Buildathon 2026 — Track 03: AI Revenue Recovery**  
> *"Don't just identify the problem. Show measured money recovered across a batch, with compliant escalation, stopping rules, and an audit trail."*

**Kinato turns a merchant's existing website into an autonomous revenue surface for both humans and AI buyers.**

For humans, Kinato recovers revenue through context-aware voice and messaging. For AI buyers, Kinato exposes the merchant's catalog and checkout as machine-readable commerce infrastructure.

Rather than acting as a standalone marketplace, Kinato integrates directly via a lightweight SDK (`Kinato.init()`), transforming passive merchant websites into intelligent systems capable of real-time human recovery and external AI commerce.

---

## 🏗 The Architecture

We strictly separate **AI Reasoning** (Supervisors/Intelligence) from **Deterministic Services** (Safety, Policy, Execution) and the **Real-Time Runtime** (Audio transport).

```text
                 MERCHANT
                     │
                     ▼
             MERCHANT SUPERVISOR
                     │
          ┌──────────┴──────────┐
          ▼                     ▼
    REVENUE INTEL        MERCHANT QUERIES
          │
          ▼
 RECOVERY OPPORTUNITY
          │
          ▼
   IDENTITY RESOLUTION
          │
          ▼
     CONSENT GATE
          │
          ▼
   CALL ORCHESTRATOR
          │
     ┌────┴────┐
     ▼         ▼
VOICE RUNTIME  CUSTOMER INTEL
     │         │
     │    STRUCTURED STATE
     │         │
     └────┬────┘
          ▼
     NEXT ACTION
          │
     ┌────┴───────────┐
     ▼                ▼
POLICY ENGINE    COMMUNICATION
     │           CONSENT CHECK
     ▼                │
 OFFER             WhatsApp
     │                │
     └───────┬────────┘
             ▼
      PAYMENT EXECUTION
             │
             ▼
          RAZORPAY
             │
          WEBHOOK
             │
             ▼
          EVENT BUS
        ┌────┼─────┐
        ▼    ▼     ▼
      AUDIT MEMORY ATTRIBUTION
```

---

## 🚀 The Two WOW Demos (Core Loops)

### 1. Human Revenue Recovery (Priority 1)
A customer abandons a cart on Jiva's store. 10 seconds later, the Abandonment Detector fires (ensuring the cart wasn't already paid). Revenue Intel scores the opportunity, Identity resolves the phone number, and the Consent Gate approves outreach. The Call Orchestrator initiates a real outbound voice call via the **Voice Runtime (Twilio)**. 

The customer says, *"It's too expensive yaar."*  
**Customer Intel** parses the structured state: `Intent: purchase`, `Barrier: price`, and crucially stores the immutable `customer_words: "It's too expensive yaar"`.  
The LLM selects `next_action: request_offer(12%)`.  
The **Policy Engine** deterministically evaluates merchant rules (margins, max discounts) and counters: `{"decision": "MODIFY", "approved_discount": 8}`.  
The **Communication Consent** is re-checked, then a WhatsApp checkout payload is dispatched **during the live call**.  
The customer pays via Razorpay. The webhook hits the Event Bus, and the dashboard instantly displays the `Kinato-Attributed Revenue`.

### 2. AI-Buyer Commerce — *partially built, and honest about it*
An external AI buyer can search a catalog, receive a **Quote**, and have that
quote strictly revalidated (price changed, quote expired, inventory gone) before
any purchase is allowed. **That revalidation boundary is real and tested.**

What is **not** built: the catalog behind it is not yet multi-tenant, so
`create_purchase_intent` deliberately returns
`REJECTED: catalog_not_yet_multi_tenant` rather than guessing which merchant to
bill. There is no Agent Catalog manifest and no MCP transport shipped — earlier
drafts of this README advertised both; neither existed, so the claims have been
removed rather than left pointing at 404s. The dashboard marks this surface
"soon" instead of faking it.

---

## 🎯 Quick Start

### 1. Backend (FastAPI + Event Bus + Services)
```bash
cd backend
pip install -r requirements.txt
cp .env.example .env
python -m uvicorn app.main:app --port 8000 --reload
```

### 2. Frontend (Next.js - Merchant Command Center)
```bash
cd frontend
npm install
npm run dev
```
Open `http://localhost:3000` to access the Merchant Control Plane.
