"""
Day 6 verification for the rebuilt Conversation Agent
(app/channels/voice_runtime.py), updated for the Twilio-Neural-primary
voice decision (see app/services/tts.py's module docstring for why
ElevenLabs stopped being treated as fatal-if-unavailable). Covers what's
still true regardless of TTS vendor: real per-call context loading (no
hardcoded customer/product), and that agreement/opt-out are driven by the
agent's actual tool calls, not text pattern matching. The real Twilio call
itself is never exercised here (see conftest._fake_voice_dispatch for why)
- these tests drive the TwiML webhook handlers directly, the way Twilio
would.
"""
import json

import httpx
import pytest

from app.main import app
from app.agents.state import AgentResult
import app.channels.voice_runtime as voice_runtime
from app.db.repositories import checkouts as checkouts_repo
from app.db.repositories import customers as customers_repo
from app.db.repositories import recovery_attempts as recovery_attempts_repo


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def real_recovery_attempt(connected_merchant_id, unique_checkout_id):
    """A real recovery_attempt row with a real checkout (real line_items)
    and a real customer - the exact shape voice_dispatch.place_outbound_call
    would have been given by call_orchestrator."""
    merchant_id = connected_merchant_id
    checkout_id = unique_checkout_id
    checkouts_repo.create_checkout(
        merchant_id=merchant_id,
        amount_paise=249_900,
        cogs_paise=120_000,
        checkout_id=checkout_id,
        line_items=[{"product_id": "sku_1", "name": "Woven Table Runner"}],
    )
    customer = customers_repo.upsert_by_contact(merchant_id, phone="+911234500000", name="Asha")
    attempt = recovery_attempts_repo.create_recovery_attempt(merchant_id, checkout_id, customer["customer_id"])
    recovery_attempts_repo.update_state(
        attempt["recovery_attempt_id"],
        "CALLING",
        plan=json.dumps({"opening_line": "Hi Asha! I wanted to help you finish your order.", "degraded": False}),
    )
    voice_runtime.CALL_SESSIONS.clear()
    return attempt["recovery_attempt_id"]


class TestSessionLoading:
    def test_load_session_pulls_real_customer_and_product_names(self, real_recovery_attempt):
        session = voice_runtime._load_session_for_call(real_recovery_attempt)
        assert session is not None
        assert session["customer_name"] == "Asha"
        assert "Woven Table Runner" in session["item_description"]
        assert session["opening_line"] == "Hi Asha! I wanted to help you finish your order."
        assert session["ctx"].recovery_attempt_id == real_recovery_attempt

    def test_load_session_returns_none_for_unknown_id(self):
        assert voice_runtime._load_session_for_call("rec_does_not_exist") is None

    def test_describe_cart_never_fabricates_a_product_name(self):
        assert voice_runtime._describe_cart({"line_items": "[]"}) == "their order"
        assert voice_runtime._describe_cart({}) == "their order"


class TestOutboundWebhook:
    async def test_outbound_plays_the_real_opening_line(self, client, real_recovery_attempt, monkeypatch):
        monkeypatch.setattr(voice_runtime, "tts_voice_block", _make_fake_voice_block_fn())

        resp = await client.post(
            f"/voice/outbound?recovery_attempt_id={real_recovery_attempt}", data={"CallSid": "CAtest1"}
        )
        assert resp.status_code == 200
        assert "<Gather" in resp.text
        assert "https://fake-audio.test/voice.mp3" in resp.text
        assert "CAtest1" in voice_runtime.CALL_SESSIONS

    async def test_outbound_escalates_honestly_when_recovery_attempt_unknown(self, client):
        resp = await client.post(
            "/voice/outbound?recovery_attempt_id=rec_does_not_exist", data={"CallSid": "CAtest2"}
        )
        assert resp.status_code == 200
        assert "<Hangup" in resp.text
        assert "<Gather" not in resp.text

    async def test_outbound_continues_on_twilio_neural_voice_when_elevenlabs_unavailable(
        self, client, real_recovery_attempt, monkeypatch
    ):
        """The behavior this rewrite deliberately changed: ElevenLabs being
        unavailable is no longer fatal. voice_block() itself falls back to
        Twilio's Neural voice - the call must CONTINUE (Gather present),
        not hang up, and must not use the old robotic standard voice."""
        monkeypatch.setattr(
            voice_runtime, "tts_voice_block", _make_fake_voice_block_fn(elevenlabs_unavailable=True)
        )
        resp = await client.post(
            f"/voice/outbound?recovery_attempt_id={real_recovery_attempt}", data={"CallSid": "CAtest3"}
        )
        assert resp.status_code == 200
        assert "<Gather" in resp.text
        assert "<Hangup" not in resp.text
        assert voice_runtime.TWILIO_NEURAL_VOICE in resp.text
        assert "Polly.Aditi\"" not in resp.text  # the old robotic standard voice, never used again


