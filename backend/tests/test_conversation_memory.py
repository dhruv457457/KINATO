"""
The agent remembering anything at all.

Before `conversation_turns` existed, a call's dialogue lived in a
module-global dict and in stdout. That one decision produced four separate
failures, and this file covers all of them:

  * the recovery drawer showed tool calls with no conversation beside them
  * a second worker process could not serve a turn of a call it had not
    started
  * a restart mid-call answered a mid-sentence customer with "Sorry, I lost
    track of our order details"
  * every later attempt for the same customer began from nothing, re-asking
    a question they had already answered

The restart test is the one worth reading. It is also the only one here
that would have been impossible to write before, because there was nothing
to restore from.
"""
import json

import httpx
import pytest

from app.main import app
from app.agents.state import AgentResult
from app.agents import runtime as agent_runtime
import app.channels.voice_runtime as voice_runtime
from app.db.repositories import checkouts as checkouts_repo
from app.db.repositories import conversation_turns as turns_repo
from app.db.repositories import customers as customers_repo
from app.db.repositories import recovery_attempts as ra_repo
from app.services import customer_memory
from tests.conftest import wait_until


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def fake_tts(monkeypatch):
    async def _fake(text: str) -> str:
        return "<Play>https://fake-audio.test/voice.mp3</Play>"

    monkeypatch.setattr(voice_runtime, "tts_voice_block", _fake)


@pytest.fixture
def call_case(connected_merchant_id, unique_checkout_id):
    checkouts_repo.create_checkout(
        merchant_id=connected_merchant_id,
        amount_paise=249_900,
        cogs_paise=100_000,
        checkout_id=unique_checkout_id,
        line_items=[{"product_id": "sku_1", "name": "Woven Table Runner"}],
    )
    customer = customers_repo.upsert_by_contact(
        connected_merchant_id, phone="+911234500077", email="memo@example.com", name="Asha"
    )
    attempt = ra_repo.create_recovery_attempt(
        connected_merchant_id, unique_checkout_id, customer["customer_id"]
    )
    ra_repo.update_state(
        attempt["recovery_attempt_id"], "CALLING",
        plan=json.dumps({"opening_line": "Hi Asha! I wanted to help you finish your order."}),
    )
    voice_runtime.CALL_SESSIONS.clear()
    return {
        "merchant_id": connected_merchant_id,
        "checkout_id": unique_checkout_id,
        "customer_id": customer["customer_id"],
        "recovery_attempt_id": attempt["recovery_attempt_id"],
    }


def _agent_saying(text):
    async def _run(**kwargs):
        _run.last_kwargs = kwargs
        return AgentResult(ok=True, output={"content": text})

    return _run


class TestTheTranscriptIsWrittenDown:
    async def test_both_sides_of_every_exchange_are_persisted(
        self, client, call_case, fake_tts, monkeypatch
    ):
        monkeypatch.setattr(
            voice_runtime.agent_runtime, "run_agent", _agent_saying("Of course, I'll send that over.")
        )

        await client.post(
            f"/voice/outbound?recovery_attempt_id={call_case['recovery_attempt_id']}",
            data={"CallSid": "CAmem1"},
        )
        await client.post(
            "/voice/respond",
            data={"CallSid": "CAmem1", "SpeechResult": "can you send me the link", "Confidence": "0.91"},
        )

        # Transcript writes are fire-and-forget so they cannot spend the
        # call's Twilio budget, which means they land shortly AFTER the
        # response rather than before it. Eventually-persisted is the real
        # contract now, so the test waits for it instead of assuming the
        # write finished inside the request.
        await wait_until(
            lambda: len(turns_repo.list_for_attempt(call_case["recovery_attempt_id"])) >= 3,
            timeout=8.0,
        )
        turns = turns_repo.list_for_attempt(call_case["recovery_attempt_id"])
        speakers = [t["speaker"] for t in turns]
        texts = [t["text"] for t in turns]

        assert "Hi Asha! I wanted to help you finish your order." in texts  # the opening
        assert "can you send me the link" in texts
        assert "Of course, I'll send that over." in texts
        assert turns_repo.CUSTOMER in speakers and turns_repo.AGENT in speakers
        # In order, and each index used once.
        assert [t["turn_index"] for t in turns] == sorted(t["turn_index"] for t in turns)

    async def test_how_well_we_heard_each_turn_is_recorded_with_it(
        self, client, call_case, fake_tts, monkeypatch
    ):
        """"We may have misheard this" is a fact about the turn, and it is
        exactly the fact a merchant needs when a call reads oddly."""
        monkeypatch.setattr(voice_runtime.agent_runtime, "run_agent", _agent_saying("Understood."))

        await client.post(
            f"/voice/outbound?recovery_attempt_id={call_case['recovery_attempt_id']}",
            data={"CallSid": "CAmem2"},
        )
        await client.post(
            "/voice/respond",
            data={"CallSid": "CAmem2", "SpeechResult": "it is a bit pricey", "Confidence": "0.64"},
        )

        await wait_until(
            lambda: any(t["text"] == 'it is a bit pricey' for t in
                        turns_repo.list_for_attempt(call_case["recovery_attempt_id"])),
            timeout=8.0,
        )
        turns = turns_repo.list_for_attempt(call_case["recovery_attempt_id"])
        spoken = next(t for t in turns if t["text"] == "it is a bit pricey")
        assert spoken["stt_confidence"] == pytest.approx(0.64)
        assert spoken["input_mode"] == "speech"

    async def test_a_keypad_press_is_recorded_as_a_keypad_press(
        self, client, call_case, fake_tts, monkeypatch
    ):
        monkeypatch.setattr(voice_runtime.agent_runtime, "run_agent", _agent_saying("Sure."))

        await client.post(
            f"/voice/outbound?recovery_attempt_id={call_case['recovery_attempt_id']}",
            data={"CallSid": "CAmem3"},
        )
        await client.post("/voice/respond", data={"CallSid": "CAmem3", "Digits": "1"})

        await wait_until(
            lambda: any(t["text"] == '[pressed 1]' for t in
                        turns_repo.list_for_attempt(call_case["recovery_attempt_id"])),
            timeout=8.0,
        )
        turns = turns_repo.list_for_attempt(call_case["recovery_attempt_id"])
        pressed = next(t for t in turns if t["text"] == "[pressed 1]")
        assert pressed["input_mode"] == "dtmf"
        assert pressed["stt_confidence"] is None


