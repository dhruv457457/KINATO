"""
Surviving speech-to-text.

Twilio has posted a `Confidence` float with every single Gather callback
since the first call this project ever placed, and nothing read it. A
0.12-confidence garble reached the agent with exactly the same authority as
a clean 0.95 utterance and could move real money on the strength of it.

These tests cover the two mechanisms that close that, and one property that
must hold for both:

  * Below STT_UNUSABLE_FLOOR the model is not called at all - there is
    nothing to reason about, and reasoning about it anyway produces a
    fluent reply to words nobody said.
  * Below LOW_CONFIDENCE_FLOOR the conversation continues but no money tool
    may run. Enforced inside the tools, not asked of the prompt.
  * The keypad is exempt, because a pressed digit is not a transcription.

The last one is the point of the whole design: DTMF is the only input path
in this system that speech recognition cannot corrupt, which is exactly why
opt-out lives on it as well as on speech.
"""
import json

import httpx
import pytest

from app.main import app
from app.agents.state import AgentContext, AgentResult
from app.agents import tools as tools_module
import app.channels.voice_runtime as voice_runtime
from app.db.repositories import checkouts as checkouts_repo
from app.db.repositories import consents as consents_repo
from app.db.repositories import customers as customers_repo
from app.db.repositories import recovery_attempts as recovery_attempts_repo


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def call_case(connected_merchant_id, unique_checkout_id):
    """A real recovery attempt with a real checkout and customer, plus
    granted voice consent - the state a live call actually starts from."""
    merchant_id = connected_merchant_id
    checkout_id = unique_checkout_id
    checkouts_repo.create_checkout(
        merchant_id=merchant_id,
        amount_paise=249_900,
        cogs_paise=100_000,
        checkout_id=checkout_id,
        line_items=[{"product_id": "sku_1", "name": "Woven Table Runner"}],
    )
    customer = customers_repo.upsert_by_contact(merchant_id, phone="+911234500001", name="Asha")
    consents_repo.record_consent(
        merchant_id, customer["customer_id"], "voice", "granted", source="test_fixture"
    )
    attempt = recovery_attempts_repo.create_recovery_attempt(
        merchant_id, checkout_id, customer["customer_id"]
    )
    recovery_attempts_repo.update_state(
        attempt["recovery_attempt_id"],
        "CALLING",
        plan=json.dumps({"opening_line": "Hi Asha!", "degraded": False}),
    )
    voice_runtime.CALL_SESSIONS.clear()
    return {
        "merchant_id": merchant_id,
        "checkout_id": checkout_id,
        "customer_id": customer["customer_id"],
        "recovery_attempt_id": attempt["recovery_attempt_id"],
    }


@pytest.fixture
def fake_tts(monkeypatch):
    """Records every line the agent would have spoken aloud."""
    spoken: list = []

    async def _fake(text: str) -> str:
        spoken.append(text)
        return "<Play>https://fake-audio.test/voice.mp3</Play>"

    monkeypatch.setattr(voice_runtime, "tts_voice_block", _fake)
    return spoken


class _AgentSpy:
    """Stands in for agent_runtime.run_agent and records whether it ran at
    all. "The model was not called" is the actual assertion for an unusable
    turn - a reply that merely looks sensible is not evidence of that."""

    def __init__(self, result: AgentResult = None):
        self.calls: list = []
        self._result = result or AgentResult(ok=True, output={"content": "Sure."})

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self._result


async def _open_call(client, case, call_sid):
    return await client.post(
        f"/voice/outbound?recovery_attempt_id={case['recovery_attempt_id']}",
        data={"CallSid": call_sid},
    )


# ---------------------------------------------------------------------------
# The tools themselves. These are the guarantee; the webhook is only the
# thing that supplies the number.
# ---------------------------------------------------------------------------

