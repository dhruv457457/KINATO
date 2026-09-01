"""Sign in with Google.

`GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` have been sitting in
`frontend/.env.local` under a comment reading "Required for Sign in with
Google" since before this file existed. There was no backend route, no
next-auth, and no button anywhere in the dashboard. It is the same shape as
the dead policy columns in FINDINGS #4: configuration that promises a
capability nothing provides - except this one also put a client *secret* in
the frontend project, which is the wrong side of the wire for it to live on.

The flow is server-side on purpose. The session is an httpOnly cookie the
browser cannot read, and that is worth keeping: a token the page can reach
is a token an injected script can take. So the browser only ever visits two
URLs here, and the code exchange happens between this server and Google.

    /api/auth/google/start     -> redirect to Google
    /api/auth/google/callback  -> exchange, sign in, redirect to dashboard

Three things this is careful about.

**CSRF.** The `state` parameter is minted here, put in a short-lived
httpOnly cookie, and required to match on the way back. Without it, anyone
can hand a victim a callback URL and sign them into an attacker's account.

**Unverified emails.** Accounts are matched by email address, so an
unverified one would let somebody claim an account by asserting they own an
address they do not. `email_verified` is checked, and a false one is
refused.

**Passwords.** A Google-created merchant gets a random unusable password
hash rather than a blank or a known one, because `password_hash` is NOT
NULL and a predictable value there is a password. `auth_provider` records
how the account signs in so nothing later offers a password reset for a
login that does not take one.
"""
import logging
import secrets
from typing import Optional
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse

from app.core.auth import (
    SESSION_COOKIE_NAME,
    create_session_token,
    hash_password,
    session_cookie_kwargs,
)
from app.core.config import settings
from app.db.repositories import merchants as merchants_repo

logger = logging.getLogger(__name__)
google_router = APIRouter(prefix="/api/auth/google", tags=["auth"])

_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"

STATE_COOKIE_NAME = "kinato_oauth_state"
# Long enough to sign in, short enough that a leaked state is useless.
STATE_TTL_SECONDS = 600


def _configured() -> bool:
    return bool(settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET)


def _redirect_uri(request: Request) -> str:
    """Where Google sends the browser back.

    Built from the request rather than configured separately so it cannot
    drift from the URL actually being served - a redirect_uri that does not
    match what is registered in Google fails with an error page nobody can
    debug from the outside. It must still be registered in the Google
    console; this only guarantees both halves agree about what it is.
    """
    base = (settings.NGROK_URL or str(request.base_url)).rstrip("/")
    return f"{base}/api/auth/google/callback"


def _state_cookie_kwargs(request: Request) -> dict:
    kwargs = session_cookie_kwargs(request)
    kwargs["max_age"] = STATE_TTL_SECONDS
    return kwargs


@google_router.get("/status")
async def google_status():
    """Whether the dashboard should offer the button at all.

    A sign-in button that cannot work is worse than no button: the person
    clicks it, lands on a Google error, and has no way to know the problem
    is a missing key on our side.
    """
    return {"configured": _configured()}


@google_router.get("/start")
async def google_start(request: Request):
    if not _configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google sign-in is not configured on this server.",
        )

    state = secrets.token_urlsafe(32)
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": _redirect_uri(request),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        # Ask for an account chooser rather than silently reusing whichever
        # Google account the browser happens to be signed into.
        "prompt": "select_account",
    }
    response = RedirectResponse(f"{_AUTHORIZE_URL}?{urlencode(params)}")
    response.set_cookie(STATE_COOKIE_NAME, state, **_state_cookie_kwargs(request))
    return response


async def _exchange_for_profile(code: str, redirect_uri: str) -> Optional[dict]:
    """Trade the one-time code for the signed-in person's profile.

    A named function rather than an inline block so a test can replace it.
    The alternative is a test that patches a name which does not exist,
    passes because the real call to Google failed instead, and reports
    coverage of a branch it never reached - which is how a test comes to be
    counted while proving nothing.
    """
    async with httpx.AsyncClient(timeout=15.0) as client:
        token_response = await client.post(
            _TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        token_response.raise_for_status()
        access_token = token_response.json().get("access_token")
        if not access_token:
            return None

        userinfo = await client.get(
            _USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}
        )
        userinfo.raise_for_status()
        return userinfo.json()


def _failed(reason: str) -> RedirectResponse:
    """Back to the dashboard's login page with something it can show.

    Never a raw 500. The person is in a browser mid-sign-in; an error page
    from an API they did not know they were talking to tells them nothing.
    """
    frontend = settings.FRONTEND_URL.rstrip("/")
    return RedirectResponse(f"{frontend}/login?error={reason}")


@google_router.get("/callback")
async def google_callback(request: Request, code: Optional[str] = None, state: Optional[str] = None):
    if not _configured():
        return _failed("google_not_configured")

    expected = request.cookies.get(STATE_COOKIE_NAME)
    if not state or not expected or not secrets.compare_digest(state, expected):
        # Either a stale attempt or a forged callback. Both end here.
        logger.warning("Google callback with a state that did not match the one we issued.")
        return _failed("bad_state")
    if not code:
        return _failed("no_code")

    try:
        profile = await _exchange_for_profile(code, _redirect_uri(request))
    except Exception as e:
        logger.warning(f"Google token exchange failed: {e}")
        return _failed("google_unreachable")
    if profile is None:
        return _failed("no_token")

    email = (profile.get("email") or "").strip().lower()
    if not email:
        return _failed("no_email")
    # Accounts are matched by email, so an unverified one would let somebody
    # claim an account by asserting they own an address they do not.
    if not profile.get("email_verified"):
        logger.warning(f"Refused Google sign-in for unverified address {email}.")
        return _failed("email_not_verified")

    merchant = merchants_repo.get_merchant_by_email(email)
    if not merchant:
        merchant = merchants_repo.create_merchant(
            name=profile.get("name") or email.split("@")[0],
            email=email,
            # NOT NULL, and a predictable value here would be a password.
            # A random bcrypt hash nobody holds the input to cannot be
            # matched by any login attempt.
            password_hash=hash_password(secrets.token_urlsafe(48)),
        )
        try:
            merchants_repo.set_auth_provider(merchant["merchant_id"], "google")
        except Exception as e:  # pragma: no cover - column exists after init_db
            logger.warning(f"Could not record auth_provider for {merchant['merchant_id']}: {e}")
        logger.info(f"Created merchant {merchant['merchant_id']} from Google sign-in.")

    frontend = settings.FRONTEND_URL.rstrip("/")
    # Straight to wherever they left off, rather than always the dashboard -
    # a half-finished onboarding is the most common reason to sign back in.
    step = merchant.get("onboarding_step") or "signup"
    destination = "/dashboard" if step == "done" else f"/onboarding/{step}"
    if step == "signup":
        destination = "/onboarding/connect"

    response = RedirectResponse(f"{frontend}{destination}")
    response.set_cookie(
        SESSION_COOKIE_NAME,
        create_session_token(merchant["merchant_id"]),
        **session_cookie_kwargs(request),
    )
    response.delete_cookie(STATE_COOKIE_NAME, path="/")
    return response
