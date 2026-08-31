"""One "yes" must never become two payment links.

When a webhook takes longer than Twilio is willing to wait, Twilio RETRIES
the POST with identical parameters. Nothing here noticed: the retry
rehydrated the session and ran the agent again on the same sentence, and on
the wrong turn that means a second offer token and a second Razorpay link for
one thing the customer said once.

Every other duplicate-delivery path in this system is guarded - the event bus
has durable idempotency keys, the payment webhook dedupes on payment_id - and
this one, the only path a customer actually hears, was not.

Telling a retry apart from a person is the hard part, and two obvious designs
are both wrong:

  * Keying on the turn index cannot work: the first attempt increments
    `turns`, so a retry never matches the key the original wrote.

  * Keying on the spoken words works for retries and is wrong for people. A
    customer who says "yes" twice in twenty seconds is not Twilio retrying,
    and replaying the previous answer to a different question is its own bug.

What separates them is concurrency. Twilio only retries when it received NO
response, so a retry arrives while the original is still running; a human
always speaks after we have answered. That is what these tests pin.
"""
import asyncio

import httpx
import pytest

import app.channels.voice_runtime as voice_runtime
from app.main import app
from app.db.repositories import checkouts as checkouts_repo
from app.db.repositories import recovery_attempts as recovery_attempts_repo


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def attempt(connected_merchant_id, unique_checkout_id):
    checkouts_repo.create_checkout(
        merchant_id=connected_merchant_id,
        amount_paise=100_000,
        cogs_paise=50_000,
        checkout_id=unique_checkout_id,
        line_items=[{"product_id": "sku_1", "name": "Woven Table Runner"}],
    )
    row = recovery_attempts_repo.create_recovery_attempt(
        connected_merchant_id, unique_checkout_id, None
    )
    return row["recovery_attempt_id"]


class TestATwilioRetryDoesNotRunTheAgentTwice:
    async def test_a_concurrent_duplicate_is_held_not_executed(
        self, client, attempt, monkeypatch
    ):
        """The retry arrives while the first turn is still working.

        The agent must run exactly ONCE. The second request is answered with
        a hold, because the turn it is duplicating is about to reply.
        """
        call_sid = "CAinflight1"
        await client.post(f"/voice/outbound?recovery_attempt_id={attempt}", data={"CallSid": call_sid})

        runs = {"n": 0}
        started = asyncio.Event()
        release = asyncio.Event()
        original = voice_runtime.agent_runtime.run_agent

        async def _slow_run_agent(*args, **kwargs):
            runs["n"] += 1
            started.set()
            await release.wait()
            return await original(*args, **kwargs)

        monkeypatch.setattr(voice_runtime.agent_runtime, "run_agent", _slow_run_agent)

        first = asyncio.create_task(
            client.post("/voice/respond", data={"CallSid": call_sid, "SpeechResult": "yes send it"})
        )
        await asyncio.wait_for(started.wait(), timeout=10)

        # Twilio, having waited and heard nothing, resends the identical POST.
        retry = await client.post(
            "/voice/respond", data={"CallSid": call_sid, "SpeechResult": "yes send it"}
        )

        release.set()
        await first

        assert runs["n"] == 1, "the agent ran twice for one thing the customer said once"
        assert retry.status_code == 200
        body = retry.text
        # Held, not answered: the original turn is about to speak, and two
        # replies to one sentence is the confusion this prevents arriving by
        # another route.
        assert "<Redirect" in body, "the retry must be told to come back"
        assert "<Say" not in body and "<Play>" not in body, "the hold must not talk over the real turn"
        assert "<Hangup" not in body, "the original turn is still working"

    async def test_the_guard_is_released_once_the_turn_answers(self, client, attempt):
        """A guard that leaks its flag is worse than no guard.

        If the CallSid stayed in the set, every remaining turn of that call
        would get the hold response and the conversation would be over -
        far worse than the duplicate it prevents.
        """
        call_sid = "CAinflight2"
        await client.post(f"/voice/outbound?recovery_attempt_id={attempt}", data={"CallSid": call_sid})

        await client.post("/voice/respond", data={"CallSid": call_sid, "SpeechResult": "hello there"})
        assert call_sid not in voice_runtime._TURNS_IN_FLIGHT

        # And the NEXT thing the customer says is a real turn, not a retry.
        second = await client.post(
            "/voice/respond", data={"CallSid": call_sid, "SpeechResult": "hello there"}
        )
        assert second.status_code == 200
        assert "<Redirect" not in second.text, (
            "a customer repeating themselves after we answered is a new turn, not a retry"
        )

    async def test_two_calls_do_not_block_each_other(self, client, attempt):
        """Per CallSid, not global - one slow call must not hold everyone."""
        voice_runtime._TURNS_IN_FLIGHT.add("CAsomeone_else")
        try:
            call_sid = "CAinflight3"
            await client.post(
                f"/voice/outbound?recovery_attempt_id={attempt}", data={"CallSid": call_sid}
            )
            resp = await client.post(
                "/voice/respond", data={"CallSid": call_sid, "SpeechResult": "hello"}
            )
            assert resp.status_code == 200
            assert "<Redirect" not in resp.text
        finally:
            voice_runtime._TURNS_IN_FLIGHT.discard("CAsomeone_else")
