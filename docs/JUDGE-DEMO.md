# Kinato 2.0: Judge Demo & Verification Guide

> **Razorpay AI Buildathon 2026 — Track 01: AI Growth & Agentic Commerce**  
> *"Grow the merchant's revenue, and make them sellable to AI buyers"*

---

## 🚀 60-Second Quick Start

### 1. Start FastAPI Backend
```bash
cd backend
python -m uvicorn app.main:app --port 8000 --reload
```
* **Backend API**: `http://localhost:8000`
* **Agent-Readable Manifest**: `http://localhost:8000/.well-known/agent-catalog.json`
* **FastMCP Tool Endpoint**: `http://localhost:8000/mcp`
* **Swagger Docs**: `http://localhost:8000/docs`

### 2. Run Automated Safety & Resilience Invariant Suite
```bash
python tests/run_tests.py
```
* Runs 9 automated tests validating Floor Price Protection, Cashflow Limits, HMAC Tamper Detection, Razorpay Idempotency, AI Upsell Margins, FIFO Yield Markdown, and all 5 Chaos Invariants.

### 3. Start Next.js Frontend Command Center
```bash
cd frontend
npm run dev
```
* Open `http://localhost:3000` in your browser.

---

## 🎬 5-Minute Pitch Video Script & Walkthrough

### 0:00 - 0:45 ➔ The Hook & The "Why Now"
* *"Commerce is shifting from human clicks to autonomous AI buyers. But today, merchants have no way to sell to AI agents, and they lose 20% of revenue to perishable waste and unoptimized pricing. Kinato is the dual-sided Agentic Commerce Protocol on Razorpay rails that turns any merchant into an autonomous revenue engine."*

### 0:45 - 1:45 ➔ Merchant Revenue Engine (Pillar 1)
1. Open **Merchant Growth Portal** (`http://localhost:3000/merchant`).
2. Show **Dynamic Yield & FIFO Markdowns**: An aging batch of cheese block (60% shelf life used) is automatically priced with a dynamic markdown ($P \ge CP \times 1.15$) to prevent write-offs.
3. Show **AI Upsell Matrix**: When an AI buyer requests Cheese, the merchant's AI automatically bundles Brioche Buns & Chipotle Sauce at a 12% bundle discount, lifting Average Order Value (AOV) by +35%.
4. Show **1-Click Campaign Orchestrator**: Click *"Launch AI Campaign"* to broadcast promotional flash deals across the agent network.

### 1:45 - 3:00 ➔ Autonomous Buyer Restock & Razorpay Settlement (Pillars 2 & 4)
1. Open **Buyer Workspace** (`http://localhost:3000/dashboard`).
2. Type in chat: *"Restock Mozzarella Cheese and burger bakery staples"*.
3. Watch the real-time **Multi-Supplier Bidding War**: Supplier A and Supplier B compete. Metro Foodservice Hub bundles aging sauce to win the auction.
4. Show **Razorpay Rails**:
   * Click **"Approve & Pay"** $\rightarrow$ Razorpay Standard Checkout popup opens $\rightarrow$ Enter test UPI (`success@razorpay`) $\rightarrow$ Payment confirmed and settled!
   * Click **"Generate Shareable Payment Link / QR"** $\rightarrow$ Razorpay Payment Link generated with instant QR code for asynchronous mobile checkout.
   * Switch mode to **"AutoPay Mandate"** $\rightarrow$ Zero-click autonomous procurement under pre-authorized daily budget.

### 3:00 - 4:00 ➔ "The Bar" & Chaos Failure Injection (Pillar 3)
1. Open **Chaos Sandbox** (`http://localhost:3000/sandbox`).
2. Click **"Run All 5 Invariant Tests"**:
   * **Under-Cost Sale Attack** $\rightarrow$ Intercepted by Deterministic Policy Gate ($P < CP \times 1.15 \rightarrow \text{BLOCKED}$).
   * **HMAC Signature Tampering** $\rightarrow$ Tampered payload rejected via constant-time digest comparison.
   * **Webhook Drop Recovery** $\rightarrow$ Active reconciliation worker resolves `UNCERTAIN` state via Razorpay API.
   * **Cashflow Ceiling Breach** $\rightarrow$ Order blocked with remedial adjustment calculation.
   * **Double-Charge Deduplication** $\rightarrow$ Idempotency Journal returns cached order in 0.5ms with 0 duplicate orders.

### 4:00 - 5:00 ➔ External AI Interoperability & Strategic Value for Razorpay
* Open `skills/kinato-commerce/SKILL.md` and `/.well-known/agent-catalog.json`.
* *"Any external agent (Claude Code, Cursor, Perplexity) can plug into Kinato via MCP or Agent Skills. Kinato makes Razorpay the indispensable financial settlement layer for the global agent economy."*

---

## 🔑 Test Razorpay Sandbox Credentials

* **Key ID:** `rzp_test_TSk4KG18ZnfUX7`
* **Test Card:** `4100 2800 0000 1007` (CVV: `123`, Expiry: `12/26`, OTP: `123456`)
* **Test UPI:** `success@razorpay`
