import logging
import os
import httpx
from typing import Dict, Any, Optional
from app.gateway.event_bus import bus
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
        item_name = payload.get("item_name", "Handcrafted Bamboo Lamp")
        amount = float(payload.get("amount", 3149.0))
        discount_pct = float(payload.get("discount", 10.0))
        base_price = float(payload.get("base_price", 3499.0))
        merchant_id = event.get("merchant_id", "jiva_demo")
        correlation_id = event.get("correlation_id", "")
        recovery_attempt_id = payload.get("recovery_attempt_id", "")

        logger.info(f"📧 [EmailService] Dispatching checkout email to {to_email} for ₹{int(amount)}")
        
        status, msg_id = await EmailService.send_checkout_email(
            to_email=to_email,
            link_url=link_url,
            item_name=item_name,
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
        base_price: float = 3499.0
    ) -> tuple[str, str]:
        discount_amount = base_price - final_amount
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
        <span class="badge">✨ Kinato AI Concierge VIP Deal</span>
        <h2 style="margin: 0 0 4px 0; color: #0f172a; font-size: 22px; letter-spacing: -0.5px;">JIVA LIFESTYLE</h2>
        <p style="color: #64748b; font-size: 13px; margin: 0 0 16px 0;">Artisan Masterpiece Collection</p>
        <p style="color: #334155; font-size: 14px; line-height: 1.5;">Hey Dhruv! Great speaking with you on the phone. As promised, your exclusive negotiated discount has been unlocked and secured.</p>
        
        <div class="product-box">
            <h3 style="margin: 0 0 6px 0; color: #0f172a; font-size: 16px;">🎋 {item_name}</h3>
            <p style="color: #64748b; font-size: 12px; margin: 0 0 14px 0;">Handcrafted in Assam • Direct from Rural Master Artisans</p>
            <div style="border-top: 1px solid #e2e8f0; padding-top: 12px; margin-top: 12px;">
                <span class="price">₹{int(final_amount):,}</span>
                <span class="strike">₹{int(base_price):,}</span>
                <span style="color: #059669; font-size: 13px; font-weight: 700; margin-left: 10px; background: #ecfdf5; padding: 3px 8px; border-radius: 6px;">{int(discount_pct)}% OFF (-₹{int(discount_amount):,})</span>
            </div>
        </div>

        <a href="{link_url}" class="btn">Complete Order via Razorpay →</a>
        
        <p style="text-align: center; color: #94a3b8; font-size: 11px; margin-top: 24px; line-height: 1.6;">
            🔒 Secured by Razorpay • Instant UPI (GPay, PhonePe, Paytm), Cards & NetBanking<br>
            🚚 Free Express Artisan Delivery & 30-Day Authenticity Guarantee
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
                        "subject": f"✨ Jiva Lifestyle: Your VIP Discount is Ready (₹{int(final_amount):,})",
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

email_service = EmailService()
bus.subscribe("email.send_requested", EmailService.handle_send_requested)
