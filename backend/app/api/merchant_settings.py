"""
Merchant-authenticated settings: connecting a real Razorpay test-mode
account, and issuing/revoking the pk_/sk_ API keys used for event ingestion
(see app/api/events.py, Day 3). All routes require a valid session - there
is no unauthenticated fallback merchant here, unlike the still-transitional
dashboard/trigger routes (see policy_engine.py's docstring for why).
"""
import csv
import io
import json
import logging
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from pydantic import BaseModel, Field
from typing import Optional

from app.core.auth import get_current_merchant
from app.core.config import settings
from app.core.crypto import encrypt_secret, EncryptionNotConfiguredError
from app.db.repositories import merchants as merchants_repo
from app.db.repositories import api_keys as api_keys_repo
from app.db.repositories import products as products_repo
from app.db.repositories import policies as policies_repo
from app.db.repositories import events as events_repo
from app.db.database import run_db_async
from app.services.razorpay_client_factory import validate_credentials_live, invalidate_cache

logger = logging.getLogger(__name__)
merchant_router = APIRouter(prefix="/api/merchant", tags=["merchant"])


class ConnectRazorpayRequest(BaseModel):
    key_id: str = Field(..., min_length=8)
    key_secret: str = Field(..., min_length=8)
    webhook_secret: str = ""


class CreateApiKeyRequest(BaseModel):
    key_type: str = Field(..., pattern="^(publishable|secret)$")


class AllowedOriginsRequest(BaseModel):
    origins: list[str]


class WebhookSecretRequest(BaseModel):
    webhook_secret: str = Field(..., min_length=8)


@merchant_router.post("/razorpay/connect")
async def connect_razorpay(payload: ConnectRazorpayRequest, current_merchant: dict = Depends(get_current_merchant)):
    if not payload.key_id.startswith("rzp_test_"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only Razorpay TEST-mode keys (rzp_test_...) are accepted.",
        )

    ok, message = validate_credentials_live(payload.key_id, payload.key_secret)
    if not ok:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)

    try:
        merchants_repo.set_razorpay_credentials(
            current_merchant["merchant_id"],
            key_id_enc=encrypt_secret(payload.key_id),
            key_secret_enc=encrypt_secret(payload.key_secret),
            webhook_secret_enc=encrypt_secret(payload.webhook_secret),
        )
    except EncryptionNotConfiguredError as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    invalidate_cache(current_merchant["merchant_id"])
    merchants_repo.set_onboarding_step(current_merchant["merchant_id"], "integrate")
    return {"status": "connected", "message": message}


@merchant_router.get("/webhook-url")
async def webhook_url(current_merchant: dict = Depends(get_current_merchant)):
    """The webhook URL a merchant should paste into Razorpay.

    This has to come from the SERVER, not be assembled by the browser from
    whatever API host it happens to be talking to. A merchant running the
    dashboard locally would otherwise be shown
    http://localhost:8000/webhooks/... - a URL Razorpay can never reach, and
    which fails as silence rather than as an error. settings.NGROK_URL is
    this deployment's own public base (Railway URL or tunnel); when it isn't
    configured we say so instead of handing back something unusable.
    """
    public_base = (settings.NGROK_URL or "").rstrip("/")
    return {
        "url": f"{public_base}/webhooks/razorpay/{current_merchant['merchant_id']}" if public_base else "",
        "public_base_configured": bool(public_base),
    }


@merchant_router.get("/razorpay/status")
async def razorpay_status(current_merchant: dict = Depends(get_current_merchant)):
    # webhook_secret_set is reported separately because a merchant can be
    # fully "connected" (keys valid) and still receive zero events: the
    # webhook receiver rejects anything it cannot HMAC-verify. Surfacing it
    # turns a silent dead end into something the dashboard can point at.
    return {
        "connected": bool(current_merchant.get("rzp_key_id_enc")),
        "webhook_secret_set": bool(current_merchant.get("rzp_webhook_secret_enc")),
    }


@merchant_router.put("/razorpay/webhook-secret")
async def set_webhook_secret(payload: WebhookSecretRequest, current_merchant: dict = Depends(get_current_merchant)):
    """Sets just the Razorpay webhook signing secret.

    Until this is set, POST /webhooks/razorpay/{merchant_id} rejects every
    incoming event ("has not configured a Razorpay webhook secret yet") -
    correct, since it refuses to trust unsigned webhooks, but it means
    recovery silently never starts. A merchant creates this secret in
    Razorpay's own webhook dialog, which is a different screen and a
    different moment from where they got their API keys, so it gets its own
    endpoint rather than forcing a full reconnect.
    """
    if not current_merchant.get("rzp_key_id_enc"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Connect your Razorpay account first, then add the webhook secret.",
        )
    try:
        merchants_repo.set_webhook_secret(
            current_merchant["merchant_id"], encrypt_secret(payload.webhook_secret.strip())
        )
    except EncryptionNotConfiguredError as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
    return {"status": "saved"}