class TestMoneyToolsRefuseWhatWeDidNotHear:
    async def test_check_offer_refuses_below_the_confidence_floor(self, call_case):
        ctx = AgentContext(
            merchant_id=call_case["merchant_id"],
            correlation_id="test",
            customer_id=call_case["customer_id"],
            checkout_id=call_case["checkout_id"],
            recovery_attempt_id=call_case["recovery_attempt_id"],
            stt_confidence=0.31,  # usable enough to answer, not to spend money on
            barrier_confirmed=True,
        )
        result = await tools_module._check_offer(ctx, requested_discount_percent=10, reason="price")
        assert result["decision"] == "DENY"
        assert result["reason"] == "REJECTED_LOW_CONFIDENCE"

    async def test_issue_offer_refuses_independently_of_check_offer(self, call_case):
        """A token minted on a clearly-heard turn must not become spendable
        by a later garbled "yes" that was never a yes."""
        ctx = AgentContext(
            merchant_id=call_case["merchant_id"],
            correlation_id="test",
            customer_id=call_case["customer_id"],
            checkout_id=call_case["checkout_id"],
            recovery_attempt_id=call_case["recovery_attempt_id"],
            stt_confidence=0.2,
        )
        result = await tools_module._issue_offer(ctx, offer_token="tok_anything")
        assert result["status"] == "REJECTED"
        assert result["reason"] == "REJECTED_LOW_CONFIDENCE"

    async def test_a_clearly_heard_turn_is_not_blocked(self, call_case):
        ctx = AgentContext(
            merchant_id=call_case["merchant_id"],
            correlation_id="test",
            customer_id=call_case["customer_id"],
            checkout_id=call_case["checkout_id"],
            recovery_attempt_id=call_case["recovery_attempt_id"],
            stt_confidence=0.94,
            barrier_confirmed=True,
        )
        result = await tools_module._check_offer(ctx, requested_discount_percent=5, reason="price")
        assert result["decision"] in ("ALLOW", "MODIFY")
        assert result["offer_token"]

    async def test_none_confidence_means_not_applicable_not_assume_fine(self, call_case):
        """Keypad input, email replies and scripted batch cases have no
        transcription confidence at all. None must not be read as zero."""
        ctx = AgentContext(
            merchant_id=call_case["merchant_id"],
            correlation_id="test",
            customer_id=call_case["customer_id"],
            checkout_id=call_case["checkout_id"],
            recovery_attempt_id=call_case["recovery_attempt_id"],
            stt_confidence=None,
            barrier_confirmed=True,
        )
        result = await tools_module._check_offer(ctx, requested_discount_percent=5, reason="price")
        assert result["decision"] in ("ALLOW", "MODIFY")


class TestBarrierMustBeConfirmedBeforeSpendingMargin:
    async def test_discount_is_refused_until_the_barrier_is_confirmed(self, call_case):
        ctx = AgentContext(
            merchant_id=call_case["merchant_id"],
            correlation_id="test",
            customer_id=call_case["customer_id"],
            checkout_id=call_case["checkout_id"],
            recovery_attempt_id=call_case["recovery_attempt_id"],
            # Usable, but inside the band where we could have misheard.
            stt_confidence=0.7,
            input_is_speech=True,
            barrier_confirmed=False,
        )
        result = await tools_module._check_offer(ctx, requested_discount_percent=15, reason="price")
        assert result["decision"] == "DENY"
        assert result["reason"] == "REJECTED_UNCONFIRMED_BARRIER"

    async def test_a_clearly_heard_request_needs_no_confirmation(self, call_case):
        """"Can you do 40% off?" at 0.95 confidence states the barrier in
        the customer's own words. Reading it back to them is the scripted
        interrogation FINDINGS #1 is about, and on a short call it loses
        the sale outright."""
        ctx = AgentContext(
            merchant_id=call_case["merchant_id"],
            correlation_id="test",
            customer_id=call_case["customer_id"],
            checkout_id=call_case["checkout_id"],
            recovery_attempt_id=call_case["recovery_attempt_id"],
            stt_confidence=0.95,
            input_is_speech=True,
            barrier_confirmed=False,
        )
        result = await tools_module._check_offer(ctx, requested_discount_percent=40, reason="they asked for 40%")
        assert result["decision"] in ("ALLOW", "MODIFY")

    async def test_a_non_speech_turn_is_not_gated_on_confirmation(self, call_case):
        """The rule guards against MISHEARING. A keypad press, an email
        reply or a scripted batch case cannot be misheard, so applying it
        there would block discounts on every channel that is not a phone
        call - a silent, product-wide behaviour change riding on a default
        value."""
        ctx = AgentContext(
            merchant_id=call_case["merchant_id"],
            correlation_id="test",
            customer_id=call_case["customer_id"],
            checkout_id=call_case["checkout_id"],
            recovery_attempt_id=call_case["recovery_attempt_id"],
            input_is_speech=False,
            barrier_confirmed=False,
        )
        result = await tools_module._check_offer(ctx, requested_discount_percent=5, reason="price")
        assert result["decision"] in ("ALLOW", "MODIFY")

    async def test_full_price_is_never_gated_on_confirmation(self, call_case):
        """FINDINGS #1: a customer who asks for the link gets the link. The
        confirmation turn exists to protect margin, never to delay a sale
        that has already been won."""
        ctx = AgentContext(
            merchant_id=call_case["merchant_id"],
            correlation_id="test",
            customer_id=call_case["customer_id"],
            checkout_id=call_case["checkout_id"],
            recovery_attempt_id=call_case["recovery_attempt_id"],
            stt_confidence=0.9,
            input_is_speech=True,
            barrier_confirmed=False,
        )
        result = await tools_module._check_offer(ctx, requested_discount_percent=0, reason="ready to buy")
        assert result["decision"] in ("ALLOW", "MODIFY")
        assert result["approved_percent"] == 0


