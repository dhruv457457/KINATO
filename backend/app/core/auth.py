"""
Merchant authentication: password hashing, JWT session tokens carried in an
httpOnly cookie, and the get_current_merchant FastAPI dependency every
authenticated route depends on.

Deliberately NOT using passlib - it's effectively unmaintained and breaks
under modern bcrypt (its own version-detection code raises on bcrypt>=4.1,
see https://github.com/pyca/bcrypt/issues - this is well documented, not a
one-off). Calling the bcrypt library directly avoids that whole class of bug.
"""
import bcrypt
import jwt
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from fastapi import Request, HTTPException, status

from app.core.config import settings
from app.db.repositories.merchants import get_merchant, MerchantNotFoundError

SESSION_COOKIE_NAME = "kinato_session"
JWT_ALGORITHM = "HS256"


class AuthNotConfiguredError(Exception):
    """Raised when JWT_SECRET_KEY is unset. Deliberately not a silent
    fallback to a hardcoded secret - see app/core/config.py's comment."""


def _require_jwt_secret() -> str:
    if not settings.JWT_SECRET_KEY:
        raise AuthNotConfiguredError(
            "JWT_SECRET_KEY is not set in backend/.env - auth cannot issue or "
            "verify sessions until it is. Generate one with: "
            'python -c "import secrets; print(secrets.token_urlsafe(48))"'
        )
    return settings.JWT_SECRET_KEY


def hash_password(password: str) -> str:
    # bcrypt has a hard 72-byte input limit; truncate rather than let it
    # raise on a long (but not otherwise unreasonable) passphrase.
    return bcrypt.hashpw(password.encode("utf-8")[:72], bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8")[:72], password_hash.encode("utf-8"))
    except ValueError:
        return False


def create_session_token(merchant_id: str) -> str:
    secret = _require_jwt_secret()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": merchant_id,
        "iat": now,
        "exp": now + timedelta(minutes=settings.JWT_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)


def decode_session_token(token: str) -> Optional[str]:
    """Returns the merchant_id if the token is valid, else None. Never raises -
    an invalid/expired/malformed cookie should behave like "not logged in",
    not like a server error."""
    try:
        secret = _require_jwt_secret()
        payload = jwt.decode(token, secret, algorithms=[JWT_ALGORITHM])
        return payload.get("sub")
    except (jwt.PyJWTError, AuthNotConfiguredError):
        return None


async def get_current_merchant(request: Request) -> Dict[str, Any]:
    """
    Required-auth FastAPI dependency. Raises 401 if there is no valid
    session - deliberately no "fall back to a default merchant" branch,
    which is exactly the bug that made the old jiva_demo tenancy fake.
    """
    token = request.cookies.get(SESSION_COOKIE_NAME)
    merchant_id = decode_session_token(token) if token else None
    if not merchant_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    try:
        return get_merchant(merchant_id)
    except MerchantNotFoundError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session refers to a deleted merchant")


async def get_optional_merchant(request: Request) -> Optional[Dict[str, Any]]:
    """Same as get_current_merchant but returns None instead of raising -
    for routes that behave differently when logged in vs not, rather than
    requiring auth outright."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    merchant_id = decode_session_token(token) if token else None
    if not merchant_id:
        return None
    try:
        return get_merchant(merchant_id)
    except MerchantNotFoundError:
        return None
