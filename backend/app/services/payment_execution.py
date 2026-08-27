import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Dict, Any

from app.gateway.event_bus import bus
from app.services.razorpay_client_factory import get_client_for_merchant, RazorpayNotConnectedError
from app.db.repositories.merchants import MerchantNotFoundError

logger = logging.getLogger(__name__)


class PaymentExecutionError(Exception):
    """Raised when a real Razorpay payment link cannot be created - either
    the merchant hasn't connected a Razorpay account yet, or the live API
    call itself failed. There is no mock/fallback link: a caller either gets
    a real, working payment link or a clear reason it couldn't get one."""


class PaymentExecutionService:
    """
    Owns Razorpay checkout generation. LLMs never touch this directly - it's
    called with an already policy-approved discount, and it never decides
    money terms itself. Uses each merchant's own connected Razorpay
    credentials (app/services/razorpay_client_factory.py) - never a single
    shared account.
    """

    @staticmethod
    async def generate_recovery_checkout(
        merchant_id: str,
        checkout_id: str,
        customer_id: str,
        recovery_attempt_id: str,
        original_amount: float,
        approved_discount_percent: float
    ) -> Dict[str, Any]:
        """
        Creates a merchant-scoped Razorpay payment link for human recovery.
        Embeds the recovery_attempt_id/checkout_id as immutable metadata (Razorpay
        "notes") so the webhook receiver can attribute the payment back to Kinato
        with no guesswork.

        Raises PaymentExecutionError if the merchant hasn't connected Razorpay
        yet, or if the live API call fails - never returns a fake link.
        """
        try:
            client = get_client_for_merchant(merchant_id)
        except (RazorpayNotConnectedError, MerchantNotFoundError) as e:
            raise PaymentExecutionError(str(e)) from e

        logger.info(f"Generating Payment Link for Recovery Attempt {recovery_attempt_id}")

        discount_amount = original_amount * (approved_discount_percent / 100)
        final_amount = round(original_amount - discount_amount, 2)
        amount_paise = int(round(final_amount * 100))

        notes = {
            "recovery_attempt_id": recovery_attempt_id,
            "checkout_id": checkout_id,
            "customer_id": customer_id,
            "merchant_id": merchant_id,
            "discount_percent": approved_discount_percent,
            "original_amount_paise": int(round(original_amount * 100)),
            "kinato_touchpoint": "voice_recovery",
        }

        try:
            expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
            rzp_link = client.payment_link.create({
                "amount": amount_paise,
                "currency": "INR",
                "accept_partial": False,
                "expire_by": int(expires_at.timestamp()),
                "reference_id": recovery_attempt_id,
                "description": f"Recovery checkout for {checkout_id}",
                "notify": {"sms": False, "email": True},
                "notes": notes
            })
        except Exception as e:
            raise PaymentExecutionError(f"Razorpay Payment Link API call failed: {e}") from e

        payment_link_id = rzp_link["id"]
        payment_url = rzp_link["short_url"]
        logger.info(f"Created Razorpay Payment Link {payment_link_id}")

        await bus.publish(
            event_type="payment_link.created",
            payload={
                "recovery_attempt_id": recovery_attempt_id,
                "checkout_id": checkout_id,
                "payment_link_id": payment_link_id,
                "payment_url": payment_url,
                "final_amount": final_amount,
            },
            correlation_id=checkout_id,
            merchant_id=merchant_id,
            idempotency_key=f"plink_gen_{recovery_attempt_id}"
        )

        return {
            "payment_link_id": payment_link_id,
            "url": payment_url,
            "final_amount": final_amount
        }


payment_execution = PaymentExecutionService()
