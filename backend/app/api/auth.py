import logging
from fastapi import APIRouter, Request, Response, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field

from app.core.auth import (
    hash_password, verify_password, create_session_token,
    get_current_merchant, SESSION_COOKIE_NAME, AuthNotConfiguredError,
)
from app.core.config import settings
from app.db.repositories import merchants as merchants_repo

logger = logging.getLogger(__name__)
auth_router = APIRouter(prefix="/api/auth", tags=["auth"])

def _cookie_kwargs(request: Request) -> dict:
    """Session cookie attributes, decided per request.

    The dashboard and the API do not necessarily share a site: the frontend
    can run on Vercel (or localhost during development) while the API runs
    on Railway. That makes the session cookie CROSS-SITE, and a browser will
    silently refuse to store a cross-site cookie unless it is
    `SameSite=None; Secure`. The previous fixed `SameSite=lax` meant login
    returned 200, the browser dropped the cookie on the floor, the next
    /auth/me came back 401, and the app bounced straight back to the login
    page - an infinite redirect with no error anywhere to explain it.

    So: over HTTPS use None+Secure, which is what any deployed split-origin
    setup needs. Over plain HTTP (local development, same-site) keep Lax,
    because browsers reject `SameSite=None` without `Secure` and a local
    http:// login would then break instead.
    """
    is_https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    return dict(
        httponly=True,
        samesite="none" if is_https else "lax",
        secure=is_https,
        max_age=settings.JWT_EXPIRE_MINUTES * 60,
        path="/",
    )


class SignupRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    store_url: str = ""


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


def _public_merchant(merchant: dict) -> dict:
    """Never return password_hash or encrypted Razorpay secrets to the client."""
    return {
        "merchant_id": merchant["merchant_id"],
        "name": merchant["name"],
        "email": merchant["email"],
        "store_url": merchant.get("store_url", ""),
        "onboarding_step": merchant.get("onboarding_step", "signup"),
        "rzp_connected": bool(merchant.get("rzp_key_id_enc")),
        "status": merchant.get("status", "active"),
    }


@auth_router.post("/signup")
async def signup(payload: SignupRequest, response: Response, request: Request):
    if merchants_repo.get_merchant_by_email(payload.email):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An account with this email already exists.")

    try:
        merchant = merchants_repo.create_merchant(
            name=payload.name,
            email=payload.email,
            password_hash=hash_password(payload.password),
            store_url=payload.store_url,
        )
        token = create_session_token(merchant["merchant_id"])
    except AuthNotConfiguredError as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    response.set_cookie(SESSION_COOKIE_NAME, token, **_cookie_kwargs(request))
    return {"merchant": _public_merchant(merchant)}


@auth_router.post("/login")
async def login(payload: LoginRequest, response: Response, request: Request):
    merchant = merchants_repo.get_merchant_by_email(payload.email)
    if not merchant or not verify_password(payload.password, merchant["password_hash"]):
        # Same error for "no such email" and "wrong password" - don't leak
        # which one it was.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password.")

    try:
        token = create_session_token(merchant["merchant_id"])
    except AuthNotConfiguredError as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    response.set_cookie(SESSION_COOKIE_NAME, token, **_cookie_kwargs(request))
    return {"merchant": _public_merchant(merchant)}


@auth_router.post("/logout")
async def logout(response: Response, request: Request):
    """Clear the session cookie - with the SAME attributes it was set with.

    This is the whole bug. A browser matches a deletion against the
    original cookie's path, SameSite and Secure; get any of them wrong and
    it treats the Set-Cookie as describing a DIFFERENT cookie, quietly
    stores that expired one, and leaves the real session untouched.

    The cookie is written cross-site (dashboard on Vercel or localhost, API
    on Railway) so it carries `SameSite=None; Secure` - see _cookie_kwargs.
    Deleting it with only `path="/"` therefore did nothing at all. Sign-out
    returned 200, the app redirected to /login, and navigating straight back
    to /dashboard was still fully authenticated. Verified in a browser
    against the deployed API.

    Worse than a missing button: a logout that reports success and does not
    log you out is one the person believes. On a shared machine that is the
    next person reading someone else's customers.
    """
    kwargs = _cookie_kwargs(request)
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        path=kwargs["path"],
        httponly=kwargs["httponly"],
        samesite=kwargs["samesite"],
        secure=kwargs["secure"],
    )
    return {"status": "ok"}


@auth_router.get("/me")
async def me(current_merchant: dict = Depends(get_current_merchant)):
    return {"merchant": _public_merchant(current_merchant)}