@merchant_router.get("/api-keys")
async def list_api_keys(current_merchant: dict = Depends(get_current_merchant)):
    return {"keys": api_keys_repo.list_keys_for_merchant(current_merchant["merchant_id"])}


@merchant_router.post("/api-keys")
async def create_api_key(payload: CreateApiKeyRequest, current_merchant: dict = Depends(get_current_merchant)):
    raw_key, row = api_keys_repo.create_key(current_merchant["merchant_id"], payload.key_type)
    return {
        "key": raw_key,  # shown exactly once - the caller must save it now
        "key_id": row["key_id"],
        "key_prefix": row["key_prefix"],
        "warning": "This key is shown only once. Store it securely.",
    }


@merchant_router.post("/api-keys/{key_id}/revoke")
async def revoke_api_key(key_id: str, current_merchant: dict = Depends(get_current_merchant)):
    owned = [k for k in api_keys_repo.list_keys_for_merchant(current_merchant["merchant_id"]) if k["key_id"] == key_id]
    if not owned:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such API key on this account.")
    api_keys_repo.revoke_key(key_id)
    return {"status": "revoked"}


@merchant_router.get("/allowed-origins")
async def get_allowed_origins(current_merchant: dict = Depends(get_current_merchant)):
    return {"origins": merchants_repo.get_allowed_origins(current_merchant["merchant_id"])}


@merchant_router.post("/allowed-origins")
async def set_allowed_origins(payload: AllowedOriginsRequest, current_merchant: dict = Depends(get_current_merchant)):
    merchants_repo.set_allowed_origins(current_merchant["merchant_id"], payload.origins)
    return {"origins": payload.origins}


# ---------------------------------------------------------------------------
# Onboarding: catalog upload, policy, event verification, completion.
# ---------------------------------------------------------------------------

_REQUIRED_CATALOG_COLUMNS = {"sku", "name", "price"}


@merchant_router.get("/onboarding/events-check")
async def onboarding_events_check(current_merchant: dict = Depends(get_current_merchant)):
    """Powers the Integrate screen's live 'waiting for your first event…'
    poll. Real DB read - no timer, no simulated flip. The first event this
    merchant's key or webhook ever produced is what unblocks the funnel."""
    rows = events_repo.recent_events(current_merchant["merchant_id"], limit=1)
    if not rows:
        return {"received": False}
    latest = rows[0]
    if current_merchant.get("onboarding_step") == "integrate":
        merchants_repo.set_onboarding_step(current_merchant["merchant_id"], "catalog")
    return {
        "received": True,
        "event_type": latest["event_type"],
        "created_at": str(latest.get("created_at", "")),
    }


@merchant_router.post("/onboarding/integrate/skip")
async def onboarding_integrate_skip(current_merchant: dict = Depends(get_current_merchant)):
    """Advances past Integrate WITHOUT a verified event.

    Onboarding must never dead-end. events-check above only moves the
    merchant forward once a real webhook lands, which means a merchant who
    hasn't taken a payment since adding the webhook (or who is setting up
    before going live) had no way out of that step at all - the funnel's
    own guard would bounce them straight back to it forever. This is the
    server-side half of the Integrate screen's "continue without a verified
    event" affordance; the webhook URL stays available in Settings, and the
    first real event still works whenever it arrives.
    """
    if current_merchant.get("onboarding_step") == "integrate":
        merchants_repo.set_onboarding_step(current_merchant["merchant_id"], "catalog")
    return {"onboarding_step": "catalog", "verified": False}


