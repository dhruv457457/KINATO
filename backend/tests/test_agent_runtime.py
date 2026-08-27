"""
Day 5 verification, per the rebuild plan:
  - offer-token gate: 40% ask -> MODIFY 10%; forged / expired / consumed /
    cross-merchant tokens all rejected by issue_offer
  - max_iterations halts cleanly (never raises)
  - a degraded agent cannot mutate
  - no tool schema accepts a forbidden argument name
"""
import uuid
import pytest

from app.agents.state import AgentContext
from app.agents.tools import ALL_TOOLS, FORBIDDEN_ARG_NAMES, check_offer, issue_offer
from app.agents.audit import execute_tool
from app.agents import runtime as agent_runtime
from app.db.repositories import checkouts as checkouts_repo
from app.db.repositories import offer_tokens as offer_tokens_repo
from app.core.ids import new_id


def _ctx(merchant_id, checkout_id=None, **overrides):
    return AgentContext(
        merchant_id=merchant_id,
        correlation_id=new_id("corr"),
        checkout_id=checkout_id,
        **overrides,
    )


@pytest.fixture
def cart_checkout(connected_merchant_id, unique_checkout_id):
    """A real checkout with a margin that only allows a 10% discount at
    most (default policy: max_discount=10, minimum_margin=15) - amount
    ₹1000, cogs ₹500 -> margin room is 35%, so the merchant's own 10%
    ceiling is the binding constraint, not margin."""
    checkouts_repo.create_checkout(
        merchant_id=connected_merchant_id,
        amount_paise=100_000,
        cogs_paise=50_000,
        checkout_id=unique_checkout_id,
    )
    return connected_merchant_id, unique_checkout_id


class TestOfferTokenGate:
    async def test_over_ceiling_request_is_modified_down(self, cart_checkout):
        merchant_id, checkout_id = cart_checkout
        ctx = _ctx(merchant_id, checkout_id)
        result = await execute_tool(check_offer, {"requested_discount_percent": 40}, ctx)
        assert result["decision"] == "MODIFY"
        assert result["approved_percent"] == 10.0
        assert result["requested_percent"] == 40
        assert result["offer_token"].startswith("off_")

    async def test_issue_offer_with_valid_token_succeeds(self, cart_checkout):
        merchant_id, checkout_id = cart_checkout
        ctx = _ctx(merchant_id, checkout_id)
        checked = await execute_tool(check_offer, {"requested_discount_percent": 5}, ctx)
        assert checked["decision"] == "ALLOW"

        issued = await execute_tool(
            issue_offer, {"offer_token": checked["offer_token"], "channel": "email"}, ctx
        )
        assert issued["status"] == "ISSUED"
        assert issued["approved_percent"] == 5
        assert issued["payment_url"]

    async def test_forged_token_is_rejected(self, cart_checkout):
        merchant_id, checkout_id = cart_checkout
        ctx = _ctx(merchant_id, checkout_id)
        result = await execute_tool(issue_offer, {"offer_token": "off_totally_made_up"}, ctx)
        assert result["status"] == "REJECTED"
        assert "not_found" in result["reason"]

    async def test_expired_token_is_rejected(self, cart_checkout):
        merchant_id, checkout_id = cart_checkout
        token_row = offer_tokens_repo.create_offer_token(
            merchant_id=merchant_id,
            decision="ALLOW",
            reason="within_margin_and_discount_limits",
            base_amount_paise=100_000,
            final_amount_paise=95_000,
            requested_percent=5,
            approved_percent=5,
            checkout_id=checkout_id,
            expires_in_seconds=-1,  # already expired
        )
        ctx = _ctx(merchant_id, checkout_id)
        result = await execute_tool(issue_offer, {"offer_token": token_row["offer_token"]}, ctx)
        assert result["status"] == "REJECTED"
        assert "expired" in result["reason"]

    async def test_consumed_token_cannot_be_reused(self, cart_checkout):
        merchant_id, checkout_id = cart_checkout
        ctx = _ctx(merchant_id, checkout_id)
        checked = await execute_tool(check_offer, {"requested_discount_percent": 5}, ctx)
        first = await execute_tool(issue_offer, {"offer_token": checked["offer_token"]}, ctx)
        assert first["status"] == "ISSUED"

        second = await execute_tool(issue_offer, {"offer_token": checked["offer_token"]}, ctx)
        assert second["status"] == "REJECTED"
        assert "consumed" in second["reason"]

    async def test_cross_merchant_token_is_rejected(self, cart_checkout):
        from app.db.repositories import merchants as merchants_repo
        from app.core.auth import hash_password

        merchant_id, checkout_id = cart_checkout
        ctx_owner = _ctx(merchant_id, checkout_id)
        checked = await execute_tool(check_offer, {"requested_discount_percent": 5}, ctx_owner)

        # A second, genuinely distinct merchant - not the same fixture
        # instance as `cart_checkout`'s (fixtures are cached per test, so
        # asking for `real_merchant_id` here would silently resolve to the
        # same merchant `connected_merchant_id` already built on).
        other_merchant = merchants_repo.create_merchant(
            name="Other Merchant",
            email=f"other_{uuid.uuid4().hex[:10]}@example.com",
            password_hash=hash_password("test-password-not-real"),
        )
        try:
            other_merchant_ctx = _ctx(other_merchant["merchant_id"], checkout_id)
            result = await execute_tool(issue_offer, {"offer_token": checked["offer_token"]}, other_merchant_ctx)
            assert result["status"] == "REJECTED"
            assert "merchant_mismatch" in result["reason"]
        finally:
            from app.db.database import get_db
            with get_db() as conn:
                cursor = conn.cursor()
                for table in ("audit_log", "events", "merchant_policies", "merchants"):
                    cursor.execute(f"DELETE FROM {table} WHERE merchant_id = %s", (other_merchant["merchant_id"],))

    async def test_denied_decision_mints_no_usable_token(self, connected_merchant_id, unique_checkout_id):
        # Cart with zero margin - any discount at all should DENY.
        checkouts_repo.create_checkout(
            merchant_id=connected_merchant_id,
            amount_paise=100_000,
            cogs_paise=100_000,  # cogs == price, zero margin
            checkout_id=unique_checkout_id,
        )
        ctx = _ctx(connected_merchant_id, unique_checkout_id)
        result = await execute_tool(check_offer, {"requested_discount_percent": 5}, ctx)
        assert result["decision"] == "DENY"
        assert "offer_token" not in result


