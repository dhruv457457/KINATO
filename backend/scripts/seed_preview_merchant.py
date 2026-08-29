"""
Seed one throwaway merchant with a realistic, fully-populated recovery so
the dashboard can actually be looked at.

This exists because the most interesting screen in the product - the
recovery drawer's merged "what was said / what was done" timeline - is
behind session auth and shows nothing at all against an empty database.
Reviewing it therefore needed either a real phone call or a fixture, and a
fixture is repeatable.

Everything written here is REAL data through the real repositories: real
rows, real audit entries, real transcript turns. Nothing about the drawer
is mocked - it renders exactly what it would render for a genuine call. The
one thing that did not happen is the phone call itself; the utterances are
written by us, and this script is named `preview` rather than `demo` so
that distinction stays obvious.

    python scripts/seed_preview_merchant.py

Prints a merchant_id and a session token. Safe to run repeatedly - each run
creates its own merchant and cleans up nothing, so delete them when done:

    python scripts/seed_preview_merchant.py --cleanup
"""
import json
import secrets
import sys
import time
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

# Run from backend/, same as scripts/run_recovery_batch.py.
sys.path.insert(0, ".")
load_dotenv()

from app.core.auth import create_session_token, hash_password  # noqa: E402
from app.db.database import get_db  # noqa: E402
from app.db.init_db import init_db  # noqa: E402
from app.db.repositories import audit as audit_repo  # noqa: E402
from app.db.repositories import checkouts as checkouts_repo  # noqa: E402
from app.db.repositories import consents as consents_repo  # noqa: E402
from app.db.repositories import conversation_turns as turns_repo  # noqa: E402
from app.db.repositories import customers as customers_repo  # noqa: E402
from app.db.repositories import merchants as merchants_repo  # noqa: E402
from app.db.repositories import recovery_attempts as ra_repo  # noqa: E402

PREVIEW_EMAIL_DOMAIN = "preview.kinato.local"


