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

    async def test_issue_offer_never_runs(self, monkeypatch, unpaid_cart):
        """The live failure spent a mutating call and its latency on a token
        that could not exist. tool_calls_made means "what ran"."""
        merchant_id, checkout_id = unpaid_cart
        result = await self._run(monkeypatch, merchant_id, checkout_id)
        assert "issue_offer" not in result.tool_calls_made
        assert "check_offer" in result.tool_calls_made

    async def test_the_refusal_is_still_counted(self, monkeypatch, unpaid_cart):
        """Not executing it must not make it invisible. A model reaching for
        a money tool it has no token for is exactly the thing the refusal
        tallies exist to surface."""
        merchant_id, checkout_id = unpaid_cart
        result = await self._run(monkeypatch, merchant_id, checkout_id)
        reasons = [r["reason"] for r in result.refusals if r["tool"] == "issue_offer"]
        assert reasons == ["REJECTED_OFFER_TOKEN_NOT_YET_MINTED"]

    async def test_check_offer_still_mints_a_real_token(self, monkeypatch, unpaid_cart):
        """The point of refusing the premature call is that the legitimate
        one in the same batch survives it with a spendable token."""
        merchant_id, checkout_id = unpaid_cart
        await self._run(monkeypatch, merchant_id, checkout_id)
        minted = offer_tokens_repo.concessions_already_made(merchant_id, checkout_id)
        # A full-price (0%) token is not a concession, so that counter stays
        # at zero - the token itself is what must exist.
        assert minted == 0

    @pytest.mark.parametrize("invented", ["token", "token_from_check_offer", "off_deadbeef"])
    async def test_no_invented_token_is_ever_spent(self, monkeypatch, unpaid_cart, invented):
        """Including one shaped like a real token. The batch is refused on
        ordering, before the value is even looked up."""
        merchant_id, checkout_id = unpaid_cart
        result = await self._run(monkeypatch, merchant_id, checkout_id, token=invented)
        assert "issue_offer" not in result.tool_calls_made
        assert result.ok is True

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
