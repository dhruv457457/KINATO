"""A session that expires while you are using it is a bug, not a policy.

The session is a seven-day JWT in an httpOnly cookie, and it was issued once
at login and never touched again. So a merchant who signed in a week ago and
is in the middle of editing their policy is logged out mid-action - no
warning, no renewal, and the work in the form is gone. Nothing failed
loudly; the next request simply came back 401 and the app bounced to /login.

Seven days of *inactivity* is a reasonable thing to end a session on. Seven
days since you last typed your password is not, and those are different
rules. This makes it the first one: while you are active the clock keeps
being pushed forward, and when you stop, it runs out.

Two things it must not do.

  * It must not resurrect a session that has genuinely expired. Renewal
    happens on a cookie that is still valid; an expired one stays dead.
  * The refreshed cookie must carry EXACTLY the attributes the original
    had. A browser matches cookies on path, SameSite and Secure, so getting
    one wrong writes a second, different cookie and leaves the first alone.
    That is not hypothetical here - it is the logout bug, in reverse, and
    the reason that test exists.
"""
from datetime import datetime, timedelta, timezone

import httpx
import jwt
import pytest

from app.core.auth import SESSION_COOKIE_NAME, JWT_ALGORITHM, create_session_token
from app.core.config import settings
from app.main import app


def _session_cookies(response) -> list:
    return [
        v for k, v in response.headers.multi_items()
        if k.lower() == "set-cookie" and v.startswith(f"{SESSION_COOKIE_NAME}=")
    ]


def _token_aged(merchant_id: str, minutes_old: int) -> str:
    """A real, valid session token that was issued `minutes_old` ago."""
    issued = datetime.now(timezone.utc) - timedelta(minutes=minutes_old)
    return jwt.encode(
        {
            "sub": merchant_id,
            "iat": issued,
            "exp": issued + timedelta(minutes=settings.JWT_EXPIRE_MINUTES),
        },
        settings.JWT_SECRET_KEY,
        algorithm=JWT_ALGORITHM,
    )


def _exp_of(raw_cookie: str) -> datetime:
    token = raw_cookie.split("=", 1)[1].split(";", 1)[0]
    payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    return datetime.fromtimestamp(payload["exp"], tz=timezone.utc)


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as c:
        yield c


class TestAnActiveMerchantIsNotLoggedOut:
    async def test_an_old_but_valid_session_is_renewed(self, client, real_merchant_id):
        """The whole point. Past halfway through its life, using the app
        pushes the expiry forward."""
        lifetime = settings.JWT_EXPIRE_MINUTES
        old = _token_aged(real_merchant_id, int(lifetime * 0.8))

        resp = await client.get("/api/auth/me", cookies={SESSION_COOKIE_NAME: old})
        assert resp.status_code == 200

        refreshed = _session_cookies(resp)
        assert refreshed, "an old session was not renewed - the merchant will be logged out mid-work"
        assert _exp_of(refreshed[0]) > datetime.now(timezone.utc) + timedelta(
            minutes=lifetime * 0.9
        )

    async def test_a_fresh_session_is_left_alone(self, client, real_merchant_id):
        """Rewriting the cookie on every single request is noise on every
        response for no benefit. Only a session actually approaching its end
        needs pushing."""
        fresh = create_session_token(real_merchant_id)
        resp = await client.get("/api/auth/me", cookies={SESSION_COOKIE_NAME: fresh})
        assert resp.status_code == 200
        assert not _session_cookies(resp)

    async def test_the_renewed_cookie_keeps_the_original_attributes(
        self, client, real_merchant_id
    ):
        """A browser matches on path, SameSite and Secure. Change one and
        you have written a SECOND cookie and left the first in place - which
        is the logout bug, in reverse."""
        old = _token_aged(real_merchant_id, int(settings.JWT_EXPIRE_MINUTES * 0.8))
        resp = await client.get("/api/auth/me", cookies={SESSION_COOKIE_NAME: old})

        cookie = _session_cookies(resp)[0].lower()
        assert "httponly" in cookie
        assert "path=/" in cookie
        # base_url is https, so this is the cross-site shape the deployed
        # dashboard actually needs.
        assert "samesite=none" in cookie
        assert "secure" in cookie


class TestWhatRenewalMustNeverDo:
    async def test_an_expired_session_stays_expired(self, client, real_merchant_id):
        """Renewal extends a live session. It does not raise the dead - a
        cookie past its expiry is someone who must sign in again."""
        expired = _token_aged(real_merchant_id, settings.JWT_EXPIRE_MINUTES + 60)
        resp = await client.get("/api/auth/me", cookies={SESSION_COOKIE_NAME: expired})
        assert resp.status_code == 401
        assert not _session_cookies(resp)

    async def test_a_forged_cookie_is_never_renewed(self, client):
        resp = await client.get(
            "/api/auth/me", cookies={SESSION_COOKIE_NAME: "not.a.real.token"}
        )
        assert resp.status_code == 401
        assert not _session_cookies(resp)

    async def test_an_anonymous_request_is_given_no_session(self, client):
        resp = await client.get("/api/auth/me")
        assert resp.status_code == 401
        assert not _session_cookies(resp)

    async def test_logging_out_is_not_undone_by_renewal(self, client, real_merchant_id):
        """Logout clears the cookie. If renewal then wrote a fresh one onto
        the same response, sign-out would silently not sign you out - the
        exact bug test_logout_actually_logs_out exists to prevent."""
        old = _token_aged(real_merchant_id, int(settings.JWT_EXPIRE_MINUTES * 0.8))
        resp = await client.post("/api/auth/logout", cookies={SESSION_COOKIE_NAME: old})
        assert resp.status_code == 200

        cookies = _session_cookies(resp)
        assert cookies, "logout set no cookie at all"
        for c in cookies:
            value = c.split("=", 1)[1].split(";", 1)[0]
            assert value in ("", '""'), f"logout response carried a live session: {c}"
