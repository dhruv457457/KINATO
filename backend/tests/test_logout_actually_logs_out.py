"""Signing out has to end the session, not just navigate away.

Found in a browser, against the deployed API: sign-out returned 200, the app
redirected to /login, and typing /dashboard straight back into the address
bar was still fully authenticated. The session had never been cleared.

The cause is that a browser matches a cookie deletion against the ORIGINAL
cookie's path, SameSite and Secure. Get any of them wrong and it treats the
Set-Cookie as describing a different cookie entirely: it stores that expired
one, and leaves the real session sitting there. The session cookie here is
cross-site (dashboard on Vercel or localhost, API on Railway) so it is
written `SameSite=None; Secure`, and deleting it with only `path="/"` did
precisely nothing.

The reason this is worth a test rather than a one-line fix and a shrug: a
logout that reports success and does not log you out is one the person
believes. On a shared machine that is the next person reading someone else's
customers. A missing button would have been safer, because nobody trusts a
button that isn't there.
"""
import httpx
import pytest

from app.api.auth import SESSION_COOKIE_NAME
from app.main import app


def _set_cookie_headers(response) -> list:
    """Every Set-Cookie on the response, as raw header strings."""
    return [v for k, v in response.headers.multi_items() if k.lower() == "set-cookie"]


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as c:
        yield c


class TestTheDeletionMatchesTheCookieItDeletes:
    async def test_logout_clears_the_session_cookie_cross_site(self, client):
        """Over HTTPS the cookie is SameSite=None; Secure, so the deletion
        must be too - or the browser silently keeps the real one."""
        resp = await client.post("/api/auth/logout")
        assert resp.status_code == 200

        cookies = _set_cookie_headers(resp)
        session_cookies = [c for c in cookies if c.startswith(f"{SESSION_COOKIE_NAME}=")]
        assert session_cookies, "logout set no cookie at all, so nothing was cleared"

        header = session_cookies[0].lower()
        # The three attributes that decide whether a browser considers this
        # the same cookie. Missing any one is the bug.
        assert "path=/" in header
        assert "samesite=none" in header, (
            "the cookie was written SameSite=None; a deletion without it describes a different cookie"
        )
        assert "secure" in header
        assert "httponly" in header

    async def test_the_deletion_actually_expires_it(self, client):
        """Set-Cookie alone is not deletion - it has to expire the value."""
        resp = await client.post("/api/auth/logout")
        header = _set_cookie_headers(resp)[0].lower()
        assert "max-age=0" in header or "expires=" in header, (
            "the cookie was re-set rather than expired"
        )


class TestTheSessionIsGoneAfterwards:
    async def test_me_is_unauthenticated_once_the_cookie_is_cleared(self, client):
        """The behaviour a person actually cares about, end to end."""
        await client.post("/api/auth/logout")
        # httpx applies the Set-Cookie to its jar exactly as a browser would.
        me = await client.get("/api/auth/me")
        assert me.status_code == 401, "the session survived a sign-out"