# ---------------------------------------------------------------------------
# The live webhook.
# ---------------------------------------------------------------------------

class TestUnusableTranscriptionNeverReachesTheModel:
    async def test_garbled_turn_does_not_call_the_model_at_all(
        self, client, call_case, fake_tts, monkeypatch
    ):
        spy = _AgentSpy()
        monkeypatch.setattr(voice_runtime.agent_runtime, "run_agent", spy)

        await _open_call(client, call_case, "CAstt1")
        resp = await client.post(
            "/voice/respond",
            data={"CallSid": "CAstt1", "SpeechResult": "mumble garble", "Confidence": "0.15"},
        )

        assert resp.status_code == 200
        assert spy.calls == []  # the whole point
        assert "<Gather" in resp.text  # conversation continues
        assert "say that again" in fake_tts[-1]

    async def test_second_consecutive_miss_offers_the_keypad_instead_of_asking_again(
        self, client, call_case, fake_tts, monkeypatch
    ):
        spy = _AgentSpy()
        monkeypatch.setattr(voice_runtime.agent_runtime, "run_agent", spy)

        await _open_call(client, call_case, "CAstt2")
        for _ in range(2):
            resp = await client.post(
                "/voice/respond",
                data={"CallSid": "CAstt2", "SpeechResult": "###", "Confidence": "0.05"},
            )

        assert spy.calls == []
        assert "press 9" in fake_tts[-1].lower()
        assert "<Gather" in resp.text

    async def test_a_usable_turn_resets_the_streak(self, client, call_case, fake_tts, monkeypatch):
        spy = _AgentSpy()
        monkeypatch.setattr(voice_runtime.agent_runtime, "run_agent", spy)

        await _open_call(client, call_case, "CAstt3")
        await client.post(
            "/voice/respond", data={"CallSid": "CAstt3", "SpeechResult": "???", "Confidence": "0.1"}
        )
        await client.post(
            "/voice/respond",
            data={"CallSid": "CAstt3", "SpeechResult": "yes I am here", "Confidence": "0.9"},
        )
        assert voice_runtime.CALL_SESSIONS["CAstt3"]["misheard_streak"] == 0
        assert len(spy.calls) == 1

    async def test_silence_is_reprompted_not_handed_to_the_model_as_empty_speech(
        self, client, call_case, fake_tts, monkeypatch
    ):
        spy = _AgentSpy()
        monkeypatch.setattr(voice_runtime.agent_runtime, "run_agent", spy)

        await _open_call(client, call_case, "CAstt4")
        resp = await client.post("/voice/respond", data={"CallSid": "CAstt4", "SpeechResult": ""})

        assert spy.calls == []
        assert "<Gather" in resp.text
        assert "didn't hear anything" in fake_tts[-1]


class TestConfidenceIsPassedToTheAgentPerTurn:
    async def test_the_turns_confidence_reaches_the_agent_context(
        self, client, call_case, fake_tts, monkeypatch
    ):
        spy = _AgentSpy()
        monkeypatch.setattr(voice_runtime.agent_runtime, "run_agent", spy)

        await _open_call(client, call_case, "CAstt5")
        await client.post(
            "/voice/respond",
            data={"CallSid": "CAstt5", "SpeechResult": "it's a bit expensive", "Confidence": "0.42"},
        )

        assert spy.calls[0]["ctx"].stt_confidence == pytest.approx(0.42)
        assert spy.calls[0]["ctx"].input_is_speech is True

    async def test_an_unconfirmed_barrier_refusal_opens_the_confirmation_turn(
        self, client, call_case, fake_tts, monkeypatch
    ):
        """The state machine runs on the tool's real refusal, not on
        matching the customer's words against a list of agreement phrases -
        which is the pattern this file's own rewrite deleted."""
        spy = _AgentSpy(
            AgentResult(
                ok=True,
                output={"content": "So it's the price that's holding you up?"},
                tool_calls_made=["check_offer"],
                refusals=[{"tool": "check_offer", "reason": "REJECTED_UNCONFIRMED_BARRIER"}],
            )
        )
        monkeypatch.setattr(voice_runtime.agent_runtime, "run_agent", spy)

        await _open_call(client, call_case, "CAstt6")
        assert voice_runtime.CALL_SESSIONS["CAstt6"]["discount_bounced"] is False

        await client.post(
            "/voice/respond",
            data={"CallSid": "CAstt6", "SpeechResult": "too costly for me", "Confidence": "0.85"},
        )
        assert voice_runtime.CALL_SESSIONS["CAstt6"]["discount_bounced"] is True

        # The next turn is an answer to the confirming question, so the
        # discount path is now open to the agent.
        await client.post(
            "/voice/respond", data={"CallSid": "CAstt6", "SpeechResult": "yes that's right", "Confidence": "0.88"}
        )
        assert spy.calls[-1]["ctx"].barrier_confirmed is True


