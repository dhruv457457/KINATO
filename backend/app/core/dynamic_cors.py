"""
Per-merchant CORS for POST /api/events. The global CORSMiddleware in
app/main.py serves the dashboard's own fixed origins (settings.CORS_ORIGINS);
it can't also express "each merchant has their own storefront origin," and
would reject the preflight for any of them before the actual route ever ran.

This middleware intercepts only the OPTIONS preflight for /api/events,
checks the request's Origin against the full set of origins any merchant
has registered (app/api/merchant_settings.py's /api/merchant/allowed-origins),
and answers the preflight directly if it matches. The real per-merchant
check (which merchant's API key, which merchant's specific allowlist) still
happens in the POST handler itself (app/api/events.py) - this middleware
only proves "some registered merchant expects this origin," so the browser
completes the preflight and sends the real request.
"""
import json
import logging
from fastapi import Request
from fastapi.responses import Response

from app.db.database import get_db

logger = logging.getLogger(__name__)

EVENTS_PATH = "/api/events"


def _all_registered_origins() -> set:
    origins = set()
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT allowed_origins FROM merchants WHERE allowed_origins IS NOT NULL AND allowed_origins != '[]'"
        )
        for row in cursor.fetchall():
            try:
                origins.update(json.loads(row["allowed_origins"]))
            except (TypeError, ValueError):
                continue
    return origins


async def dynamic_cors_middleware(request: Request, call_next):
    if request.url.path == EVENTS_PATH and request.method == "OPTIONS":
        origin = request.headers.get("origin", "")
        if origin and origin in _all_registered_origins():
            return Response(
                status_code=204,
                headers={
                    "Access-Control-Allow-Origin": origin,
                    "Access-Control-Allow-Methods": "POST, OPTIONS",
                    "Access-Control-Allow-Headers": "Content-Type, X-Kinato-Key, Authorization, Idempotency-Key",
                    "Access-Control-Max-Age": "600",
                    "Vary": "Origin",
                },
            )
        logger.info(f"Rejected /api/events preflight from unregistered origin: {origin!r}")
    return await call_next(request)
