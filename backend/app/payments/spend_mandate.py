"""
Kinato's own pre-authorized spend-mandate layer for the AI Commerce /
autonomous-buyer surface (app/commerce/mcp_server.py's Path A).

HONESTY NOTE (renamed from upi_reserve_pay.py): this file used to call
itself "Razorpay UPI Reserve Pay" and claim "compliance with NPCI/RBI
regulations for agentic commerce." That overstated what it does. Razorpay's
real UPI Autopay/mandate products require the *customer's own* one-time
in-app approval before any recurring debit can happen - a fully headless AI
agent cannot complete that step on its own, so a real end-to-end autonomous
UPI settlement is not something this backend can produce by itself.

What this file actually is: a Kinato-enforced daily spending cap (deterministic,
never decided by an LLM) authorized once by the merchant, checked before every
autonomous purchase, layered on top of a REAL Razorpay Order created for
record-keeping on each attempt. If the Razorpay API call itself fails, the
attempt fails too - it never fabricates a success. Order creation succeeding
is a real financial artifact (visible on the merchant's Razorpay dashboard);
it is not the same thing as a captured/settled payment, and this module does
not claim otherwise.
"""
import uuid
import logging
from typing import Optional, Dict, Any
from app.core.config import settings
from app.db.database import get_db

logger = logging.getLogger(__name__)

try:
    import razorpay
    RAZORPAY_INSTALLED = True
except ImportError:
    razorpay = None
    RAZORPAY_INSTALLED = False