class TestTheKeypadCannotBeMisheard:
    async def test_every_gather_accepts_speech_and_dtmf(self, client, call_case, fake_tts):
        resp = await _open_call(client, call_case, "CAdtmf0")
        assert 'input="speech dtmf"' in resp.text
        assert 'numDigits="1"' in resp.text

    async def test_the_opening_tells_them_about_9(self, client, call_case, fake_tts):
        resp = await _open_call(client, call_case, "CAdtmf1")
        assert "press 9" in resp.text.lower()

    async def test_pressing_9_revokes_consent_without_calling_the_model(
        self, client, call_case, fake_tts, monkeypatch
    ):
        spy = _AgentSpy()
        monkeypatch.setattr(voice_runtime.agent_runtime, "run_agent", spy)

        assert consents_repo.check_consent(
            call_case["merchant_id"], call_case["customer_id"], "voice"
        ) is True

        await _open_call(client, call_case, "CAdtmf2")
        resp = await client.post("/voice/respond", data={"CallSid": "CAdtmf2", "Digits": "9"})

        assert resp.status_code == 200
        assert "<Hangup" in resp.text
        assert spy.calls == []
        assert consents_repo.check_consent(
            call_case["merchant_id"], call_case["customer_id"], "voice"
        ) is False

    async def test_pressing_0_records_a_callback_request(self, client, call_case, fake_tts, monkeypatch):
        monkeypatch.setattr(voice_runtime.agent_runtime, "run_agent", _AgentSpy())

        await _open_call(client, call_case, "CAdtmf3")
        resp = await client.post("/voice/respond", data={"CallSid": "CAdtmf3", "Digits": "0"})

        assert "<Hangup" in resp.text
        attempt = recovery_attempts_repo.get_recovery_attempt(call_case["recovery_attempt_id"])
        assert attempt["state"] == "CALLBACK_REQUESTED"
        assert attempt["callback_requested_at"] is not None

    async def test_pressing_1_runs_a_turn_with_no_confidence_gate(
        self, client, call_case, fake_tts, monkeypatch
    ):
        """A pressed key is not a transcription. It must not be subject to
        the mishearing gate, or the one reliable input path would be the
        one that cannot complete a sale."""
        spy = _AgentSpy()
        monkeypatch.setattr(voice_runtime.agent_runtime, "run_agent", spy)

        await _open_call(client, call_case, "CAdtmf4")
        await client.post("/voice/respond", data={"CallSid": "CAdtmf4", "Digits": "1"})

        assert len(spy.calls) == 1
        assert spy.calls[0]["ctx"].stt_confidence is None
        assert spy.calls[0]["ctx"].input_is_speech is False
        assert "yes" in spy.calls[0]["user_message"].lower()

    async def test_a_digit_beats_whatever_noise_was_transcribed_alongside_it(
        self, client, call_case, fake_tts, monkeypatch
    ):
        spy = _AgentSpy()
        monkeypatch.setattr(voice_runtime.agent_runtime, "run_agent", spy)

        await _open_call(client, call_case, "CAdtmf5")
        resp = await client.post(
            "/voice/respond",
            data={"CallSid": "CAdtmf5", "Digits": "9", "SpeechResult": "yes send it", "Confidence": "0.9"},
        )
        # The deliberate act wins. Acting on the speech here would sell to
        # someone who just asked to be left alone.
        assert "<Hangup" in resp.text
        assert spy.calls == []


class TestConfidenceParsing:
    def test_missing_and_unparseable_confidence_are_not_applicable(self):
        assert voice_runtime._parse_confidence(None) is None
        assert voice_runtime._parse_confidence("") is None
        assert voice_runtime._parse_confidence("high") is None

    def test_values_are_clamped(self):
        assert voice_runtime._parse_confidence("1.4") == 1.0
        assert voice_runtime._parse_confidence("-0.2") == 0.0
        assert voice_runtime._parse_confidence("0.73") == pytest.approx(0.73)