def cleanup() -> None:
    """Removes every merchant this script has ever created, and their rows."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT merchant_id FROM merchants WHERE email LIKE %s",
            (f"%@{PREVIEW_EMAIL_DOMAIN}",),
        )
        ids = [dict(r)["merchant_id"] for r in cursor.fetchall()]
        for merchant_id in ids:
            for table in (
                "conversation_turns", "audit_log", "events", "offer_tokens",
                "recovery_attempts", "checkouts", "consents", "customers",
                "products", "api_keys", "merchant_policies", "merchants",
            ):
                cursor.execute(f"DELETE FROM {table} WHERE merchant_id = %s", (merchant_id,))
    print(f"Removed {len(ids)} preview merchant(s).")


def _turn(merchant_id, attempt_id, customer_id, index, speaker, text, **kw):
    turns_repo.record_turn(
        merchant_id=merchant_id,
        recovery_attempt_id=attempt_id,
        customer_id=customer_id,
        turn_index=index,
        speaker=speaker,
        text=text,
        **kw,
    )
    # The drawer orders by real timestamps, so the rows need to land in a
    # believable order rather than all within the same millisecond.
    time.sleep(0.02)


def _audit(merchant_id, attempt_id, action, decision, args, result, latency_ms):
    audit_repo.record_audit(
        actor="agent:llm",
        action=action,
        merchant_id=merchant_id,
        correlation_id=attempt_id,
        args=args,
        result=result,
        decision=decision,
        degraded=False,
        latency_ms=latency_ms,
    )
    time.sleep(0.02)


def main() -> None:
    init_db()

    tag = secrets.token_hex(3)
    merchant = merchants_repo.create_merchant(
        name="Loomwork",
        email=f"preview_{tag}@{PREVIEW_EMAIL_DOMAIN}",
        # A random throwaway. Nobody needs to know it - sign-in below uses a
        # session token minted directly, so no password is ever typed
        # anywhere.
        password_hash=hash_password(secrets.token_urlsafe(24)),
        store_url="https://loomwork.example",
    )
    merchant_id = merchant["merchant_id"]

    # Skip the onboarding funnel - this merchant exists to look at the
    # dashboard, not to walk the setup wizard.
    with get_db() as conn:
        conn.cursor().execute(
            "UPDATE merchants SET onboarding_step = 'done' WHERE merchant_id = %s", (merchant_id,)
        )

    customer = customers_repo.upsert_by_contact(
        merchant_id, email="asha@example.com", phone="+919000000001", name="Asha Menon"
    )
    customer_id = customer["customer_id"]
    consents_repo.record_consent(merchant_id, customer_id, "voice", "granted", source="preview_seed")

    # --- Case 1: the one worth screenshotting -------------------------
    # A card decline, a customer who does raise price, a 40% ask, and a
    # policy engine handing back 10%. Every refusal code in one drawer.
    checkout = checkouts_repo.create_checkout(
        merchant_id=merchant_id,
        amount_paise=249_900,
        cogs_paise=100_000,
        customer_id=customer_id,
        line_items=[{"product_id": "sku_runner", "name": "Handwoven Table Runner"}],
        source="razorpay_webhook",
    )
    checkout_id = checkout["checkout_id"]
    checkouts_repo.record_failure(
        checkout_id,
        {
            "error_code": "BAD_REQUEST_ERROR",
            "error_reason": "payment_failed",
            "error_description": "Issuer bank timed out, please try again",
            "error_source": "bank",
            "error_step": "payment",
            "method": "card",
        },
        "SOFT_DECLINE",
    )

    attempt = ra_repo.create_recovery_attempt(merchant_id, checkout_id, customer_id)
    attempt_id = attempt["recovery_attempt_id"]
    ra_repo.update_state(
        attempt_id,
        "PAYMENT_LINK_SENT",
        channel="voice",
        twilio_call_sid=f"CApreview{tag}",
        approved_discount_percent=10.0,
        final_amount_paise=224_910,
        rzp_payment_link_id="plink_preview_1",
        rzp_payment_link_url="https://rzp.io/i/preview1",
        plan=json.dumps({"opening_line": "Hi Asha, this is Loomwork calling about your order."}),
    )

    i = 0
    _turn(merchant_id, attempt_id, customer_id, i, "agent",
          "Hi Asha, this is Loomwork calling about your order."); i += 1
    _turn(merchant_id, attempt_id, customer_id, i, "customer",
          "Oh - yes, hi. The payment didn't go through I think.",
          stt_confidence=0.91); i += 1
    _turn(merchant_id, attempt_id, customer_id, i, "agent",
          "That's right - your bank timed out on our side, nothing to do with you. "
          "I can send you a working link right now."); i += 1
    # A turn we genuinely did not hear. The drawer marks it, and the money
    # tools refuse on it.
    _turn(merchant_id, attempt_id, customer_id, i, "customer",
          "mm the... it's a bit  [unclear]", stt_confidence=0.22); i += 1
    _audit(merchant_id, attempt_id, "check_offer", "DENY",
           {"requested_discount_percent": 20.0, "reason": "sounded like a price objection"},
           {"decision": "DENY", "reason": "REJECTED_LOW_CONFIDENCE",
            "detail": "speech confidence 0.22 is below 0.60 - ask them to repeat that "
                      "before discussing any price"},
           412)
    _turn(merchant_id, attempt_id, customer_id, i, "agent",
          "Sorry, the line broke up there - could you say that again?"); i += 1
    _turn(merchant_id, attempt_id, customer_id, i, "customer",
          "I said it's a bit expensive for me right now.", stt_confidence=0.88); i += 1
    # Their payment broke, so the first discount ask is refused outright.
    _audit(merchant_id, attempt_id, "check_offer", "DENY",
           {"requested_discount_percent": 40.0, "reason": "customer says too expensive"},
           {"decision": "DENY", "reason": "REJECTED_FULL_PRICE_FIRST",
            "detail": "this checkout failed as SOFT_DECLINE - their payment broke, they did "
                      "not object to the price. Send a working link at full price "
                      "(requested_discount_percent=0) unless they themselves say the price is too high"},
           388)
    _turn(merchant_id, attempt_id, customer_id, i, "agent",
          "So it's the price that's holding you up - is that right?"); i += 1
    _turn(merchant_id, attempt_id, customer_id, i, "customer",
          "Yes, exactly.", stt_confidence=0.95); i += 1
    # Confirmed. Now the ask is allowed - and capped.
    _audit(merchant_id, attempt_id, "check_offer", "MODIFY",
           {"requested_discount_percent": 40.0, "reason": "confirmed price objection"},
           {"decision": "MODIFY", "reason": "REJECTED_CEILING",
            "requested_percent": 40.0, "approved_percent": 10.0,
            "ceiling_percent": 10.0, "margin_floor_percent": 15.0,
            "final_amount_paise": 224910, "offer_token": "tok_preview_1"},
           2104)
    _turn(merchant_id, attempt_id, customer_id, i, "agent",
          "I can do 10% off - that brings it to 2,249. Shall I send that across?"); i += 1
    _turn(merchant_id, attempt_id, customer_id, i, "customer",
          "[pressed 1]", input_mode="dtmf"); i += 1
    _audit(merchant_id, attempt_id, "issue_offer", "ISSUED",
           {"offer_token": "tok_preview_1", "channel": "email"},
           {"status": "ISSUED", "approved_percent": 10.0, "final_amount_paise": 224910,
            "payment_url": "https://rzp.io/i/preview1"},
           1673)
    _turn(merchant_id, attempt_id, customer_id, i, "agent",
          "Done - that's in your inbox now. Thanks Asha!"); i += 1

    # --- Case 2: a promise to pay, so the promise panel has something ---
    checkout2 = checkouts_repo.create_checkout(
        merchant_id=merchant_id,
        amount_paise=489_900,
        cogs_paise=210_000,
        customer_id=customer_id,
        line_items=[{"product_id": "sku_rug", "name": "Jute Floor Rug"}],
        source="razorpay_webhook",
    )
    attempt2 = ra_repo.create_recovery_attempt(merchant_id, checkout2["checkout_id"], customer_id)
    attempt2_id = attempt2["recovery_attempt_id"]
    ra_repo.update_state(
        attempt2_id, "PROMISED",
        channel="voice",
        promised_at=datetime.now(timezone.utc) + timedelta(days=3),
        promised_amount_paise=489_900,
        promise_words="I'll pay on Friday, right after payday",
        rzp_payment_link_url="https://rzp.io/i/preview2",
        plan=json.dumps({"opening_line": "Hi Asha, Loomwork here about the rug you were looking at."}),
    )
    j = 0
    _turn(merchant_id, attempt2_id, customer_id, j, "agent",
          "Hi Asha, Loomwork here about the rug you were looking at."); j += 1
    _turn(merchant_id, attempt2_id, customer_id, j, "customer",
          "I do still want it, I just can't right now. I'll pay on Friday, right after payday.",
          stt_confidence=0.93); j += 1
    _audit(merchant_id, attempt2_id, "record_promise_to_pay", "RECORDED",
           {"pay_date": "in 3 days", "amount_inr": 4899.0,
            "customer_words": "I'll pay on Friday, right after payday"},
           {"status": "RECORDED", "promised_date": "friday", "outreach_paused_until": "friday"},
           291)
    _turn(merchant_id, attempt2_id, customer_id, j, "agent",
          "Understood - I'll leave it with you and send the link so it's waiting. "
          "We won't chase you before then."); j += 1

    token = create_session_token(merchant_id)
    print("\n" + "=" * 68)
    print("  Preview merchant seeded.")
    print(f"  merchant_id : {merchant_id}")
    print(f"  email       : {merchant['email']}")
    print(f"  recovery #1 : {attempt_id}   (refusals + merged timeline)")
    print(f"  recovery #2 : {attempt2_id}   (promise to pay)")
    print("=" * 68)
    print("\nSession token (set as the `kinato_session` cookie):\n")
    print(token)
    print("\nRemove everything this script created with: "
          "python scripts/seed_preview_merchant.py --cleanup\n")


if __name__ == "__main__":
    if "--cleanup" in sys.argv:
        cleanup()
    else:
        main()
