"""
Shared pytest fixtures.

The event bus (`app.gateway.event_bus.bus`) is a process-wide singleton, and
subscribers are registered once at import time by each service module (mirrors
how app/main.py boots the real app). Tests therefore must NOT re-import those
service modules per test - instead we import them all once here, then reset
the bus's mutable state (event log + idempotency keys) before every test so
tests don't leak events into each other.
"""
import asyncio
import uuid
import pytest

# Import every service module once so their bus.subscribe(...) calls at
# import time register exactly as they do in app/main.py.
from app.gateway.event_bus import bus
import app.gateway.sweeper  # noqa: F401
import app.services.checkout_tracking  # noqa: F401
import app.services.identity_service  # noqa: F401
import app.services.recovery_eligibility  # noqa: F401
import app.services.discovery_agent  # noqa: F401
import app.services.call_orchestrator  # noqa: F401
import app.services.policy_engine  # noqa: F401
import app.services.payment_execution  # noqa: F401
import app.services.email_service  # noqa: F401
import app.services.attribution  # noqa: F401

from app.core.auth import hash_password
from app.core.ids import new_id
from app.db.database import get_db
from app.db.repositories import merchants as merchants_repo


@pytest.fixture
def real_merchant_id():
    """
    Creates one real, throwaway merchant row for a test that needs
    policy_engine (DB-backed) to resolve a real merchant_id, instead of
    relying on any seeded/pretend business. A fresh merchant's default
    policy (max_discount_percent=10, minimum_margin_percent=15 - see
    app/db/init_db.py's column defaults) matches what these tests expect
    with zero overrides needed. Cleans up its own rows afterward - the real
    (shared, persistent) DB is not a place to leave test debris.
    """
    merchant = merchants_repo.create_merchant(
        name="Test Merchant",
        email=f"test_{uuid.uuid4().hex[:10]}@example.com",
        password_hash=hash_password("test-password-not-real"),
    )
    merchant_id = merchant["merchant_id"]
    yield merchant_id
    with get_db() as conn:
        cursor = conn.cursor()
        for table in ("audit_log", "events", "offer_tokens", "recovery_attempts",
                      "checkouts", "consents", "customers", "products", "api_keys",
                      "merchant_policies", "merchants"):
            cursor.execute(f"DELETE FROM {table} WHERE merchant_id = %s", (merchant_id,))


@pytest.fixture
def unique_checkout_id(real_merchant_id):
    """A fresh, collision-proof checkout_id for tests that exercise the
    recovery pipeline (which now does real DB lookups keyed by checkout_id -
    a fixed literal like "chk_test_1" would collide with the same test's
    leftover row from a previous run). Cleans itself up on teardown."""
    checkout_id = new_id("chk_test")
    yield checkout_id
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM recovery_attempts WHERE checkout_id = %s", (checkout_id,))
        cursor.execute("DELETE FROM checkouts WHERE checkout_id = %s", (checkout_id,))


class _FakePaymentLink:
    """Stands in for the real Razorpay SDK's `client.payment_link` resource
    in tests - a test double, not a product-level mock (the product never
    ships this; app/services/payment_execution.py always calls the real SDK
    method and raises PaymentExecutionError if it fails). This is the same
    pattern already used in test_customer_intelligence_fallback.py to stand
    in for the LLM call - the same test-double pattern used for the LLM
    calls in app/services/merchant_intelligence.py and discovery_agent.py."""
    def create(self, payload):
        return {"id": f"plink_test_{uuid.uuid4().hex[:8]}", "short_url": "https://rzp.io/i/test_stub"}


class _FakeRazorpayClient:
    payment_link = _FakePaymentLink()


@pytest.fixture
def connected_merchant_id(real_merchant_id, monkeypatch):
    """A merchant that behaves as if it has connected a real Razorpay
    account - for tests exercising the payment-link-generation path without
    hitting the real (paid, rate-limited) Razorpay API on every test run."""
    import app.services.payment_execution as payment_execution_module
    monkeypatch.setattr(
        payment_execution_module, "get_client_for_merchant", lambda merchant_id: _FakeRazorpayClient()
    )
    return real_merchant_id


@pytest.fixture(autouse=True)
def _discovery_agent_heuristic_only(monkeypatch):
    """Forces app/services/discovery_agent.py onto its heuristic (no-LLM-call)
    path for every test - the suite should be fast and network-independent
    by default. A live-LLM smoke test lives separately as a manual script."""
    import app.services.discovery_agent as discovery_agent_module
    monkeypatch.setattr(discovery_agent_module.settings, "OPENROUTER_API_KEY", "")


@pytest.fixture(autouse=True)
def _fake_voice_dispatch(monkeypatch):
    """NEVER place a real Twilio call from the automated test suite - this
    account has a real, limited pool of live call-testing minutes. Patches
    the exact name call_orchestrator.py imported (`from ... import
    place_outbound_call`), not the origin module, since that's the
    reference actually invoked at call time."""
    import app.services.call_orchestrator as call_orchestrator_module

    def _fake_place_call(to_phone: str, recovery_attempt_id: str, record: bool = False) -> str:
        return f"CAfake_{recovery_attempt_id}"

    monkeypatch.setattr(call_orchestrator_module, "place_outbound_call", _fake_place_call)

    # call_orchestrator also pre-generates the greeting's voice block via
    # tts.voice_block() before dialing (see its docstring) - that would
    # otherwise call the real ElevenLabs API (or synthesize a real Twilio
    # Neural fragment) from the automated suite.
    async def _fake_voice_block(text: str) -> str:
        return "<Say>fake voice block</Say>"

    monkeypatch.setattr(call_orchestrator_module, "tts_voice_block", _fake_voice_block)


@pytest.fixture(autouse=True)
def _reset_bus_state():
    """Clears the in-memory event log/idempotency set between tests, and
    disables durable persistence so the suite stays fast and doesn't depend
    on (or pollute) whatever DATABASE_URL happens to be configured."""
    bus._event_log.clear()
    bus._processed_idempotency_keys.clear()
    bus.persist = False
    yield
    bus._event_log.clear()
    bus._processed_idempotency_keys.clear()


async def wait_until(predicate, timeout: float = 3.0, interval: float = 0.05):
    """Polls `predicate()` until it returns truthy or timeout elapses.
    Needed because bus.publish fires subscribers as fire-and-forget asyncio
    tasks, so downstream events land on a later loop iteration, not
    synchronously with the publish() call."""
    elapsed = 0.0
    while elapsed < timeout:
        if predicate():
            return True
        await asyncio.sleep(interval)
        elapsed += interval
    return predicate()
