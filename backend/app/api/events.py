"""
Event ingestion - the "does our platform even know a checkout happened"
gap. Two trust levels:

  - `sk_` (Authorization: Bearer sk_...) - full trust, server-to-server.
    Any allowed event type, amounts taken at face value.
  - `pk_` (X-Kinato-Key: pk_...) - restricted trust, browser-facing. Only a
    small allow-list of event types, and CORS is enforced per-merchant here
    in the route (see app/core/dynamic_cors.py for the preflight side of
    this - a single global allow-list can't express "each merchant has
    their own storefront origin").

Idempotency: an explicit `Idempotency-Key` header is honored; otherwise one
is derived from (merchant_id, event_type, checkout_id). Enforced by the
`events.idempotency_key` UNIQUE constraint via bus persistence, not just an
in-memory set.
"""
import json
import logging
import time
from typing import Optional, Dict, Any
from fastapi import APIRouter, Request, HTTPException, Response, status

from app.db.repositories import api_keys as api_keys_repo
from app.db.repositories import merchants as merchants_repo
from app.db.repositories import customers as customers_repo
from app.db.repositories import consents as consents_repo
from app.gateway.event_bus import bus

logger = logging.getLogger(__name__)
events_router = APIRouter()

# Events a pk_ (browser-facing, spoofable) key may send. Notably absent:
# checkout.abandoned (the sweeper is the sole authority on that - see
# app/gateway/sweeper.py) and payment.* (those only ever come from a
# Razorpay webhook, never a claim the browser can make).
PUBLISHABLE_ALLOWED_EVENTS = {"checkout.started", "checkout.updated", "cart.updated", "customer.identified"}

# Simple fixed-window rate limit for pk_ keys: demo-appropriate, not a
# sliding-window/distributed limiter. {key_id: (window_start, count)}
_RATE_LIMIT_WINDOW_S = 60
_RATE_LIMIT_MAX_REQUESTS = 100
_rate_limit_state: Dict[str, tuple] = {}


def _rate_limited(key_id: str) -> bool:
    now = time.time()
    window_start, count = _rate_limit_state.get(key_id, (now, 0))
    if now - window_start > _RATE_LIMIT_WINDOW_S:
        window_start, count = now, 0
    count += 1
    _rate_limit_state[key_id] = (window_start, count)
    return count > _RATE_LIMIT_MAX_REQUESTS


def _authenticate(request: Request, body_api_key: Optional[str] = None) -> tuple[Dict[str, Any], str]:
    """Returns (api_key_row, trust_level) or raises HTTPException.

    `body_api_key` supports the sendBeacon retry path (static/sdk/kinato.js):
    sendBeacon cannot carry custom headers, so on page unload the SDK embeds
    the key in the JSON body as `api_key` instead. Only publishable keys are
    ever accepted this way - safe because pk_ keys are meant to be public
    (their security is the origin allowlist + restricted event scope below,
    not secrecy), unlike sk_ secret keys which must never appear in a
    client-visible payload."""
    auth_header = request.headers.get("authorization", "")
    pk_header = request.headers.get("x-kinato-key", "") or (body_api_key if body_api_key and body_api_key.startswith("pk_") else "")

    if auth_header.startswith("Bearer "):
        raw_key = auth_header[len("Bearer "):].strip()
        key_row = api_keys_repo.get_by_raw_key(raw_key)
        if not key_row or key_row["key_type"] != "secret":
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid secret key.")
        return key_row, "full"

    if pk_header:
        key_row = api_keys_repo.get_by_raw_key(pk_header)
        if not key_row or key_row["key_type"] != "publishable":
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid publishable key.")
        if _rate_limited(key_row["key_id"]):
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Rate limit exceeded for this key.")
        return key_row, "restricted"

    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing API key (Authorization: Bearer sk_... or X-Kinato-Key: pk_...).")


def _normalize_payload(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Accepts both canonical snake_case and the SDK's historical camelCase
    (cartId/items) so an already-deployed older SDK build doesn't silently
    stop working the moment this ships - normalizes to one shape either way."""
    payload = dict(raw.get("payload") or {})
    if "cartId" in payload and "cart_id" not in payload:
        payload["cart_id"] = payload.pop("cartId")
    if "items" in payload and "product_ids" not in payload:
        items = payload.pop("items")
        payload["product_ids"] = [i.get("sku") or i.get("id") for i in items] if isinstance(items, list) else []
    return payload


@events_router.post("/events")
async def ingest_event(request: Request, response: Response):
    # Parsed before auth because the sendBeacon retry path (static/sdk/kinato.js)
    # can't carry a custom header - its key travels as `api_key` in the body.
    try:
        raw = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid JSON body.")

    key_row, trust_level = _authenticate(request, body_api_key=raw.get("api_key"))
    merchant_id = key_row["merchant_id"]

    if trust_level == "restricted":
        origin = request.headers.get("origin", "")
        allowed_origins = merchants_repo.get_allowed_origins(merchant_id)
        if origin and allowed_origins and origin not in allowed_origins:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "This origin is not registered for this merchant.")
        if origin:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Vary"] = "Origin"

    event_type = raw.get("event_type") or raw.get("event")
    if not event_type:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Missing event_type.")

    if trust_level == "restricted" and event_type not in PUBLISHABLE_ALLOWED_EVENTS:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"Publishable keys cannot send '{event_type}' events. "
            f"Allowed: {sorted(PUBLISHABLE_ALLOWED_EVENTS)}",
        )

    payload = _normalize_payload(raw)
    customer_raw = raw.get("customer") or {}
    checkout_id = payload.get("checkout_id") or payload.get("checkoutId")

    # customer.identified: the one legitimate source of real consent grants
    # today (see app/services/identity_service.py) - a merchant's own site
    # explicitly declaring the customer opted in, not an inferred default.
    if event_type == "customer.identified":
        external_id = customer_raw.get("external_id") or customer_raw.get("customerId") or customer_raw.get("id")
        if not external_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "customer.identified requires customer.external_id.")
        customer = customers_repo.upsert_by_external_id(
            merchant_id, external_id,
            name=customer_raw.get("name", ""), email=customer_raw.get("email", ""), phone=customer_raw.get("phone", ""),
        )
        consent = payload.get("consent") or {}
        for channel, granted in consent.items():
            if granted:
                consents_repo.record_consent(merchant_id, customer["customer_id"], channel, "granted", source="sdk_identify")
        return {"status": "ok", "customer_id": customer["customer_id"]}

    idempotency_key = request.headers.get("idempotency-key") or (
        f"{merchant_id}:{event_type}:{checkout_id}" if checkout_id else None
    )

    correlation_id = checkout_id or payload.get("cart_id") or merchant_id
    await bus.publish(
        event_type=event_type,
        payload={**payload, "source": "sdk" if trust_level == "restricted" else "server_api"},
        correlation_id=correlation_id,
        merchant_id=merchant_id,
        idempotency_key=idempotency_key,
    )

    return {"status": "ok"}