@merchant_router.post("/onboarding/catalog")
async def upload_catalog(
    file: UploadFile = File(...), current_merchant: dict = Depends(get_current_merchant)
):
    """Parses a merchant-uploaded CSV and upserts real product rows,
    including cogs_paise - the field the old hardcoded
    `cart_details = {"cogs": 1500.0}` had no real backing for. Rejects
    (rather than silently skipping) a file missing the required columns or
    containing no parseable rows, since a silently-empty catalog looks
    identical to "skipped" from the merchant's side."""
    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File is not valid UTF-8 text.")

    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="CSV has no header row.")

    columns = {c.strip().lower() for c in reader.fieldnames}
    missing = _REQUIRED_CATALOG_COLUMNS - columns
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"CSV is missing required column(s): {', '.join(sorted(missing))}. "
                   f"Expected at least: sku, name, price (cogs and inventory optional but recommended).",
        )

    def _to_paise(value: Optional[str]) -> Optional[int]:
        if value is None or value.strip() == "":
            return None
        try:
            return round(float(value) * 100)
        except ValueError:
            return None

    imported, skipped = [], []
    for row in reader:
        row = {k.strip().lower(): (v or "").strip() for k, v in row.items()}
        sku, name = row.get("sku"), row.get("name")
        price_paise = _to_paise(row.get("price"))
        if not sku or not name or price_paise is None:
            skipped.append(row.get("sku") or "(missing sku)")
            continue
        product = products_repo.upsert_product(
            merchant_id=current_merchant["merchant_id"],
            product_id=sku,
            name=name,
            price_paise=price_paise,
            cogs_paise=_to_paise(row.get("cogs")),
            inventory_count=int(row["inventory"]) if row.get("inventory", "").isdigit() else 0,
        )
        imported.append(product["product_id"])

    if not imported:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No valid rows found - every row was missing a sku, name, or valid price.",
        )

    # Only advance the funnel if the merchant is still IN it. This same
    # endpoint backs the dashboard's Catalog re-upload, and an already-
    # onboarded merchant updating their prices must not be thrown back to
    # step 05 - the funnel guard would then eject them from the dashboard
    # entirely.
    if current_merchant.get("onboarding_step") == "catalog":
        merchants_repo.set_onboarding_step(current_merchant["merchant_id"], "policy")
    return {"imported": len(imported), "skipped": skipped, "product_ids": imported}


@merchant_router.post("/onboarding/catalog/skip")
async def skip_catalog(current_merchant: dict = Depends(get_current_merchant)):
    if current_merchant.get("onboarding_step") == "catalog":
        merchants_repo.set_onboarding_step(current_merchant["merchant_id"], "policy")
    return {"onboarding_step": "policy"}


class PolicyUpdateRequest(BaseModel):
    max_discount_percent: Optional[float] = Field(None, ge=0, le=100)
    minimum_margin_percent: Optional[float] = Field(None, ge=0, le=100)
    calling_start_hour: Optional[int] = Field(None, ge=0, le=23)
    # 24 is allowed on the END hour only, and only so that 0-24 can mean
    # "any hour". Capping it at 23 made true round-the-clock calling
    # unreachable: 0-23 silently skipped 23:00-00:00, and the natural
    # alternative (0-0) meant never.
    calling_end_hour: Optional[int] = Field(None, ge=0, le=24)
    auto_approval_threshold_inr: Optional[float] = Field(None, ge=0)
    # Whether this merchant actually has EMI enabled on their Razorpay
    # account. The agent offers instalments before a discount when this is
    # on (see failure_diagnosis.describe) - and offering instalments a
    # checkout cannot provide tells a customer something untrue about their
    # money, so it stays off until a human says otherwise.
    emi_available: Optional[bool] = None


class PolicyProposalRequest(BaseModel):
    instruction: str = Field(..., min_length=3, max_length=500)


@merchant_router.get("/policy")
async def get_policy(current_merchant: dict = Depends(get_current_merchant)):
    return policies_repo.get_policy(current_merchant["merchant_id"])


@merchant_router.put("/policy")
async def update_policy(payload: PolicyUpdateRequest, current_merchant: dict = Depends(get_current_merchant)):
    updates = {k: v for k, v in payload.model_dump().items() if v is not None}
    return policies_repo.update_policy(current_merchant["merchant_id"], updates)


# The fields a merchant may describe in words. Anything the model returns
# outside this set is dropped, not merged - a policy is the one object in
# this system where an unexpected key is a security question rather than a
# convenience.
_PROPOSABLE_FIELDS = {
    "max_discount_percent",
    "minimum_margin_percent",
    "calling_start_hour",
    "calling_end_hour",
    "auto_approval_threshold_inr",
    "emi_available",
}