class TestSurvivingARestartMidCall:
    async def test_a_lost_session_is_rebuilt_instead_of_ending_the_call(
        self, client, call_case, fake_tts, monkeypatch
    ):
        """The scenario: the customer is mid-sentence and the process that
        answered their last turn is gone. Before this, the only possible
        reply was "Sorry, I lost track of our order details" and the call
        was over."""
        monkeypatch.setattr(voice_runtime.agent_runtime, "run_agent", _agent_saying("Right, I understand."))

        await client.post(
            f"/voice/outbound?recovery_attempt_id={call_case['recovery_attempt_id']}",
            data={"CallSid": "CAmem4"},
        )
        await client.post(
            "/voice/respond",
            data={"CallSid": "CAmem4", "SpeechResult": "the shipping time is too slow", "Confidence": "0.9"},
        )

        # Everything in this process's memory, gone.
        voice_runtime.CALL_SESSIONS.clear()
        agent_runtime.discard_thread("CAmem4")
        assert not agent_runtime.has_thread("CAmem4")

        resp = await client.post(
            "/voice/respond",
            data={"CallSid": "CAmem4", "SpeechResult": "so what can you do", "Confidence": "0.9"},
        )

        assert "<Gather" in resp.text, "the call must continue"
        assert "lost track" not in resp.text
        assert "CAmem4" in voice_runtime.CALL_SESSIONS

    async def test_the_rebuilt_thread_contains_what_was_already_said(
        self, client, call_case, fake_tts, monkeypatch
    ):
        monkeypatch.setattr(voice_runtime.agent_runtime, "run_agent", _agent_saying("I see."))

        await client.post(
            f"/voice/outbound?recovery_attempt_id={call_case['recovery_attempt_id']}",
            data={"CallSid": "CAmem5"},
        )
        await client.post(
            "/voice/respond",
            data={"CallSid": "CAmem5", "SpeechResult": "the shipping time is too slow", "Confidence": "0.9"},
        )

        voice_runtime.CALL_SESSIONS.clear()
        agent_runtime.discard_thread("CAmem5")

        await client.post(
            "/voice/respond", data={"CallSid": "CAmem5", "SpeechResult": "hello", "Confidence": "0.9"}
        )

        restored = agent_runtime._conversation_store["CAmem5"]
        contents = " ".join(m.get("content") or "" for m in restored)
        assert "shipping time is too slow" in contents
        assert restored[0]["role"] == "system"

    async def test_a_rebuilt_session_appends_rather_than_overwriting(
        self, client, call_case, fake_tts, monkeypatch
    ):
        """turn_index is seeded from the database, not from zero."""
        monkeypatch.setattr(voice_runtime.agent_runtime, "run_agent", _agent_saying("Right."))

        await client.post(
            f"/voice/outbound?recovery_attempt_id={call_case['recovery_attempt_id']}",
            data={"CallSid": "CAmem6"},
        )
        await client.post(
            "/voice/respond", data={"CallSid": "CAmem6", "SpeechResult": "one", "Confidence": "0.9"}
        )
        before = len(turns_repo.list_for_attempt(call_case["recovery_attempt_id"]))

        voice_runtime.CALL_SESSIONS.clear()
        agent_runtime.discard_thread("CAmem6")

        await client.post(
            "/voice/respond", data={"CallSid": "CAmem6", "SpeechResult": "two", "Confidence": "0.9"}
        )

        turns = turns_repo.list_for_attempt(call_case["recovery_attempt_id"])
        assert len(turns) > before
        indices = [t["turn_index"] for t in turns]
        assert len(indices) == len(set(indices)), "a rebuilt session must not reuse turn indices"

    async def test_an_unknown_call_sid_still_ends_honestly(self, client, fake_tts):
        """Rehydration must not turn "we genuinely do not know this call"
        into a fabricated conversation."""
        resp = await client.post(
            "/voice/respond", data={"CallSid": "CAnever_existed", "SpeechResult": "hello"}
        )
        assert "<Hangup" in resp.text
        assert "lost track" in resp.text


