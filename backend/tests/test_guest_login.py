"""Guest sign-in: a reviewer should not have to create an account.

The point of this endpoint is that a judge opening the dashboard sees real
recoveries, a real policy and real audit rows without first signing up and
connecting a Razorpay key.

The point of testing it is the other half: a demo account is still a real
merchant with live keys attached, so the credentials must live in the
environment and reach the browser never. The frontend calls an endpoint; it
is not handed a password to send. This file asserts the endpoint exists,
that it is off unless deliberately configured, that it fails closed when
misconfigured, and that a successful guest is an ordinary signed-in
merchant - not a second, weaker way in that everything downstream then has
to know about.
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.auth import SESSION_COOKIE_NAME, hash_password
from app.core.config import settings
from app.db.repositories import merchants as merchants_repo
from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def demo_merchant(monkeypatch):
    """A throwaway merchant with a generated password.

    Deliberately not the real demo account: a test that needs the live
    password would mean the live password had to exist somewhere a test can
    read it, which is the thing this design exists to avoid.
    """
    email = f"guest-test-{uuid.uuid4().hex[:8]}@example.invalid"
    password = uuid.uuid4().hex
    merchant = merchants_repo.create_merchant(
        name="Guest Test Store",
        email=email,
        password_hash=hash_password(password),
    )
    monkeypatch.setattr(settings, "GUEST_DEMO_EMAIL", email)
    monkeypatch.setattr(settings, "GUEST_DEMO_PASSWORD", password)
    return merchant, email, password


class TestOffUnlessConfigured:
    def test_status_reports_off_when_unset(self, client, monkeypatch):
        monkeypatch.setattr(settings, "GUEST_DEMO_EMAIL", "")
        monkeypatch.setattr(settings, "GUEST_DEMO_PASSWORD", "")
        assert client.get("/api/auth/guest/status").json() == {"configured": False}

    def test_login_refuses_when_unset(self, client, monkeypatch):
        """A button that always fails is worse than no button, and an
        endpoint that half-works is worse than one that says no."""
        monkeypatch.setattr(settings, "GUEST_DEMO_EMAIL", "")
        monkeypatch.setattr(settings, "GUEST_DEMO_PASSWORD", "")
        assert client.post("/api/auth/guest").status_code == 503

    def test_half_configured_is_still_off(self, client, monkeypatch):
        """An email with no password is a deployment mistake, not an
        invitation to sign somebody in."""
        monkeypatch.setattr(settings, "GUEST_DEMO_EMAIL", "someone@example.invalid")
        monkeypatch.setattr(settings, "GUEST_DEMO_PASSWORD", "")
        assert client.get("/api/auth/guest/status").json() == {"configured": False}
        assert client.post("/api/auth/guest").status_code == 503


class TestFailsClosed:
    def test_configured_but_wrong_password_does_not_sign_anyone_in(
        self, client, demo_merchant, monkeypatch
    ):
        _, email, _ = demo_merchant
        monkeypatch.setattr(settings, "GUEST_DEMO_PASSWORD", "not-the-password")
        res = client.post("/api/auth/guest")
        assert res.status_code == 503
        assert SESSION_COOKIE_NAME not in res.cookies

    def test_configured_with_an_unknown_email_does_not_sign_anyone_in(
        self, client, demo_merchant, monkeypatch
    ):
        monkeypatch.setattr(settings, "GUEST_DEMO_EMAIL", "nobody@example.invalid")
        res = client.post("/api/auth/guest")
        assert res.status_code == 503
        assert SESSION_COOKIE_NAME not in res.cookies


class TestAGuestIsAnOrdinaryMerchant:
    def test_it_returns_a_real_session(self, client, demo_merchant):
        merchant, _, _ = demo_merchant
        res = client.post("/api/auth/guest")
        assert res.status_code == 200
        assert res.cookies.get(SESSION_COOKIE_NAME)
        assert res.json()["merchant"]["merchant_id"] == merchant["merchant_id"]

    def test_that_session_works_on_a_normal_authenticated_route(self, client, demo_merchant):
        """The whole design rests on this. A guest is signed in through the
        same code path as everyone else, so nothing downstream needs a
        second notion of who is allowed to do what."""
        merchant, _, _ = demo_merchant
        client.post("/api/auth/guest")
        me = client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["merchant"]["merchant_id"] == merchant["merchant_id"]

    def test_the_password_is_never_sent_to_the_browser(self, client, demo_merchant):
        """The reason this endpoint exists rather than a hardcoded button.
        The response is a session, not a credential."""
        _, email, password = demo_merchant
        body = client.post("/api/auth/guest").text
        assert password not in body
        assert "password" not in body.lower()
