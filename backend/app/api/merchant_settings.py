"""
Merchant-authenticated settings: connecting a real Razorpay test-mode
account, and issuing/revoking the pk_/sk_ API keys used for event ingestion
(see app/api/events.py, Day 3). All routes require a valid session - there
is no unauthenticated fallback merchant here, unlike the still-transitional
dashboard/trigger routes (see policy_engine.py's docstring for why).
"""
import json
import logging
from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile, File, status
from pydantic import BaseModel, Field
from typing import Dict, List, Optional

from app.core.auth import get_current_merchant
from app.core.config import settings
from app.core.crypto import encrypt_secret, EncryptionNotConfiguredError
from app.db.repositories import merchants as merchants_repo
from app.db.repositories import api_keys as api_keys_repo
from app.db.repositories import products as products_repo
from app.db.repositories import policies as policies_repo
from app.db.repositories import events as events_repo
from app.db.database import run_db_async
from app.services import catalog_ingest
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


class ProfileRequest(BaseModel):
    # Bounded because this string is READ ALOUD on every call. A 400-character
    # "name" is not a business, it is an instruction hidden in a field the
    # agent speaks - so the length limit is a guard, not tidiness.
    name: str = Field(..., min_length=1, max_length=80)
    store_url: str = Field("", max_length=300)


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


@merchant_router.put("/profile")
async def update_profile(
    payload: ProfileRequest, current_merchant: dict = Depends(get_current_merchant)
):
    """Change the business name customers hear on the phone.

    This existed nowhere. The name was captured once at signup and threaded
    into every outbound call - "you are calling from X" - with no way to
    correct a typo, a rebrand, or a trading name that differs from whatever
    was typed while creating an account. A merchant could hear their own
    agent say the wrong company and have no recourse but a new account.

    Newlines are stripped rather than rejected. This value is interpolated
    into the agent's system prompt, and a name containing its own line
    breaks is the cheapest possible prompt injection - against the merchant
    themselves, but the money gates do not care who is asking, so it fails
    at the tools regardless. Stripping it keeps the prompt well-formed.
    """
    clean = " ".join(payload.name.split())
    if not clean:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="A business name cannot be blank."
        )
    merchant = await run_db_async(
        merchants_repo.update_profile, current_merchant["merchant_id"], clean, payload.store_url
    )
    logger.info(f"Merchant {current_merchant['merchant_id']} renamed to {clean!r}.")
    return {
        "name": merchant["name"],
        "store_url": merchant.get("store_url") or "",
        "spoken_as": f"you are calling from {merchant['name']}",
    }


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


_CATALOG_HEADER_PROMPT = """A merchant uploaded a product catalogue. These column headers could not be
matched to any known field by our own synonym list:

{unknown}

The fields we need are:
  sku         - the product's own code or identifier
  name        - what the product is called
  price       - what the customer is charged
  cogs        - what the merchant PAID for it (cost price)
  inventory   - units in stock
  description - free text about the product

Here are up to three example values from each unmatched column, which are often
more informative than the header:

{samples}

Already matched, do not propose these again: {taken}

Reply with JSON only: {{"mapping": {{"<field>": "<exact header>"}}}}
Only include a field you are confident about. Omit anything you are unsure of -
an unmapped column is corrected by the merchant in one click, a wrongly mapped
one is not noticed. Never map two fields to the same header."""


async def _ask_model_about_headers(
    unknown: List[str], samples: Dict[str, List[str]], taken: List[str]
) -> Dict[str, str]:
    """Last resort, for headers our synonym list did not recognise.

    Deliberately narrow. The model is not shown the file, is not asked to
    read any values into fields, and cannot cause anything to be written -
    it returns column names, which are then checked against the columns
    that actually exist. On a normal export it is never called at all,
    which is why catalogue upload works with no API key configured.
    """
    if not unknown or not settings.OPENROUTER_API_KEY:
        return {}
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
            messages=[{"role": "user", "content": _CATALOG_HEADER_PROMPT.format(
                unknown="\n".join(f"  - {h}" for h in unknown),
                samples=json.dumps(samples, indent=1, ensure_ascii=False)[:1500],
                taken=", ".join(taken) or "(none)",
            )}],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=300,
            timeout=15.0,
        )
        raw = json.loads(response.choices[0].message.content or "{}")
    except Exception as e:
        logger.warning(f"Catalogue header assist failed: {e}")
        return {}

    # Everything it says is checked against reality: a field we actually
    # have, a header the file actually contains, and nothing already taken.
    out: Dict[str, str] = {}
    for f, header in (raw.get("mapping") or {}).items():
        if f in catalog_ingest.ALL_FIELDS and header in unknown and header not in out.values():
            out[f] = header
    return out


