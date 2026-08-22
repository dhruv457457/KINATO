# Kinato Architecture Disclosure & Technical Trade-offs

This document outlines the intentional technical trade-offs, security boundaries, and production roadmap for **Kinato** (Razorpay AI Buildathon Track 01).

---

## 1. Intentional Architectural Trade-Offs

### 1.1 Local-First SQLite Persistence vs Cloud Postgres (Supabase)
* **Design Decision:** We chose an embedded SQLite database with Write-Ahead Logging (WAL) and JSON seed fixtures rather than requiring a cloud database instance on startup.
* **Rationale:** A hiring evaluation codebase should run in **0.5 seconds with zero external setup** upon `git clone`. Adding third-party cloud DB dependencies introduces setup friction and network failure modes during live evaluation.
* **Production Migration Path:** The SQLite repository layer (`app/db/database.py`) is decoupled from business logic. In production, changing `DATABASE_URL` to a PostgreSQL / Supabase connection string requires zero changes to agent or policy logic.

### 1.2 Deterministic Scripted Fallback vs Sole Reliance on LLM
* **Design Decision:** We paired our LangGraph Multi-Agent StateGraph with a zero-dependency deterministic fallback engine.
* **Rationale:** Financial procurement protocols cannot tolerate stochastic API timeouts or rate limits during business-critical restock operations.
* **Production Path:** In production, high-value bulk negotiations use fine-tuned LLMs with fallback failover to guaranteed deterministic rule engines.

---

## 2. Security Boundaries & Invariants

```text
[External AI / Client Request]
             │
             ▼
[1. Pydantic v2 Type Gate] ────► Blocks malicious prompt injection in numeric fields
             │
             ▼
[2. LangGraph State Handshake] ─► Multi-factor utility ranking across N merchants
             │
             ▼
[3. Deterministic Policy Gate] ─► Asserts SP >= CP * 1.15 AND Total <= Budget Limit
             │
             ▼
[4. HMAC-SHA256 Proposal Seal] ─► Verifies payload integrity against price tampering
             │
             ▼
[5. Razorpay Test-Mode Rails] ──► Mints Order with Idempotency Key & Settles via Webhook
```

---

## 3. Production Roadmap

1. **NPCI Universal Agent Protocol (UAP) Integration:** Expand `/.well-known/agent-catalog.json` into a live federated merchant registry.
2. **Dynamic Multi-Supplier Split Basket Optimization:** Automatically split large procurement orders across multiple suppliers when individual SKUs are cheaper at different warehouses.
3. **Razorpay Tokenized UPI AutoPay Recurring Mandates:** Automate end-to-end recurring daily kitchen restocks without human intervention within bounded pre-authorized budgets.
