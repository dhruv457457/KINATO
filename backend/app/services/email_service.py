import json
import logging
import os
import httpx
from typing import Dict, Any, Optional
from app.gateway.event_bus import bus
from app.db.repositories import checkouts as checkouts_repo
from app.db.repositories import consents as consents_repo
from app.db.repositories import customers as customers_repo
from app.db.repositories import merchants as merchants_repo
from app.db.repositories import recovery_attempts as recovery_attempts_repo
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", "Kinato <onboarding@resend.dev>")
DEFAULT_CUSTOMER_EMAIL = os.getenv("CUSTOMER_EMAIL", "")

if not RESEND_API_KEY:
    logger.warning("RESEND_API_KEY not set - recovery emails will fail to send until backend/.env is configured.")

class EmailService:
    """
    Deterministic Email Service powered by Resend.
    Dispatches luxury, high-converting checkout emails with direct Razorpay payment links.
    """

    @staticmethod
    async def handle_send_requested(event: Dict[str, Any]):
        payload = event.get("payload", {})
        to_email = payload.get("customer_email") or DEFAULT_CUSTOMER_EMAIL
        link_url = payload.get("payment_url", "")
        # No fabricated fallbacks. These used to default to the deleted demo
        # product ("Handcrafted Bamboo Lamp"), its prices (3149/3499) and its
        # merchant ("jiva_demo"), which meant a real Loomwork customer was
        # emailed about a bamboo lamp they had never seen. If a field is
        # missing now the email says less, rather than saying something false.
        item_name = (payload.get("item_name") or "").strip()
        business_name = (payload.get("business_name") or "").strip()
        customer_name = (payload.get("customer_name") or "").strip()
        amount = float(payload.get("amount") or 0.0)
        discount_pct = float(payload.get("discount") or 0.0)
        base_price = float(payload.get("base_price") or amount)
        merchant_id = event.get("merchant_id", "")
        correlation_id = event.get("correlation_id", "")
        recovery_attempt_id = payload.get("recovery_attempt_id", "")

        logger.info(f"📧 [EmailService] Dispatching checkout email to {to_email} for ₹{int(amount)}")
        
        status, msg_id = await EmailService.send_checkout_email(
            to_email=to_email,
            link_url=link_url,
            item_name=item_name,
            business_name=business_name,
            customer_name=customer_name,
            final_amount=amount,
            discount_pct=discount_pct,
            base_price=base_price
        )

        if status == "delivered":
            await bus.publish(
                event_type="email.sent",
                payload={
                    "customer_email": to_email,
                    "payment_url": link_url,
                    "item_name": item_name,
                    "amount": amount,
                    "resend_id": msg_id,
                    "recovery_attempt_id": recovery_attempt_id
                },
                correlation_id=correlation_id,
                merchant_id=merchant_id
            )

    @staticmethod
    async def send_checkout_email(
        to_email: str,
        link_url: str,
        item_name: str,
        final_amount: float,
        discount_pct: float,
        base_price: float = 0.0,
        business_name: str = "",
        customer_name: str = ""
    ) -> tuple[str, str]:
        discount_amount = max(0.0, base_price - final_amount)
        has_discount = discount_pct > 0 and discount_amount > 0

        # Every line below degrades to saying LESS when a fact is missing,
        # never to saying something invented. The previous template asserted
        # a "negotiated discount unlocked" even at 0% off, which was simply
        # untrue on a full-price recovery.
        store_line = business_name.upper() if business_name else "YOUR ORDER"
        greeting = f"Hi {customer_name.split()[0]}! " if customer_name else ""
        intro = (
            "Great speaking with you. As promised, here's your checkout link with the discount we agreed."
            if has_discount
            else "Great speaking with you. Here's the link to finish your order at the price we discussed."
        )
        item_html = (
            f'<h3 style="margin: 0 0 6px 0; color: #0f172a; font-size: 16px;">{item_name}</h3>'
            if item_name else ""
        )
        price_extra = (
            f'<span class="strike">₹{int(base_price):,}</span>'
            f'<span style="color: #059669; font-size: 13px; font-weight: 700; margin-left: 10px; '
            f'background: #ecfdf5; padding: 3px 8px; border-radius: 6px;">'
            f'{int(discount_pct)}% OFF (-₹{int(discount_amount):,})</span>'
            if has_discount else ""
        )
        subject = (
            f"{business_name}: your {int(discount_pct)}% discount is ready (₹{int(final_amount):,})"
            if business_name and has_discount
            else (f"{business_name}: complete your order (₹{int(final_amount):,})" if business_name
                  else f"Complete your order (₹{int(final_amount):,})")
        )
        html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f8fafc; color: #0f172a; margin: 0; padding: 24px 12px; }}
        .card {{ max-width: 520px; margin: 0 auto; background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 24px; padding: 36px; box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.05); }}
        .badge {{ display: inline-block; background-color: #ecfdf5; color: #059669; padding: 6px 14px; border-radius: 9999px; font-size: 12px; font-weight: 700; margin-bottom: 20px; border: 1px solid #a7f3d0; }}
        .product-box {{ background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 18px; padding: 20px; margin: 24px 0; }}
        .btn {{ display: block; width: 100%; box-sizing: border-box; text-align: center; background: linear-gradient(135deg, #059669, #0d9488); color: #ffffff !important; padding: 16px 24px; border-radius: 16px; font-weight: 700; text-decoration: none; font-size: 16px; margin-top: 24px; box-shadow: 0 4px 14px 0 rgba(5, 150, 105, 0.35); }}
        .price {{ font-size: 26px; font-weight: 800; color: #0f172a; }}
        .strike {{ text-decoration: line-through; color: #94a3b8; font-size: 15px; margin-left: 10px; }}
    </style>
</head>
<body>
    <div class="card">
        <h2 style="margin: 0 0 16px 0; color: #0f172a; font-size: 22px; letter-spacing: -0.5px;">{store_line}</h2>
        <p style="color: #334155; font-size: 14px; line-height: 1.5;">{greeting}{intro}</p>

        <div class="product-box">
            {item_html}
            <div style="border-top: 1px solid #e2e8f0; padding-top: 12px; margin-top: 12px;">
                <span class="price">₹{int(final_amount):,}</span>
                {price_extra}
            </div>
        </div>

        <a href="{link_url}" class="btn">Complete Order via Razorpay →</a>
        
        <p style="text-align: center; color: #94a3b8; font-size: 11px; margin-top: 24px; line-height: 1.6;">
            🔒 Secured by Razorpay • Instant UPI (GPay, PhonePe, Paytm), Cards & NetBanking<br>
            This link was sent because you asked for it on our call.
        </p>
    </div>
</body>
</html>"""

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(
                    "https://api.resend.com/emails",
                    headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
                    json={
                        "from": EMAIL_FROM,
                        "to": [to_email],
                        "subject": subject,
                        "html": html_content
                    }
                )
                if resp.status_code in [200, 201]:
                    msg_id = resp.json().get("id", "")
                    logger.info(f"📧 Resend Email delivered to {to_email}! ID: {msg_id}")
                    return "delivered", msg_id
                else:
                    logger.warning(f"Resend HTTP {resp.status_code}: {resp.text}")
        except Exception as e:
            logger.warning(f"Resend Email dispatch exception: {e}")
        return "failed", ""

    @staticmethod
    async def handle_promise_lapsed(event: Dict[str, Any]):
        """The one reminder a lapsed promise earns.

        `recovery.promise_lapsed` has been published by the sweeper on a
        carefully-designed once-only schedule - guarded by
        promise_reminded_at so a customer who committed and then didn't pay
        is chased exactly once and never again - and until now **nothing
        subscribed to it**. The event fired, the row was marked reminded,
        and no reminder was ever sent to anybody. The restraint was real
        and the reminder was imaginary.

        Three refusals here, each of which would otherwise turn a helpful
        nudge into the thing merchants get complaints about:

          * They have paid. The sweeper's query already excludes paid
            checkouts, but this is the last moment before an email leaves,
            and a payment landing in between is exactly the race worth
            losing safely.
          * They have opted out - on ANY channel. Someone who said "don't
            contact me again" on the call did not mean "except by email".
          * We have no link to remind them of, in which case the honest
            thing is to send nothing rather than an email that just asks
            for money with no way to pay it.
        """
        payload = event.get("payload", {})
        merchant_id = event.get("merchant_id", "")
        recovery_attempt_id = payload.get("recovery_attempt_id", "")
        attempt = recovery_attempts_repo.get_recovery_attempt(recovery_attempt_id)
        if not attempt:
            return

        checkout = checkouts_repo.get_checkout(attempt["checkout_id"]) if attempt.get("checkout_id") else None
        if not checkout or checkout.get("status") == "paid":
            logger.info(f"Promise reminder for {recovery_attempt_id} skipped: already paid.")
            return

        customer_id = attempt.get("customer_id")
        if customer_id and consents_repo.has_opted_out(merchant_id, customer_id):
            logger.info(f"Promise reminder for {recovery_attempt_id} skipped: customer has opted out.")
            return

        link_url = attempt.get("rzp_payment_link_url") or ""
        if not link_url:
            logger.info(f"Promise reminder for {recovery_attempt_id} skipped: no payment link on file to remind them of.")
            return

        customer = customers_repo.get_customer(customer_id) if customer_id else None
        to_email = (customer or {}).get("email") or ""
        if not to_email:
            logger.info(f"Promise reminder for {recovery_attempt_id} skipped: no email address.")
            return

        merchant = merchants_repo.get_merchant(merchant_id)
        business_name = ((merchant or {}).get("name") or "").strip()

        item_name = ""
        try:
            line_items = json.loads(checkout.get("line_items") or "[]")
            names = [li.get("name") for li in line_items if isinstance(li, dict) and li.get("name")]
            item_name = ", ".join(names[:3])
        except (TypeError, ValueError):
            item_name = ""

        # The terms they actually promised against, where we have them.
        # final_amount_paise is what was quoted; falling back to the cart
        # total would mean reminding them of a number nobody agreed to.
        final_paise = attempt.get("final_amount_paise") or checkout.get("amount_paise") or 0
        discount_pct = float(attempt.get("approved_discount_percent") or 0.0)
        base_paise = checkout.get("amount_paise") or final_paise

        logger.info(f"Sending the one promise reminder for {recovery_attempt_id} to {to_email}.")
        status, msg_id = await EmailService.send_checkout_email(
            to_email=to_email,
            link_url=link_url,
            item_name=item_name,
            business_name=business_name,
            customer_name=(customer or {}).get("name", ""),
            final_amount=final_paise / 100.0,
            discount_pct=discount_pct,
            base_price=base_paise / 100.0,
        )

        await bus.publish(
            event_type="recovery.promise_reminder_sent" if status == "delivered" else "recovery.promise_reminder_failed",
            payload={
                "recovery_attempt_id": recovery_attempt_id,
                "customer_email": to_email,
                "payment_url": link_url,
                "resend_id": msg_id,
            },
            correlation_id=event.get("correlation_id", ""),
            merchant_id=merchant_id,
        )

email_service = EmailService()
bus.subscribe("email.send_requested", EmailService.handle_send_requested)
# recovery.promise_lapsed was published by the sweeper to NO subscriber at
# all - the reminder the code is visibly careful to send only once had
# never actually been sent to anyone. See handle_promise_lapsed.
bus.subscribe("recovery.promise_lapsed", EmailService.handle_promise_lapsed)