_POLICY_PROPOSAL_PROMPT = """You translate a merchant's plain-English instruction into changes to their \
recovery policy. You do not decide anything; you only read what they asked for.

Their current policy:
{current}

What they said:
"{instruction}"

Return ONLY a JSON object with a "changes" object and a "summary" string. Put a field in "changes" ONLY if \
their instruction clearly asks for it - never restate a value that is not changing, and never fill in a \
field they did not mention.

Fields you may set:
  max_discount_percent        0-100. The most the agent may ever discount.
  minimum_margin_percent      0-100. Discounts are capped further to protect this margin.
  calling_start_hour          0-23, IST.
  calling_end_hour            0-24, IST. Use start 0 and end 24 for round-the-clock.
  auto_approval_threshold_inr rupees. The most the agent may give away on one cart unattended. 0 = no limit.
  emi_available               true/false. Only set true if they say EMI/instalments ARE available on their \
Razorpay account - it makes the agent offer instalments, and offering what a checkout cannot do is worse \
than staying quiet.

"summary" is one plain sentence saying what will change, in the merchant's own terms. If you cannot tell \
what they want, return an empty "changes" object and say so in "summary".

Never invent a number they did not give. "Be more generous" without a figure is not a number - say you need \
one."""


@merchant_router.post("/policy/propose")
async def propose_policy(
    payload: PolicyProposalRequest, current_merchant: dict = Depends(get_current_merchant)
):
    """Turn "never discount more than 10%" into a proposed policy change.

    **This endpoint writes nothing.** It returns a diff for a human to look
    at and approve, and that is the entire design, not caution for its own
    sake.

    Every guarantee this project makes rests on one arrangement: the model
    argues, and a deterministic policy engine decides. A model that can
    write the policy sets the ceiling it will later be bound by - the
    guardrail becomes model output one level up, and the ablation study
    stops meaning what it says. So the model reads an instruction and
    proposes; the merchant confirms; the existing PUT applies it.

    The proposal is validated through PolicyUpdateRequest - the SAME model
    the manual sliders go through - so a hallucinated 500% ceiling is
    rejected by bounds that already existed rather than by a second set
    written here that could drift from them.
    """
    merchant_id = current_merchant["merchant_id"]
    current = await run_db_async(policies_repo.get_policy, merchant_id)

    if not settings.OPENROUTER_API_KEY:
        return {
            "changes": {},
            "summary": "No language model is configured, so this cannot read your instruction. "
                       "The controls below still work.",
            "degraded": True,
        }

    readable = {k: current.get(k) for k in sorted(_PROPOSABLE_FIELDS) if k in current}
    prompt = _POLICY_PROPOSAL_PROMPT.format(
        current=json.dumps(readable, indent=1, default=str), instruction=payload.instruction
    )

    try:
        from openai import AsyncOpenAI
        from app.core.net import shared_ipv4_client

        client = AsyncOpenAI(
            base_url=settings.LLM_BASE_URL,
            api_key=settings.OPENROUTER_API_KEY,
            http_client=shared_ipv4_client("llm"),
        )
        response = await client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=400,
            timeout=20.0,
        )
        raw = json.loads(response.choices[0].message.content or "{}")
    except Exception as e:
        logger.warning(f"Policy proposal failed for {merchant_id}: {e}")
        return {
            "changes": {},
            "summary": "Could not read that just now. The controls below still work.",
            "degraded": True,
        }

    # Drop anything not proposable, then run what is left through the same
    # validation the manual form uses. Two filters on purpose: the first
    # stops an unexpected key reaching a money object at all, the second
    # stops an in-range-looking value that is not.
    proposed = {
        k: v for k, v in (raw.get("changes") or {}).items()
        if k in _PROPOSABLE_FIELDS and v is not None
    }
    try:
        validated = PolicyUpdateRequest(**proposed).model_dump()
    except Exception as e:
        logger.warning(f"Policy proposal for {merchant_id} failed validation: {e}")
        return {
            "changes": {},
            "summary": "That would put a value outside what this policy allows, so nothing was proposed.",
            "degraded": False,
        }

    changes = {
        k: v for k, v in validated.items()
        if v is not None and k in proposed and v != current.get(k)
    }
    return {
        "changes": changes,
        # What it currently is, so the UI can show before -> after rather
        # than a number with no context.
        "current": {k: current.get(k) for k in changes},
        "summary": str(raw.get("summary") or "")[:300],
        "degraded": False,
    }


@merchant_router.post("/onboarding/complete")
async def complete_onboarding(current_merchant: dict = Depends(get_current_merchant)):
    merchants_repo.set_onboarding_step(current_merchant["merchant_id"], "done")
    return {"onboarding_step": "done"}
