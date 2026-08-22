# Kinato Judge Demo & Verification Guide

Welcome to **Kinato** — Autonomous B2B Micro-Procurement & Agent-to-Agent Commerce Protocol on Razorpay Rails.

---

## 🚀 Quick Start (3 Steps, 60 Seconds)

### Step 1: Start the FastAPI Backend
```bash
cd backend
python -m uvicorn app.main:app --port 8000 --reload
```
* Backend starts at: `http://localhost:8000`
* Agent-Readable Catalog manifest: `http://localhost:8000/.well-known/agent-catalog.json`
* Swagger API Docs: `http://localhost:8000/docs`

### Step 2: Run the Automated Safety & Resilience Test Suite
```bash
python tests/run_tests.py
```
* Verifies Floor Price Protection, Cashflow Limits, HMAC Tamper Detection, and Razorpay Idempotency.

### Step 3: Start the Next.js Frontend Dashboard
```bash
cd frontend
npm install
npm run dev
```
* Open `http://localhost:3000` in your browser.

---

## 🎬 5-Minute Pitch Video Script & Walkthrough

### 0:00 - 0:30 ➔ The Problem (Hook)
* *"Indian cloud kitchens, cafes, and retail stores lose over ₹15,000/month to manual procurement delays and perishable waste. Kinato transforms static Razorpay merchants into autonomous, agent-transactable entities that negotiate restocks in real-time."*

### 0:30 - 1:00 ➔ The Architecture (Visual Diagram)
* Show the 4-Pillar Architecture in `README.md`:
  1. Agent-Readable Catalog (`/.well-known/agent-catalog.json`)
  2. Multi-Supplier LangGraph Reverse Bidding
  3. Deterministic Policy Gate & Floor Price Guard
  4. Razorpay Test Rails & Proof of Intent Ledger

### 1:00 - 3:00 ➔ Live Interactive Demo (3 Presets)
* **Demo 1 (Success + FIFO Aging Bundle Deal):**
  - Select 🍔 **Cloud Kitchen** vertical.
  - Click **"Auto-Restock All"**.
  - Watch the live A2A bidding stream: Supplier A and B submit quotes. Metro Foodservice Hub bundles an aging chipotle sauce at -₹160 discount to win the bid.
  - Click **"Approve & Pay with Razorpay"** $\rightarrow$ Razorpay standard checkout opens $\rightarrow$ Enter test UPI/card $\rightarrow$ Confetti celebration!
* **Demo 2 (Floor Price Refusal - Margin Shield):**
  - Click **Scenario 2** card.
  - Show that even if an AI suggests a deep discount, the Deterministic Policy Gate **BLOCKS** the transaction because $SP < CP \times 1.15$, protecting merchant gross margins.
* **Demo 3 (External AI Buyer via FastMCP):**
  - Show external AI agents (Claude Code / Cursor) connecting to `/mcp` to autonomously inspect inventory and trigger restocks.

### 3:00 - 4:00 ➔ Deep Engineering Rigor
* Open `FINDINGS.md` and show:
  1. HMAC-SHA256 proposal signing & constant-time verification.
  2. Razorpay Idempotency Journal (`kinato_{proposal_id}_{supplier_id}`).
  3. `UNCERTAIN` State Active Reconciliation.

### 4:00 - 5:00 ➔ Value for Razorpay
* *"Kinato turns Razorpay into the settlement layer for the global Agent-to-Agent commerce protocol race (UAP, ACP, AP2). Every merchant on Razorpay becomes transactable by AI buyers end to end."*

---

## 🔑 Test Razorpay Sandbox Credentials

* **Key ID:** `rzp_test_TSk4KG18ZnfUX7`
* **Test Card:** `4100 2800 0000 1007` (CVV: `123`, Expiry: `12/26`, OTP: `123456`)
* **Test UPI:** `success@razorpay`
