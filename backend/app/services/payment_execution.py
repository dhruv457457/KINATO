import asyncio
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Dict, Any

from app.gateway.event_bus import bus
from app.services.razorpay_client_factory import get_client_for_merchant, RazorpayNotConnectedError
from app.db.repositories.merchants import MerchantNotFoundError

logger = logging.getLogger(__name__)

# Strong references for fire-and-forget work. asyncio holds a running task
# only weakly, so a bare create_task can be collected before it finishes -
# the same hazard _spawn_write exists for elsewhere in this codebase.
_BACKGROUND: set = set()


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)


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
        approved_discount_percent: float,
        offer_token: str = "",
    ) -> Dict[str, Any]:
        """
        Creates a merchant-scoped Razorpay payment link for human recovery.
        Embeds the recovery_attempt_id/checkout_id/offer_token as immutable
        metadata (Razorpay "notes") so the webhook receiver can attribute the
        payment back to Kinato with no guesswork, and so the payment link
        itself - a real Razorpay entity - carries the exact token the
        two-phase money gate (app/agents/tools.py check_offer/issue_offer)
        approved, not just the discount it produced.

        Raises PaymentExecutionError if the merchant hasn't connected Razorpay
        yet, or if the live API call fails - never returns a fake link.
        """
        # `get_client_for_merchant` is cached for 5 minutes, but on a MISS it
        # does a synchronous DB read and a Fernet decrypt - both on the event
        # loop, both during a live call. Off the loop with the API call
        # below.
        try:
            client = await asyncio.to_thread(get_client_for_merchant, merchant_id)
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
            "offer_token": offer_token,
            "kinato_touchpoint": "voice_recovery",
        }

        # The razorpay SDK is synchronous `requests`. Called directly from a
        # coroutine it does not merely block THIS call - it freezes the whole
        # event loop, so every other live call's Twilio webhook goes
        # unanswered for its duration, and asyncio cannot cancel it because
        # there is no await to cancel at. That is what "the server stops when
        # the policy engine runs" actually was: not a crash, a frozen loop.
        #
        # asyncio.to_thread, not run_db_async: that executor is deliberately
        # sized below the connection pool as backpressure (see
        # db/database.py), and putting a multi-second HTTP call on it would
        # turn a slow Razorpay into a stalled database.
        try:
            expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
            rzp_link = await asyncio.to_thread(
                client.payment_link.create,
                {
                    "amount": amount_paise,
                    "currency": "INR",
                    "accept_partial": False,
                    "expire_by": int(expires_at.timestamp()),
                    "reference_id": recovery_attempt_id,
                    "description": f"Recovery checkout for {checkout_id}",
                    "notify": {"sms": False, "email": True},
                    "notes": notes,
                },
            )
        except Exception as e:
            raise PaymentExecutionError(f"Razorpay Payment Link API call failed: {e}") from e

        payment_link_id = rzp_link["id"]
        payment_url = rzp_link["short_url"]
        logger.info(f"Created Razorpay Payment Link {payment_link_id}")

        # Announced, not awaited. The link already exists - this is the
        # record of it, and a customer on the phone should not be waiting
        # for a database write about something that has already happened.
        #
        # The idempotency key stays. A keyed publish claims the key with an
        # INSERT ... ON CONFLICT DO NOTHING before dispatching subscribers,
        # so backgrounding the WHOLE publish keeps that ordering intact and
        # still prevents recovered revenue being counted twice by a redeploy
        # landing between two Razorpay retries (see event_bus). Dropping the
        # key would have been the fast and wrong version of this.
        _spawn(
            bus.publish(
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
                idempotency_key=f"plink_gen_{recovery_attempt_id}",
            )
        )

        return {
            "payment_link_id": payment_link_id,
            "url": payment_url,
            "final_amount": final_amount,
            # Returned so the caller can STORE it. This was computed above
            # and thrown away, which meant nothing downstream could tell a
            # live link from a dead one - so no link could ever be reused,
            # and every attempt minted another against Razorpay's
            # thirty-per-account test ceiling.
            "expires_at": expires_at,
        }


payment_execution = PaymentExecutionService()