class TestWhatWeAlreadyKnowAboutThem:
    def test_a_first_contact_has_no_brief_at_all(self, call_case):
        """Empty is the correct answer, not a "no prior history" line that
        spends context saying nothing and invites the model to remark on
        it."""
        brief = customer_memory.build_brief(
            call_case["customer_id"], exclude_attempt_id=call_case["recovery_attempt_id"]
        )
        assert brief == ""

    def test_no_customer_id_is_not_an_error(self):
        assert customer_memory.build_brief(None) == ""

    async def test_the_barrier_they_stated_last_time_comes_back(
        self, client, call_case, fake_tts, monkeypatch
    ):
        """The whole point: "last week you said shipping was too slow" is a
        fundamentally better second call than asking the same opening
        question again."""
        monkeypatch.setattr(voice_runtime.agent_runtime, "run_agent", _agent_saying("I see."))

        await client.post(
            f"/voice/outbound?recovery_attempt_id={call_case['recovery_attempt_id']}",
            data={"CallSid": "CAmem7"},
        )
        await client.post(
            "/voice/respond",
            data={"CallSid": "CAmem7", "SpeechResult": "the shipping time is too slow", "Confidence": "0.9"},
        )

        # A SECOND attempt for the same customer, days later.
        second = ra_repo.create_recovery_attempt(
            call_case["merchant_id"], call_case["checkout_id"], call_case["customer_id"]
        )
        brief = customer_memory.build_brief(
            call_case["customer_id"], exclude_attempt_id=second["recovery_attempt_id"]
        )
        assert "shipping time is too slow" in brief

    async def test_a_turn_we_might_have_misheard_is_never_quoted_back(
        self, client, call_case, fake_tts, monkeypatch
    ):
        """Repeating a low-confidence transcription to someone a week later,
        with confidence, is how an agent tells a customer they said
        something they never said."""
        monkeypatch.setattr(voice_runtime.agent_runtime, "run_agent", _agent_saying("Sorry?"))

        await client.post(
            f"/voice/outbound?recovery_attempt_id={call_case['recovery_attempt_id']}",
            data={"CallSid": "CAmem8"},
        )
        await client.post(
            "/voice/respond",
            data={"CallSid": "CAmem8", "SpeechResult": "grumble something garbled", "Confidence": "0.35"},
        )

        second = ra_repo.create_recovery_attempt(
            call_case["merchant_id"], call_case["checkout_id"], call_case["customer_id"]
        )
        brief = customer_memory.build_brief(
            call_case["customer_id"], exclude_attempt_id=second["recovery_attempt_id"]
        )
        assert "garbled" not in brief

    def test_a_broken_promise_is_remembered(self, call_case):
        from app.db.database import get_db

        ra_repo.update_state(call_case["recovery_attempt_id"], "PROMISED")
        with get_db() as conn:
            conn.cursor().execute(
                "UPDATE recovery_attempts SET promised_at = NOW() - INTERVAL '2 days' "
                "WHERE recovery_attempt_id = %s",
                (call_case["recovery_attempt_id"],),
            )
        second = ra_repo.create_recovery_attempt(
            call_case["merchant_id"], call_case["checkout_id"], call_case["customer_id"]
        )
        brief = customer_memory.build_brief(
            call_case["customer_id"], exclude_attempt_id=second["recovery_attempt_id"]
        )
        assert "promised to pay" in brief.lower()

    def test_the_brief_is_hard_capped(self, call_case):
        """A live turn has a 9-second reasoning budget and a Twilio webhook
        deadline behind it. Memory that grows with history would eat the
        turn it was supposed to improve, and the failure would look like
        dropped calls rather than like a memory bug."""
        from app.db.database import get_db

        for i in range(12):
            turns_repo.record_turn(
                merchant_id=call_case["merchant_id"],
                recovery_attempt_id=call_case["recovery_attempt_id"],
                turn_index=100 + i,
                speaker=turns_repo.CUSTOMER,
                text="this is a very long complaint about absolutely everything " * 10,
                customer_id=call_case["customer_id"],
                stt_confidence=0.95,
            )
        ra_repo.update_state(call_case["recovery_attempt_id"], "PROMISED", approved_discount_percent=10.0)
        with get_db() as conn:
            conn.cursor().execute(
                "UPDATE recovery_attempts SET promised_at = NOW() - INTERVAL '2 days' "
                "WHERE recovery_attempt_id = %s",
                (call_case["recovery_attempt_id"],),
            )

        second = ra_repo.create_recovery_attempt(
            call_case["merchant_id"], call_case["checkout_id"], call_case["customer_id"]
        )
        brief = customer_memory.build_brief(
            call_case["customer_id"], exclude_attempt_id=second["recovery_attempt_id"]
        )
        assert 0 < len(brief) <= customer_memory.MAX_BRIEF_CHARS

    def test_the_brief_is_carried_in_the_plan_not_rebuilt_on_the_call(self, call_case):
        """It is pre-computed before dialling, like the opening voice block,
        because /voice/outbound answers inside Twilio's ~15s deadline and
        blowing that deadline does not degrade the call - it ends it."""
        ra_repo.update_state(
            call_case["recovery_attempt_id"], "CALLING",
            plan=json.dumps({
                "opening_line": "Hi Asha!",
                "memory_brief": "WHAT YOU ALREADY KNOW ABOUT THEM: they hate shipping delays",
            }),
        )
        session = voice_runtime._load_session_for_call(call_case["recovery_attempt_id"])
        assert "shipping delays" in session["memory_brief"]

    def test_a_call_with_no_brief_is_perfectly_normal(self, call_case):
        """First contact is the common case, and it must not depend on the
        plan carrying a key nobody set."""
        session = voice_runtime._load_session_for_call(call_case["recovery_attempt_id"])
        assert session["memory_brief"] == ""
        assert session["turn_index"] == 0

    def test_the_brief_reaches_the_prompt(self, call_case):
        prompt = voice_runtime._build_system_prompt(
            "Asha", "a table runner", "Loomwork", None, "WHAT YOU ALREADY KNOW ABOUT THEM: they hate shipping delays"
        )
        assert "they hate shipping delays" in prompt
        # And the rules it must not displace are still there.
        assert "NEVER volunteer a discount" in prompt