class SpendMandateService:
    """
    Kinato's daily-spend-cap authorization for AI-buyer autonomous purchases.

    - Merchant authorizes a daily cap once (create_mandate).
    - Every autonomous purchase attempt checks the cap deterministically
      BEFORE calling Razorpay - the LLM never decides whether spend is
      allowed.
    - A real Razorpay Order is created per attempt for an auditable record.
      If that API call fails, the attempt is reported as failed - not
      silently treated as a success.
    """

    def __init__(self):
        self._client = None
        self._init_client()

    def _init_client(self):
        if RAZORPAY_INSTALLED and settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET:
            try:
                self._client = razorpay.Client(
                    auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET)
                )
                logger.info("Razorpay client initialized successfully")
            except Exception as e:
                logger.error(f"Failed to init Razorpay client: {e}")
                self._client = None

    def create_mandate(
        self,
        business_id: str,
        customer_email: str,
        customer_phone: str,
        daily_limit_inr: float,
        description: str = "Kinato Autonomous Restock Mandate"
    ) -> Dict[str, Any]:
        """
        Creates a Kinato-side daily-spend-cap mandate. The merchant authorizes
        this ONCE - after which the AI agent can autonomously transact within
        the daily_limit_inr cap, subject to the check in
        execute_autonomous_payment. Also creates a Razorpay subscription plan
        as a real, visible record of the authorized amount (not itself a
        payment - Razorpay has no direct "authorize a daily cap" primitive).
        """
        amount_paise = int(daily_limit_inr * 100)
        mandate_ref = f"mnd_{uuid.uuid4().hex[:12]}"

        if self._client:
            try:
                plan = self._client.plan.create({
                    "period": "daily",
                    "interval": 1,
                    "item": {
                        "name": description,
                        "amount": amount_paise,
                        "currency": "INR",
                        "description": f"Daily AI procurement mandate for {business_id}"
                    }
                })
                mandate_ref = plan.get("id", mandate_ref)
                logger.info(f"Created Razorpay mandate plan {mandate_ref} for {business_id}")
            except Exception as e:
                logger.warning(f"Razorpay mandate plan creation failed, tracking cap locally only: {e}")

        # Store mandate in DB
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS upi_mandates (
                    mandate_id TEXT PRIMARY KEY,
                    business_id TEXT NOT NULL,
                    customer_email TEXT,
                    customer_phone TEXT,
                    daily_limit_inr REAL NOT NULL,
                    daily_spent_inr REAL NOT NULL DEFAULT 0,
                    razorpay_plan_id TEXT,
                    status TEXT NOT NULL DEFAULT 'ACTIVE',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_reset_date DATE DEFAULT CURRENT_DATE
                )
            """)
            cursor.execute("""
                INSERT INTO upi_mandates (
                    mandate_id, business_id, customer_email, customer_phone,
                    daily_limit_inr, razorpay_plan_id, status
                ) VALUES (%s, %s, %s, %s, %s, %s, 'ACTIVE')
                ON CONFLICT (mandate_id) DO NOTHING
            """, (mandate_ref, business_id, customer_email, customer_phone,
                  daily_limit_inr, mandate_ref))

        return {
            "mandate_id": mandate_ref,
            "business_id": business_id,
            "daily_limit_inr": daily_limit_inr,
            "status": "ACTIVE",
            "message": f"AI agent authorized to spend up to ₹{daily_limit_inr:.0f}/day autonomously, capped by Kinato"
        }

    def execute_autonomous_payment(
        self,
        mandate_id: str,
        proposal_id: str,
        amount_inr: float,
        supplier_id: str,
        supplier_name: str,
        description: str = "Kinato AI Restock"
    ) -> Dict[str, Any]:
        """
        Executes an autonomous purchase attempt against an existing mandate.
        The AI agent calls this - no human approval required per transaction.

        Safety: checks the daily spending cap BEFORE calling Razorpay at all.
        If the cap is exceeded, the transaction is BLOCKED automatically and
        no API call is made. If the Razorpay Order call itself fails, this
        returns success=False with the real error - it never reports success
        on a failed or skipped API call.
        """
        # 1. Load the mandate and check daily cap
        with get_db() as conn:
            cursor = conn.cursor()

            # Reset daily spend counter if it's a new day
            cursor.execute("""
                UPDATE upi_mandates
                SET daily_spent_inr = 0, last_reset_date = CURRENT_DATE
                WHERE mandate_id = %s AND last_reset_date < CURRENT_DATE
            """, (mandate_id,))

            cursor.execute("""
                SELECT daily_limit_inr, daily_spent_inr, status
                FROM upi_mandates WHERE mandate_id = %s
            """, (mandate_id,))
            mandate = cursor.fetchone()

        if not mandate:
            return {"success": False, "error": "Mandate not found. Please authorize a spending limit first."}

        if mandate["status"] != "ACTIVE":
            return {"success": False, "error": f"Mandate is {mandate['status']}. Cannot process payment."}

        remaining = mandate["daily_limit_inr"] - mandate["daily_spent_inr"]
        if amount_inr > remaining:
            return {
                "success": False,
                "error": f"Daily spending limit reached. Spent ₹{mandate['daily_spent_inr']:.0f} of ₹{mandate['daily_limit_inr']:.0f} today.",
                "daily_limit": mandate["daily_limit_inr"],
                "daily_spent": mandate["daily_spent_inr"],
                "remaining": remaining
            }

        # 2. Record the attempt via a real Razorpay Order. No client
        # configured, or the API call itself failing, is a real failure -
        # never treated as a success.
        if not self._client:
            return {"success": False, "error": "Razorpay client not configured - cannot record this purchase."}

        try:
            order = self._client.order.create({
                "amount": int(amount_inr * 100),
                "currency": "INR",
                "receipt": f"kinato_auto_{proposal_id[:8]}",
                "notes": {
                    "mandate_id": mandate_id,
                    "proposal_id": proposal_id,
                    "supplier_id": supplier_id,
                    "payment_type": "kinato_autonomous_spend_mandate",
                    "description": description
                }
            })
            razorpay_order_id = order["id"]
        except Exception as e:
            logger.warning(f"Razorpay order creation failed for autonomous payment: {e}")
            return {"success": False, "error": f"razorpay_order_creation_failed: {e}"}

        # 3. Update daily spend tracker - only after the real order call succeeded.
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE upi_mandates
                SET daily_spent_inr = daily_spent_inr + %s
                WHERE mandate_id = %s
            """, (amount_inr, mandate_id))

        logger.info(f"Autonomous purchase recorded: ₹{amount_inr} for {supplier_name} via mandate {mandate_id}, order {razorpay_order_id}")

        return {
            "success": True,
            "payment_id": razorpay_order_id,
            "razorpay_order_id": razorpay_order_id,
            "amount_inr": amount_inr,
            "supplier_name": supplier_name,
            "mandate_id": mandate_id,
            "daily_spent": mandate["daily_spent_inr"] + amount_inr,
            "daily_limit": mandate["daily_limit_inr"],
            "message": f"Autonomous purchase of ₹{amount_inr:.0f} to {supplier_name} recorded as Razorpay order {razorpay_order_id}, under the ₹{mandate['daily_limit_inr']:.0f}/day Kinato cap"
        }

    def get_mandate_status(self, business_id: str) -> Optional[Dict[str, Any]]:
        """Get the active mandate for a business."""
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM upi_mandates
                WHERE business_id = %s AND status = 'ACTIVE'
                ORDER BY created_at DESC LIMIT 1
            """, (business_id,))
            result = cursor.fetchone()
            return dict(result) if result else None

    def revoke_mandate(self, mandate_id: str) -> Dict[str, Any]:
        """Merchant can revoke the AI agent's autonomous payment authority at any time."""
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE upi_mandates SET status = 'REVOKED' WHERE mandate_id = %s
            """, (mandate_id,))
        return {"success": True, "mandate_id": mandate_id, "status": "REVOKED"}


# Singleton
spend_mandate_service = SpendMandateService()