@merchant_router.post("/onboarding/catalog/propose")
async def propose_catalog_mapping(
    file: UploadFile = File(...), current_merchant: dict = Depends(get_current_merchant)
):
    """Read a catalogue of unknown shape and say what we think it is.

    **Writes nothing.** It returns the mapping it worked out, a preview of
    the rows that would be created, and every row that would be rejected
    with the reason why. The merchant confirms, and the existing upload
    endpoint applies it.

    That split is not caution for its own sake. `cogs_paise` is what the
    merchant paid for the goods and it is one of the two inputs to the
    margin floor, so a column mapped wrongly here changes which discounts
    are legal for that merchant on every future call. It is exactly the
    kind of decision that should be shown to a person before it binds -
    the same arrangement as /policy/propose, where the model argues and a
    human decides.
    """
    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File is not valid UTF-8 text.")

    headers, rows, header_row = catalog_ingest.read_table(text)
    if not headers:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="That file has no readable rows.")

    proposal = catalog_ingest.propose_mapping(headers, header_row)

    # Only the leftovers reach the model, and only when something we need is
    # still missing. A file our own synonyms fully understood never leaves
    # the machine.
    assisted: Dict[str, str] = {}
    if proposal.unresolved_headers and not proposal.is_usable:
        samples = {
            h: [r.get(h, "") for r in rows[:3] if r.get(h)]
            for h in proposal.unresolved_headers
        }
        assisted = await _ask_model_about_headers(
            proposal.unresolved_headers,
            samples,
            [h for h in proposal.mapping.values() if h],
        )
        for f, header in assisted.items():
            if not proposal.mapping.get(f):
                proposal.mapping[f] = header

    products, rejected = catalog_ingest.build_products(rows, proposal.mapping)
    return {
        "header_row": header_row,
        "columns": headers,
        "mapping": proposal.mapping,
        # Which of them a model suggested, so the UI can mark those for a
        # closer look rather than presenting every guess with equal weight.
        "model_suggested": list(assisted.keys()),
        "unmapped_columns": [h for h in proposal.unresolved_headers if h not in assisted.values()],
        "notes": proposal.notes,
        "usable": all(proposal.mapping.get(f) for f in catalog_ingest.REQUIRED_FIELDS),
        "preview": products[:10],
        "total_rows": len(products),
        "rejected": rejected[:20],
        "rejected_total": len(rejected),
    }


@merchant_router.post("/onboarding/catalog")
async def upload_catalog(
    file: UploadFile = File(...),
    mapping: Optional[str] = Form(None),
    current_merchant: dict = Depends(get_current_merchant),
):
    """Applies a catalogue upload, upserting real product rows.

    Reads through catalog_ingest, so a file whose header row is not row one,
    whose columns are called "SKU Code" and "Selling Price", and whose
    prices read "Rs. 1,299/-" all import. Previously each of those rejected
    the whole file and told the merchant to go and edit their spreadsheet.

    `mapping` is the JSON the merchant confirmed on the propose step. When
    it is absent the same deterministic matching is re-run - so the endpoint
    still works for a straightforward file posted directly, and the confirm
    step is a safeguard rather than a hoop.
    """
    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File is not valid UTF-8 text.")

    headers, rows, header_row = catalog_ingest.read_table(text)
    if not headers:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="That file has no readable rows.")

    chosen: Dict[str, Optional[str]] = {}
    if mapping:
        try:
            supplied = json.loads(mapping)
        except (TypeError, ValueError):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="mapping is not valid JSON.")
        if not isinstance(supplied, dict):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="mapping must be an object.")
        # Only fields we have, only columns the file actually contains. A
        # confirmed mapping still arrives over the wire, so it is checked
        # against the file rather than trusted.
        chosen = {
            f: h for f, h in supplied.items()
            if f in catalog_ingest.ALL_FIELDS and (h is None or h in headers)
        }
    if not all(chosen.get(f) for f in catalog_ingest.REQUIRED_FIELDS):
        chosen = catalog_ingest.propose_mapping(headers, header_row).mapping

    missing = [f for f in catalog_ingest.REQUIRED_FIELDS if not chosen.get(f)]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Could not work out which column holds the {', '.join(missing)}. "
                f"Columns found: {', '.join(headers)}."
            ),
        )

    products, rejected = catalog_ingest.build_products(rows, chosen)
    imported = []
    for product in products:
        saved = products_repo.upsert_product(
            merchant_id=current_merchant["merchant_id"],
            product_id=product["product_id"],
            name=product["name"],
            price_paise=product["price_paise"],
            cogs_paise=product["cogs_paise"],
            inventory_count=product["inventory_count"],
            description=product["description"],
        )
        imported.append(saved["product_id"])
    skipped = [r["row"] for r in rejected]

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
    # How the merchant wants their agent to SOUND. Style, never authority.
    #
    # The column has been in merchant_policies since the schema was written
    # and read by nothing - the seventh dead policy column here, and the
    # one a merchant most obviously wants, since the agent phones their
    # customers using their name.
    #
    # It is capped because it goes into a live-call prompt: something long
    # enough to bury the rules above it is not a persona, and a turn has
    # about five seconds. It is NOT in _PROPOSABLE_FIELDS below - the "set
    # it in your own words" flow must never be able to write the agent's
    # own prompt, which is the model editing its own instructions one level
    # up. A merchant types this themselves.
    voice_persona: Optional[str] = Field(None, max_length=400)


class PolicyProposalRequest(BaseModel):
    instruction: str = Field(..., min_length=3, max_length=500)


@merchant_router.get("/policy")
async def get_policy(current_merchant: dict = Depends(get_current_merchant)):
    return policies_repo.get_policy(current_merchant["merchant_id"])


@merchant_router.put("/policy")
async def update_policy(payload: PolicyUpdateRequest, current_merchant: dict = Depends(get_current_merchant)):
    updates = {k: v for k, v in payload.model_dump().items() if v is not None}
    # Collapsed to one line for the same reason the business name is: this
    # lands inside the agent's system prompt, and a persona carrying its own
    # line breaks is a paragraph pretending to be a sentence. An empty
    # string is kept rather than dropped, so "" can clear the setting.
    if "voice_persona" in updates:
        updates["voice_persona"] = " ".join(str(updates["voice_persona"]).split())
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
