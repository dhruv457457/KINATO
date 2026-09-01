import logging

# Must run before any other app import - several modules (notably
# app/db/database.py) log real diagnostics (e.g. which DB engine actually
# connected) at import time. Without a configured handler here, Python's
# logging module silently drops every INFO-level call via its own
# "handler of last resort" (WARNING and above only) - which is exactly
# what made it impossible to tell, from Railway's deploy logs alone,
# whether the app was really talking to Postgres or had silently fallen
# back to ephemeral local SQLite. This used to live only in
# run_backend.py's __main__ block, which a production start command
# (`uvicorn app.main:app`, per the Procfile) never actually executes.
logger = logging.getLogger(__name__)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os

from app.core.net import close_shared_clients
from app.db.database import prewarm_pool
from app.api.dashboard import router as dashboard_router
from app.api.recovery_actions import actions_router
from app.api.triggers import trigger_router
from app.api.auth import auth_router
from app.api.merchant_settings import merchant_router
from app.api.events import events_router
from app.channels.voice_runtime import voice_router
from app.payments.webhooks import payments_router
from app.gateway.event_bus import bus
from app.gateway.sweeper import run_sweeper_loop
from app.core.config import settings
from app.api.google_auth import google_router
from app.core.dynamic_cors import dynamic_cors_middleware
from app.core.auth import (
    SESSION_COOKIE_NAME,
    create_session_token,
    decode_session_token,
    session_cookie_kwargs,
    session_needs_renewal,
)
from app.db.init_db import init_db

# Import deterministic services & agents to ensure they subscribe to the event bus
import app.services.checkout_tracking
import app.services.identity_service
import app.services.recovery_eligibility
import app.services.discovery_agent
import app.services.call_orchestrator
import app.services.policy_engine
import app.services.payment_execution
import app.services.email_service
import app.services.attribution

os.makedirs("static/audio", exist_ok=True)
os.makedirs("static/sdk", exist_ok=True)

# Ensures the schema exists wherever get_db() actually resolves to - matters
# because a transient Postgres outage silently falls back to a fresh local
# SQLite file (app/db/database.py's designed behavior), which would
# otherwise have no tables at all until this runs. Idempotent, cheap, safe
# to run on every boot.
init_db()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Replaces the old per-checkout asyncio.sleep task (lost on restart, and
    # unsafe across multiple worker processes) - see app/gateway/sweeper.py.
    sweeper_task = asyncio.create_task(run_sweeper_loop())
    # Open the database connections a call will need before any call needs
    # them - a cold connection is the most expensive thing on a live turn.
    # Named, not bare create_task: asyncio references tasks weakly and a
    # collected warm-up is a warm-up that silently never happened.
    warmup_task = asyncio.create_task(prewarm_pool())
    yield
    warmup_task.cancel()
    sweeper_task.cancel()
    # The shared HTTP clients (OpenRouter, ElevenLabs) hold keepalive
    # connections open on purpose; this is where they are given back.
    await close_shared_clients()


app = FastAPI(title="Kinato Core Server — Autonomous Revenue Infrastructure", lifespan=lifespan)
app.mount("/audio", StaticFiles(directory="static/audio"), name="audio")
app.mount("/sdk", StaticFiles(directory="static/sdk"), name="sdk")

# allow_origins=["*"] + allow_credentials=True is an invalid combination that
# browsers reject outright (and was doing nothing useful even before that -
# it granted no real security). Scope to configured origins; the /api/events
# ingestion route (added for per-merchant CORS) handles its own origin check
# separately since a single global allowlist can't express per-merchant rules.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    # Wildcards do not work in allow_origins - Starlette compares strings.
    # A pattern belongs here or nowhere, and it is empty unless configured.
    allow_origin_regex=settings.CORS_ORIGIN_REGEX or None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
logger.info(
    "CORS allows %s%s",
    settings.cors_origins_list,
    f" + regex {settings.CORS_ORIGIN_REGEX!r}" if settings.CORS_ORIGIN_REGEX else "",
)
# A wildcard in this list is always a mistake, and a silent one: Starlette
# compares origins by exact string, so "https://*.vercel.app" matches
# nothing while reading exactly like it matches everything. A deployed
# frontend then loads perfectly and every request it makes is blocked by
# the browser, with the server logs showing nothing wrong at all.
for _origin in settings.cors_origins_list:
    if "*" in _origin and _origin != "*":
        logger.warning(
            "CORS origin %r contains a wildcard and will never match anything - "
            "allow_origins is compared exactly. Use CORS_ORIGIN_REGEX for patterns, "
            "or list the exact origin.",
            _origin,
        )

# Added AFTER the global CORSMiddleware so it wraps OUTSIDE it (Starlette
# applies middleware in reverse-registration order) - it must see the
# /api/events preflight before the global, fixed-origin-list middleware
# would otherwise reject any merchant's real storefront origin.
app.middleware("http")(dynamic_cors_middleware)


@app.middleware("http")
async def renew_active_sessions(request, call_next):
    """Push a live session's expiry forward while the merchant is using it.

    The session is a seven-day JWT that was issued once at login and never
    touched again, so a merchant who signed in a week ago was logged out
    mid-action - no warning, no renewal, and whatever was in the form went
    with it. Seven days of INACTIVITY is a fair thing to end a session on;
    seven days since you last typed your password is a different rule, and
    it was the one being enforced.

    Three deliberate limits:

      * Only a token that still verifies is renewed. An expired session
        stays expired - renewal extends a live session, it does not raise a
        dead one.
      * Only past halfway through its life, so responses do not all carry a
        pointless Set-Cookie.
      * Never on a response that is already writing this cookie itself.
        Login and logout both do, and a renewal landing on the logout
        response would hand back a live session to somebody who just signed
        out. That is the logout bug wearing a different hat, so it is
        checked rather than reasoned about.
    """
    response = await call_next(request)

    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token or not session_needs_renewal(token):
        return response

    already_setting = any(
        v.startswith(f"{SESSION_COOKIE_NAME}=")
        for k, v in response.headers.items()
        if k.lower() == "set-cookie"
    )
    if already_setting:
        return response

    merchant_id = decode_session_token(token)
    if not merchant_id:
        return response

    response.set_cookie(
        SESSION_COOKIE_NAME,
        create_session_token(merchant_id),
        **session_cookie_kwargs(request),
    )
    return response

# Register routers
app.include_router(google_router)
app.include_router(dashboard_router, prefix="/api")
app.include_router(actions_router, prefix="/api")
app.include_router(trigger_router, prefix="/api")
app.include_router(auth_router)
app.include_router(merchant_router)
app.include_router(events_router, prefix="/api")
app.include_router(voice_router)
app.include_router(payments_router)

@app.get("/")
def health_check():
    return {
        "status": "ok",
        "service": "Kinato Autonomous Revenue Infrastructure",
        "architecture": "4-Agent Parallel System (Discovery, Call, CustomerIntel, MerchantIntel)"
    }