class TestTheDrawerCanActuallySeeIt:
    """A transcript nobody can read is only half the fix. The drawer calls
    itself "the screen the whole product is judged on" and showed every
    tool call with none of the conversation those calls were a response
    to."""

    async def test_the_recovery_detail_endpoint_returns_the_transcript(
        self, client, call_case, fake_tts, monkeypatch, real_merchant_id
    ):
        from app.core.auth import get_current_merchant

        monkeypatch.setattr(
            voice_runtime.agent_runtime, "run_agent", _agent_saying("I can send that right over.")
        )
        await client.post(
            f"/voice/outbound?recovery_attempt_id={call_case['recovery_attempt_id']}",
            data={"CallSid": "CAmem9"},
        )
        await client.post(
            "/voice/respond",
            data={"CallSid": "CAmem9", "SpeechResult": "please send the link", "Confidence": "0.93"},
        )

        # The drawer is behind session auth; this test is about the payload
        # shape, not the login flow, so the dependency is overridden the
        # way FastAPI intends rather than by forging a cookie.
        app.dependency_overrides[get_current_merchant] = lambda: {
            "merchant_id": call_case["merchant_id"]
        }
        try:
            resp = await client.get(
                f"/api/dashboard/recoveries/{call_case['recovery_attempt_id']}"
            )
        finally:
            app.dependency_overrides.pop(get_current_merchant, None)

        assert resp.status_code == 200
        body = resp.json()
        assert "transcript" in body, "the drawer cannot render a conversation it is never sent"
        texts = [t["text"] for t in body["transcript"]]
        assert "please send the link" in texts
        assert "I can send that right over." in texts
        # And the audit trail is still there - the two are meant to be read
        # against each other, not one instead of the other.
        assert "audit_trail" in body
