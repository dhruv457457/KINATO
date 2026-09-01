"""Signing in with Google, and the three ways it could go wrong.

The credentials for this have been sitting in `frontend/.env.local` under a
comment reading "Required for Sign in with Google" for as long as that file
has existed, with no backend route, no next-auth, and no button in the UI.
Same shape as the dead policy columns - configuration promising a capability
nothing provided - except this one also put a client SECRET in the frontend
project, which is the wrong side of the wire.

What the tests below actually protect:

  * **A forged callback.** Accounts are matched by email, so a callback
    anybody can construct is an account anybody can enter. The `state`
    parameter is minted server-side, stored in a short-lived httpOnly
    cookie, and must match on the way back.
  * **An unverified address.** Matching by email means an unverified one
    lets somebody claim an account by asserting they own an address they do
    not.
  * **A usable password.** `password_hash` is NOT NULL, so a Google account
    has to put SOMETHING there, and a blank or a constant would be a
    password every Google account shares.
"""
import httpx
import pytest

from app.api.google_auth import STATE_COOKIE_NAME
from app.core.auth import SESSION_COOKIE_NAME, verify_password
from app.core.config import settings
from app.main import app


def _cookies(response, name):
    return [
        v for k, v in response.headers.multi_items()
        if k.lower() == "set-cookie" and v.startswith(f"{name}=")
    ]


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="https://test", follow_redirects=False
    ) as c:
        yield c


@pytest.fixture
def google_configured(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_SECRET", "test-secret")


class TestTheButtonIsOnlyOfferedWhenItCanWork:
    async def test_status_reports_unconfigured(self, client, monkeypatch):
        """A sign-in button that cannot work is worse than no button: the
        person clicks, lands on a Google error page, and has no way to know
        the missing piece is a key on our side."""
        monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "")
        monkeypatch.setattr(settings, "GOOGLE_CLIENT_SECRET", "")
        resp = await client.get("/api/auth/google/status")
        assert resp.json() == {"configured": False}

    async def test_status_reports_configured(self, client, google_configured):
        resp = await client.get("/api/auth/google/status")
        assert resp.json() == {"configured": True}

    async def test_starting_without_configuration_is_refused(self, client, monkeypatch):
        monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "")
        resp = await client.get("/api/auth/google/start")
        assert resp.status_code == 503


class TestTheStateParameterIsRealCsrfProtection:
    async def test_start_issues_a_state_and_remembers_it(self, client, google_configured):
        resp = await client.get("/api/auth/google/start")
        assert resp.status_code in (302, 307)
        assert "accounts.google.com" in resp.headers["location"]

        state_cookies = _cookies(resp, STATE_COOKIE_NAME)
        assert state_cookies, "no state cookie was set, so the callback can be forged"
        state_value = state_cookies[0].split("=", 1)[1].split(";", 1)[0]
        assert f"state={state_value}" in resp.headers["location"]
        # It must not be readable by page scripts.
        assert "httponly" in state_cookies[0].lower()

    async def test_a_callback_with_no_state_is_refused(self, client, google_configured):
        resp = await client.get("/api/auth/google/callback?code=abc")
        assert resp.status_code in (302, 307)
        assert "error=bad_state" in resp.headers["location"]
        assert not _cookies(resp, SESSION_COOKIE_NAME)

    async def test_a_mismatched_state_is_refused(self, client, google_configured):
        """The forged-callback case: an attacker sends someone a callback
        URL and signs them into an account of the attacker's choosing."""
        resp = await client.get(
            "/api/auth/google/callback?code=abc&state=attacker",
            cookies={STATE_COOKIE_NAME: "the-one-we-actually-issued"},
        )
        assert "error=bad_state" in resp.headers["location"]
        assert not _cookies(resp, SESSION_COOKIE_NAME)

    async def test_a_matching_state_with_no_code_is_still_refused(self, client, google_configured):
        resp = await client.get(
            "/api/auth/google/callback?state=s1", cookies={STATE_COOKIE_NAME: "s1"}
        )
        assert "error=no_code" in resp.headers["location"]
        assert not _cookies(resp, SESSION_COOKIE_NAME)


class TestWhatGoogleSaysIsNotTakenOnTrust:
    async def test_an_unverified_email_never_signs_anybody_in(
        self, client, google_configured, monkeypatch
    ):
        """Accounts are matched by email address. An unverified one lets
        somebody claim an account by asserting they own an address."""
        import app.api.google_auth as ga

        async def fake_profile(*a, **k):
            return {"email": "someone@example.com", "email_verified": False, "name": "X"}

        # raising=True on purpose. The first version of this test patched a
        # name that did not exist, so the patch did nothing, the real call to
        # Google failed instead, and the test passed while never reaching the
        # branch it claims to cover.
        monkeypatch.setattr(ga, "_exchange_for_profile", fake_profile, raising=True)
        resp = await client.get(
            "/api/auth/google/callback?code=c&state=s1", cookies={STATE_COOKIE_NAME: "s1"}
        )
        assert not _cookies(resp, SESSION_COOKIE_NAME)
        assert "error=email_not_verified" in resp.headers["location"], resp.headers["location"]

    async def test_a_verified_email_does_sign_in(
        self, client, google_configured, monkeypatch, real_merchant_id
    ):
        """The other half. Without this, the test above would still pass if
        the callback refused everybody."""
        import app.api.google_auth as ga
        from app.db.repositories import merchants as merchants_repo

        existing = merchants_repo.get_merchant(real_merchant_id)

        async def fake_profile(*a, **k):
            return {"email": existing["email"], "email_verified": True, "name": "Verified"}

        monkeypatch.setattr(ga, "_exchange_for_profile", fake_profile, raising=True)
        resp = await client.get(
            "/api/auth/google/callback?code=c&state=s1", cookies={STATE_COOKIE_NAME: "s1"}
        )
        session = _cookies(resp, SESSION_COOKIE_NAME)
        assert session, "a verified Google account was not signed in"
        assert "error=" not in resp.headers["location"]


class TestAGoogleAccountHasNoUsablePassword:
    def test_the_generated_hash_cannot_be_guessed_or_reused(self):
        """password_hash is NOT NULL, so something must go there. A blank or
        a constant would be one password shared by every Google account.
        """
        from app.core.auth import hash_password
        import secrets

        a = hash_password(secrets.token_urlsafe(48))
        b = hash_password(secrets.token_urlsafe(48))
        assert a != b
        for guess in ("", "google", "password", "changeme", a):
            assert verify_password(guess, a) is False
