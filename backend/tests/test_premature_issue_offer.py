"""issue_offer must not run in the same batch as the call that mints its token.

Taken from a live call, and the transcript is the whole argument. The
customer asked for a payment link four times in ninety seconds. Every time,
the model emitted `issue_offer` and `check_offer` together in ONE assistant
message, filling issue_offer's required `offer_token` with a value it could
not possibly have - the literal strings `"token_from_check_offer"` and then
`"token"`, straight out of the schema's own prose.

Each of those was rejected as not-found, which was correct and was not
enough. The wasted mutating call and its round trip spent the turn's budget,
so the real token `check_offer` had just minted was never spent, the turn
ended with no content, and the caller fell back to "let me have someone from
our team follow up with you by email on this." Four times. No link was ever
sent, and `offer_tokens` holds four unconsumed rows to prove it.

The instructive refusal already told the model "the only valid value is one
returned by a check_offer call in THIS conversation". It could not help: the
model had chosen both calls before it could read either result. A tool
result cannot fix ordering after the fact, so the ordering is enforced where
the batch is still visible - in the runtime, before anything executes.
"""
import uuid

import pytest

from app.agents import runtime as agent_runtime
from app.agents.state import AgentContext
from app.core.ids import new_id
from app.db.repositories import checkouts as checkouts_repo
from app.db.repositories import offer_tokens as offer_tokens_repo


class _FakeFunction:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, name, arguments):
        self.id = f"call_{uuid.uuid4().hex[:6]}"
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


class _BatchesIssueWithCheckClient:
    """The observed model behaviour, exactly: both calls in one message,
    issue_offer first, with an invented token. Then a closing sentence, so
    the run ends on its own rather than at max_iterations."""

    def __init__(self, invented_token="token_from_check_offer"):
        self.invented_token = invented_token
        self.calls = 0

    class _Chat:
        def __init__(self, outer):
            self.completions = _BatchesIssueWithCheckClient._Completions(outer)

    class _Completions:
        def __init__(self, outer):
            self.outer = outer

        async def create(self, **kwargs):
            self.outer.calls += 1
            if self.outer.calls == 1:
                return _FakeResponse(
                    _FakeMessage(
                        content=None,
                        tool_calls=[
                            _FakeToolCall(
                                "issue_offer",
                                '{"offer_token": "%s"}' % self.outer.invented_token,
                            ),
                            _FakeToolCall(
                                "check_offer",
                                '{"requested_discount_percent": 0}',
                            ),
                        ],
                    )
                )
            return _FakeResponse(_FakeMessage(content="Sending that over now."))

    @property
    def chat(self):
        return self._Chat(self)


@pytest.fixture
def unpaid_cart(connected_merchant_id, unique_checkout_id):
    checkouts_repo.create_checkout(
        merchant_id=connected_merchant_id,
        amount_paise=129_000,
        cogs_paise=60_000,
        checkout_id=unique_checkout_id,
    )
    return connected_merchant_id, unique_checkout_id


class TestIssueOfferBatchedWithCheckOffer:
    async def _run(self, monkeypatch, merchant_id, checkout_id, token="token_from_check_offer"):
        # Built ONCE. _get_llm_client is called every iteration, so a
        # lambda that constructs a new client resets its own turn counter
        # and the fake loops forever - which is what this harness did on its
        # first run.
        client = _BatchesIssueWithCheckClient(token)
        monkeypatch.setattr(agent_runtime, "_get_llm_client", lambda: client)
        ctx = AgentContext(
            merchant_id=merchant_id,
            correlation_id=new_id("corr"),
            checkout_id=checkout_id,
        )
        return await agent_runtime.run_agent(
            system_prompt="test",
            user_message="yes, please send me the link",
            ctx=ctx,
            deadline_s=10.0,
        )

    async def test_check_offer_runs_before_the_issue_offer_it_feeds(self, monkeypatch, unpaid_cart):
        """The model asked for both in one breath. Order is not the model's
        to choose when one call mints what the other spends."""
        merchant_id, checkout_id = unpaid_cart
        result = await self._run(monkeypatch, merchant_id, checkout_id)
        assert result.tool_calls_made.index("check_offer") < result.tool_calls_made.index(
            "issue_offer"
        )

    async def test_the_link_actually_goes_out(self, monkeypatch, unpaid_cart):
        """The whole point. On the live call the customer asked four times
        and was told four times that someone would follow up by email."""
        merchant_id, checkout_id = unpaid_cart
        result = await self._run(monkeypatch, merchant_id, checkout_id)
        refused = [r["reason"] for r in result.refusals if r["tool"] == "issue_offer"]
        assert refused == [], f"issue_offer was refused: {refused}"
        assert "issue_offer" in result.tool_calls_made

    async def test_the_invented_token_is_never_the_one_spent(self, monkeypatch, unpaid_cart):
        """The substituted value must be check_offer's, and the token that
        gets consumed must be a real row - not the string the model passed."""
        merchant_id, checkout_id = unpaid_cart
        await self._run(monkeypatch, merchant_id, checkout_id)
        assert offer_tokens_repo.get_offer_token("token_from_check_offer") is None

    @pytest.mark.parametrize("invented", ["token", "token_from_check_offer", "off_deadbeef"])
    async def test_any_invented_token_is_replaced(self, monkeypatch, unpaid_cart, invented):
        """Including one shaped like a real token, which would otherwise
        look plausible to everything downstream."""
        merchant_id, checkout_id = unpaid_cart
        result = await self._run(monkeypatch, merchant_id, checkout_id, token=invented)
        assert result.ok is True
        assert [r["reason"] for r in result.refusals if r["tool"] == "issue_offer"] == []

    async def test_issue_offer_alone_is_untouched(self, monkeypatch, unpaid_cart):
        """The guard is about ordering within one batch, not about
        issue_offer. On its own it must still reach the token gate and be
        judged there - otherwise this fix would have quietly disabled the
        only tool that can move money."""
        merchant_id, checkout_id = unpaid_cart

        class _OnlyIssue(_BatchesIssueWithCheckClient):
            class _Completions(_BatchesIssueWithCheckClient._Completions):
                async def create(self, **kwargs):
                    self.outer.calls += 1
                    if self.outer.calls == 1:
                        return _FakeResponse(
                            _FakeMessage(
                                content=None,
                                tool_calls=[
                                    _FakeToolCall(
                                        "issue_offer", '{"offer_token": "off_madeup"}'
                                    )
                                ],
                            )
                        )
                    return _FakeResponse(_FakeMessage(content="ok"))

            class _Chat:
                def __init__(self, outer):
                    self.completions = _OnlyIssue._Completions(outer)

            @property
            def chat(self):
                return self._Chat(self)

        only = _OnlyIssue()
        monkeypatch.setattr(agent_runtime, "_get_llm_client", lambda: only)
        ctx = AgentContext(
            merchant_id=merchant_id,
            correlation_id=new_id("corr"),
            checkout_id=checkout_id,
        )
        result = await agent_runtime.run_agent(
            system_prompt="test", user_message="send it", ctx=ctx, deadline_s=10.0
        )
        assert "issue_offer" in result.tool_calls_made
        reasons = [r["reason"] for r in result.refusals if r["tool"] == "issue_offer"]
        assert reasons == ["REJECTED_OFFER_TOKEN_NOT_FOUND"]