class TestRespondWebhook:
    async def test_agreement_requires_a_real_tool_call_not_keyword_matching(
        self, client, real_recovery_attempt, monkeypatch
    ):
        """The whole point of the rewrite: the reply text can say anything -
        what matters is whether issue_offer actually ran."""
        monkeypatch.setattr(voice_runtime, "tts_voice_block", _make_fake_voice_block_fn())

        async def _fake_run_agent(**kwargs):
            return AgentResult(ok=True, output={"content": "Sure, sounds good!"}, tool_calls_made=["check_offer"])

        monkeypatch.setattr(voice_runtime.agent_runtime, "run_agent", _fake_run_agent)

        await client.post(f"/voice/outbound?recovery_attempt_id={real_recovery_attempt}", data={"CallSid": "CAtest4"})
        resp = await client.post("/voice/respond", data={"CallSid": "CAtest4", "SpeechResult": "yes sure, send it"})
        assert resp.status_code == 200
        assert "<Gather" in resp.text  # still an ongoing conversation, not ended

    async def test_record_opt_out_tool_call_ends_the_call(self, client, real_recovery_attempt, monkeypatch):
        monkeypatch.setattr(voice_runtime, "tts_voice_block", _make_fake_voice_block_fn())

        async def _fake_run_agent(**kwargs):
            return AgentResult(ok=True, output={"content": "Understood, I won't contact you again."}, tool_calls_made=["record_opt_out"])

        monkeypatch.setattr(voice_runtime.agent_runtime, "run_agent", _fake_run_agent)

        await client.post(f"/voice/outbound?recovery_attempt_id={real_recovery_attempt}", data={"CallSid": "CAtest5"})
        resp = await client.post("/voice/respond", data={"CallSid": "CAtest5", "SpeechResult": "please stop calling me"})
        assert resp.status_code == 200
        assert "<Hangup" in resp.text
        assert "<Gather" not in resp.text

    async def test_degraded_agent_gets_an_honest_generic_line_not_a_scripted_ladder(
        self, client, real_recovery_attempt, monkeypatch
    ):
        spoken_texts: list = []
        monkeypatch.setattr(voice_runtime, "tts_voice_block", _make_fake_voice_block_fn(recorder=spoken_texts))

        async def _fake_run_agent(**kwargs):
            return AgentResult(ok=True, output={"degraded": True, "reason": "no_llm_api_key_configured"}, degraded=True)

        monkeypatch.setattr(voice_runtime.agent_runtime, "run_agent", _fake_run_agent)

        await client.post(f"/voice/outbound?recovery_attempt_id={real_recovery_attempt}", data={"CallSid": "CAtest6"})
        resp = await client.post("/voice/respond", data={"CallSid": "CAtest6", "SpeechResult": "hello?"})
        assert resp.status_code == 200
        assert "follow up with you by email" in spoken_texts[-1]
        # None of the old hardcoded ladder numbers should ever appear.
        assert "3,149" not in spoken_texts[-1] and "3499" not in spoken_texts[-1]


def _make_fake_voice_block_fn(elevenlabs_unavailable: bool = False, recorder: list = None):
    """Fakes tts.voice_block(): normally a <Play> block; when ElevenLabs is
    "unavailable", a Twilio Neural <Say> block instead - matching the real
    function's own fallback behavior (see app/services/tts.py)."""
    async def _fake(text: str) -> str:
        if recorder is not None:
            recorder.append(text)
        if elevenlabs_unavailable:
            return f'<Say voice="{voice_runtime.TWILIO_NEURAL_VOICE}">{text}</Say>'
        return "<Play>https://fake-audio.test/voice.mp3</Play>"
    return _fake