class TestDegradedAgentCannotMutate:
    async def test_degraded_context_blocks_mutating_tool(self, cart_checkout):
        merchant_id, checkout_id = cart_checkout
        ctx = _ctx(merchant_id, checkout_id, degraded=True, allow_mutations=False, source="heuristic")
        result = await execute_tool(check_offer, {"requested_discount_percent": 5}, ctx)
        assert result == {"status": "REJECTED", "reason": "degraded_agent_cannot_mutate"}

    async def test_degraded_context_still_allows_read_only_tool(self, cart_checkout):
        from app.agents.tools import get_cart

        merchant_id, checkout_id = cart_checkout
        ctx = _ctx(merchant_id, checkout_id, degraded=True, allow_mutations=False, source="heuristic")
        result = await execute_tool(get_cart, {}, ctx)
        assert result["amount_paise"] == 100_000


class TestForbiddenArguments:
    def test_no_tool_schema_declares_a_forbidden_field(self):
        for tool in ALL_TOOLS:
            offending = FORBIDDEN_ARG_NAMES & set(tool.parameters.keys())
            assert not offending, f"{tool.name} exposes forbidden field(s): {offending}"

    async def test_forbidden_argument_is_rejected_even_if_passed_directly(self, cart_checkout):
        merchant_id, checkout_id = cart_checkout
        ctx = _ctx(merchant_id, checkout_id)
        # Simulates a malformed/adversarial tool call slipping a forbidden
        # field in alongside legitimate ones - execute_tool must catch this
        # even though no schema advertises it.
        result = await execute_tool(
            check_offer, {"requested_discount_percent": 5, "amount": 1}, ctx
        )
        assert result["status"] == "REJECTED"
        assert "forbidden_argument" in result["reason"]


class _FakeFunction:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, call_id, name, arguments):
        self.id = call_id
        self.function = _FakeFunction(name, arguments)


class _FakeMessage:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class _FakeChoice:
    def __init__(self, message):
        self.message = message


class _FakeResponse:
    def __init__(self, message):
        self.choices = [_FakeChoice(message)]


class _AlwaysCallsToolClient:
    """Fakes the OpenAI client to always request the same read-only tool
    call, forever - used to prove the runtime halts at max_iterations
    instead of looping forever against a misbehaving/adversarial model."""

    class _Chat:
        class _Completions:
            async def create(self, **kwargs):
                return _FakeResponse(
                    _FakeMessage(
                        content=None,
                        tool_calls=[_FakeToolCall(f"call_{uuid.uuid4().hex[:6]}", "get_cart", "{}")],
                    )
                )

        completions = _Completions()

    chat = _Chat()


class TestMaxIterations:
    async def test_runtime_halts_cleanly_at_max_iterations(self, monkeypatch, real_merchant_id):
        monkeypatch.setattr(agent_runtime, "_get_llm_client", lambda: _AlwaysCallsToolClient())
        ctx = _ctx(real_merchant_id)
        result = await agent_runtime.run_agent(
            system_prompt="test",
            user_message="hello",
            ctx=ctx,
            max_iterations=3,
            deadline_s=5.0,
        )
        assert result.ok is True
        assert result.iterations == 3
        assert result.output["reason"] == "max_iterations_reached"
        assert result.tool_calls_made == ["get_cart", "get_cart", "get_cart"]

    async def test_runtime_never_raises_when_no_llm_key_configured(self, monkeypatch, real_merchant_id):
        monkeypatch.setattr(agent_runtime, "_get_llm_client", lambda: None)
        ctx = _ctx(real_merchant_id)
        result = await agent_runtime.run_agent(system_prompt="test", user_message="hello", ctx=ctx)
        assert result.ok is True
        assert result.degraded is True
        assert result.output["reason"] == "no_llm_api_key_configured"
