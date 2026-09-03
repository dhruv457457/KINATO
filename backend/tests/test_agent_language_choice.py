"""A merchant chooses the language, not the deployment.

`AGENT_LANGUAGE` was an environment variable - one setting for every
merchant on the platform, changed only by redeploying. Language is not a
property of a deployment. It belongs to the merchant whose customers are
being phoned, and to the row they can edit.

It was also incomplete in a way that showed up on a live call: the setting
was read by the turn-by-turn prompt and not by the one that writes the
OPENING line, so a merchant who chose Hinglish heard an English greeting
followed by Hinglish answers. An agent that changes language after its first
sentence sounds like two people.

**A language is three settings.** What it writes, what voice reads it, and
what it listens for. Changing one without the others is the real failure:
Devanagari through an English voice is noise, and `<Gather>` takes exactly
one language, so a mismatch there feeds the confidence gate that blocks the
money tools - it costs recoveries, not just transcript quality.
"""
import pytest

from app.services import agent_language as al


class TestEveryLanguageIsInternallyConsistent:
    @pytest.mark.parametrize("code", sorted(al.LANGUAGES))
    def test_voice_and_recognition_agree_with_the_script(self, code):
        """The three settings have to describe the same language, or the
        agent writes one thing, says another and hears a third."""
        lang = al.LANGUAGES[code]
        if code == "hindi":
            assert "hi-IN" in lang.voice and lang.gather == "hi-IN"
        elif code == "telugu":
            assert "te-IN" in lang.voice and lang.gather == "te-IN"
        else:
            # English and Hinglish both ride the Indian-English voice and
            # listen in en-IN - that is the point of Hinglish.
            assert "en-IN" in lang.voice and lang.gather == "en-IN"

    @pytest.mark.parametrize("code", sorted(al.LANGUAGES))
    def test_every_language_can_explain_itself(self, code):
        lang = al.LANGUAGES[code]
        assert lang.label and lang.note
        assert lang.code == code

    def test_english_adds_no_instruction(self):
        """Telling a prompt written in English to speak English is noise the
        model re-reads on every turn of a five-second call."""
        assert al.LANGUAGES["english"].instruction == ""

    @pytest.mark.parametrize("code", ["hinglish", "hindi", "telugu"])
    def test_every_other_language_does_add_one(self, code):
        assert al.LANGUAGES[code].instruction.strip()

    def test_hinglish_asks_for_roman_letters(self):
        """Devanagari handed to the Indian-English voice comes out as
        nonsense, and a mispronounced rupee amount is the worst error this
        call can make."""
        text = al.LANGUAGES["hinglish"].instruction.lower()
        assert "roman" in text
        assert "devanagari" in text  # named, so it is explicitly excluded

    @pytest.mark.parametrize("code", ["hindi", "telugu"])
    def test_scripted_languages_keep_numbers_in_latin_digits(self, code):
        assert "latin digits" in al.LANGUAGES[code].instruction.lower()


class TestResolvingNeverBreaksACall:
    @pytest.mark.parametrize(
        "value", [None, "", "   ", "klingon", "EN", "Hinglish!", 0, "hindi;drop table"]
    )
    def test_anything_unusable_falls_back_to_english(self, value):
        """A language that failed to load must not take the call down. An
        agent speaking the wrong language still recovers carts; one that
        raises on the opening webhook recovers none."""
        assert al.resolve(value).code == "english"

    @pytest.mark.parametrize("code", sorted(al.LANGUAGES))
    def test_a_known_code_resolves_to_itself(self, code):
        assert al.resolve(code).code == code

    def test_case_and_padding_do_not_matter(self):
        assert al.resolve("  HINGLISH ").code == "hinglish"
        assert al.resolve("Hindi").code == "hindi"


class TestTheDashboardCannotDriftFromTheBackend:
    def test_choices_lists_exactly_what_is_supported(self):
        codes = {c["code"] for c in al.choices()}
        assert codes == set(al.LANGUAGES)

    def test_every_choice_carries_a_label_and_a_note(self):
        for choice in al.choices():
            assert choice["label"] and choice["note"]


class TestTheChoiceActuallyReachesTheCall:
    """The test that was missing, and the reason it was missing matters.

    The module above was tested in isolation and passed - resolve() worked,
    every language was internally consistent, choices() matched. None of that
    asserted the instruction ever reached a PROMPT, so an edit to
    _build_system_prompt that silently failed to apply left the suite green
    while the agent went on reading a deployment-wide environment variable
    and ignoring the merchant entirely.

    Verified by import, which proves only that a file still parses.
    """

    @pytest.mark.parametrize("code", ["hinglish", "hindi", "telugu"])
    def test_each_language_changes_what_the_turn_prompt_says(self, code):
        from app.channels.voice_runtime import _build_system_prompt

        prompt = _build_system_prompt(
            "Priya", "a cotton kurta", "Loomwork", voice_language=code
        )
        lang = al.LANGUAGES[code]
        if code == "hinglish":
            assert "HINGLISH" in prompt.upper()
        else:
            # The first few words are enough, and less brittle than the
            # whole sentence.
            assert lang.instruction.split(".")[0] in prompt

    def test_english_leaves_the_prompt_alone(self):
        from app.channels.voice_runtime import _build_system_prompt

        plain = _build_system_prompt("Priya", "a cotton kurta", "Loomwork")
        english = _build_system_prompt(
            "Priya", "a cotton kurta", "Loomwork", voice_language="english"
        )
        assert plain == english

    def test_the_merchants_choice_beats_the_deployment_default(self, monkeypatch):
        """The whole point of moving this out of an environment variable.
        A merchant on hindi must not be overridden by a global set to
        hinglish for somebody else."""
        from app.core.config import settings
        from app.channels.voice_runtime import _build_system_prompt

        monkeypatch.setattr(settings, "AGENT_LANGUAGE", "hinglish")
        prompt = _build_system_prompt(
            "Priya", "a cotton kurta", "Loomwork", voice_language="hindi"
        )
        assert "Devanagari" in prompt
        assert "HINGLISH" not in prompt.upper()

    def test_the_global_still_applies_when_the_merchant_has_not_chosen(self, monkeypatch):
        from app.core.config import settings
        from app.channels.voice_runtime import _build_system_prompt

        monkeypatch.setattr(settings, "AGENT_LANGUAGE", "hinglish")
        prompt = _build_system_prompt("Priya", "a cotton kurta", "Loomwork", voice_language="")
        assert "HINGLISH" in prompt.upper()

    @pytest.mark.parametrize("code", ["hinglish", "hindi", "telugu"])
    def test_the_opening_line_prompt_agrees_with_the_turn_prompt(self, code):
        """These are two different prompts and they were read from two
        different settings, which is how an agent came to greet in English
        and answer in Hinglish. They must not diverge again."""
        from app.services.discovery_agent import _opening_line_prompt

        opening = _opening_line_prompt("Priya", "a cotton kurta", 2490.0, "Loomwork", code)
        assert al.LANGUAGES[code].instruction.split(".")[0] in opening
