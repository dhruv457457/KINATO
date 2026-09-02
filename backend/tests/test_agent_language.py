"""One setting, two prompts - and only one of them was listening.

Reported from a live call with AGENT_LANGUAGE already set to hinglish:

    Agent: "Hi Dhruv, I am calling from Loomwork about your order that you
            left in your cart."

Pure English, every time, on the very first sentence.

The cause is that a call is written by TWO different prompts. The turns come
from voice_runtime's SYSTEM_PROMPT_TEMPLATE, which appends _HINGLISH_STYLE
when the setting says so. The OPENING line comes from discovery_agent, a
separate model call made before the phone even rings - and that prompt had
never heard of the setting.

So the language switch worked on everything except the one sentence a
customer hears first, which is the sentence that sets the register for the
whole call. A caller who opens in English and drifts into Hinglish sounds
like two people.

The setting is read at call time rather than baked in, because it is a
config value a merchant can change between calls and there is no reason for
a restart to be part of that.
"""
import pytest

from app.core.config import settings


@pytest.fixture
def hinglish(monkeypatch):
    monkeypatch.setattr(settings, "AGENT_LANGUAGE", "hinglish")


@pytest.fixture
def english(monkeypatch):
    monkeypatch.setattr(settings, "AGENT_LANGUAGE", "english")


class TestTheOpeningLineObeysTheSetting:
    def test_the_opening_prompt_asks_for_hinglish(self, hinglish):
        from app.services.discovery_agent import _opening_line_prompt

        prompt = _opening_line_prompt("Dhruv", "a cotton kurta", 4290.0, "Loomwork")
        assert "hinglish" in prompt.lower()

    def test_english_leaves_the_prompt_alone(self, english):
        from app.services.discovery_agent import _opening_line_prompt

        prompt = _opening_line_prompt("Dhruv", "a cotton kurta", 4290.0, "Loomwork")
        assert "hinglish" not in prompt.lower()

    def test_the_two_prompts_agree(self, hinglish):
        """The whole bug in one assertion: the sentence the customer hears
        first and the sentences that follow must be written in the same
        language."""
        from app.channels.voice_runtime import _build_system_prompt
        from app.services.discovery_agent import _opening_line_prompt

        turns = _build_system_prompt("Dhruv", "a cotton kurta", "Loomwork")
        opening = _opening_line_prompt("Dhruv", "a cotton kurta", 4290.0, "Loomwork")
        assert ("hinglish" in turns.lower()) == ("hinglish" in opening.lower())

    def test_switching_needs_no_restart(self, monkeypatch):
        """Read at call time, not at import. A merchant changing this
        should not have to redeploy to hear it."""
        from app.services.discovery_agent import _opening_line_prompt

        monkeypatch.setattr(settings, "AGENT_LANGUAGE", "english")
        assert "hinglish" not in _opening_line_prompt("D", "x", 1.0, "L").lower()
        monkeypatch.setattr(settings, "AGENT_LANGUAGE", "hinglish")
        assert "hinglish" in _opening_line_prompt("D", "x", 1.0, "L").lower()


class TestWhatHinglishMustNotChange:
    def test_numbers_stay_in_english(self, hinglish):
        """A rupee amount is the one thing on this call that must not be
        creatively translated. It is also written in Roman script, because
        the line is read aloud by an Indian-English voice and Devanagari
        comes out as nonsense."""
        from app.services.discovery_agent import _opening_line_prompt

        prompt = _opening_line_prompt("Dhruv", "a cotton kurta", 4290.0, "Loomwork").lower()
        assert "roman" in prompt or "latin" in prompt
        assert "number" in prompt or "amount" in prompt

    def test_the_language_rule_never_replaces_the_facts(self, hinglish):
        """Hinglish is a note about register bolted onto the same prompt -
        it must not displace the instructions that stop the model inventing
        a product, a name, or a price."""
        from app.services.discovery_agent import _opening_line_prompt

        prompt = _opening_line_prompt("Dhruv", "a cotton kurta", 4290.0, "Loomwork")
        assert "Do not invent product details" in prompt
        assert "do not guess a name" in prompt or "Dhruv" in prompt
        assert "SPOKEN ALOUD" in prompt

    def test_the_degraded_opening_line_is_unaffected(self, hinglish):
        """When the model is unavailable the opening line is a fixed
        English sentence, and it stays that way. A language preference must
        never be able to cost us the opening line itself."""
        import inspect

        from app.services import discovery_agent

        src = inspect.getsource(discovery_agent.RecoveryStrategist)
        assert "Hello! Can you hear me alright?" in src
