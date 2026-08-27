import logging
from fastapi import APIRouter, Response, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field

from app.core.auth import (
    hash_password, verify_password, create_session_token,
    get_current_merchant, SESSION_COOKIE_NAME, AuthNotConfiguredError,
)
from app.core.config import settings
from app.db.repositories import merchants as merchants_repo

logger = logging.getLogger(__name__)
auth_router = APIRouter(prefix="/api/auth", tags=["auth"])

# httpOnly cookie, 7 days by default (settings.JWT_EXPIRE_MINUTES). `secure`
# only in non-development so local http:// testing still works.
_COOKIE_KWARGS = dict(
    httponly=True,
    samesite="lax",
    secure=settings.ENVIRONMENT != "development",
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
async def signup(payload: SignupRequest, response: Response):
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

    response.set_cookie(SESSION_COOKIE_NAME, token, **_COOKIE_KWARGS)
    return {"merchant": _public_merchant(merchant)}


@auth_router.post("/login")
async def login(payload: LoginRequest, response: Response):
    merchant = merchants_repo.get_merchant_by_email(payload.email)
    if not merchant or not verify_password(payload.password, merchant["password_hash"]):
        # Same error for "no such email" and "wrong password" - don't leak
        # which one it was.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password.")

    try:
        token = create_session_token(merchant["merchant_id"])
    except AuthNotConfiguredError as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    response.set_cookie(SESSION_COOKIE_NAME, token, **_COOKIE_KWARGS)
    return {"merchant": _public_merchant(merchant)}


@auth_router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return {"status": "ok"}


@auth_router.get("/me")
async def me(current_merchant: dict = Depends(get_current_merchant)):
    return {"merchant": _public_merchant(current_merchant)}
