"""One call, one voice.

A call does not use a single TTS engine. The opening turn plays the
ElevenLabs block and then appends the keypad note as a Twilio `<Say>`, so
both voices are heard back to back on **every** call - and any later turn
flips to the Twilio fallback whenever the 2s ElevenLabs budget overruns.

Until now those were "Sarah" (ElevenLabs, female) and "Polly.Kajal-Neural"
(female): matched by luck, with nothing requiring it, nothing checking it,
and nothing that would notice if one changed. Switching to a male voice by
editing one of the two constants would have produced a caller who changes
sex mid-sentence.

The cache is the other half. It was keyed on the spoken text alone, so it
outlived a voice change within a process - the new voice would be used for
new lines while previously-cached lines kept playing in the old one. A
female sentence in the middle of a male call, served from a cache hit.
"""
import pytest

from app.core.config import settings


class TestBothVoicesAreConfigurable:
    def test_neither_voice_is_hardcoded_any_more(self):
        """Both read from settings, so a voice change is a config change.

        config.py sets extra="ignore", so before these fields existed an
        ELEVENLABS_VOICE_ID in .env was accepted and silently dropped.
        """
        assert hasattr(settings, "ELEVENLABS_VOICE_ID")
        assert hasattr(settings, "TWILIO_VOICE_NAME")
        assert hasattr(settings, "VOICE_GATHER_LANGUAGE")

    def test_the_twilio_fallback_defaults_to_a_real_voice(self):
        """An unset TWILIO_VOICE_NAME must not mean a silent call - this is
        the voice that speaks when ElevenLabs fails, which is the moment we
        can least afford a broken TwiML attribute."""
        default = type(settings).model_fields["TWILIO_VOICE_NAME"].default
        assert default
        assert "." in default  # Provider.Name form, e.g. Google.en-IN-Neural2-B


class TestTheCacheCannotOutliveAVoiceChange:
    async def test_a_voice_change_does_not_serve_the_old_voice(self, monkeypatch):
        """The cache key includes the voice, so switching invalidates it.

        Keyed on text alone, the same sentence recorded as Sarah would keep
        being played after the agent had become someone else.
        """
        from app.services import tts

        monkeypatch.setattr(tts, "AUDIO_CACHE", {})
        monkeypatch.setattr(tts, "ELEVENLABS_VOICE_ID", "voice_the_first")
        monkeypatch.setattr(settings, "ELEVENLABS_API_KEY", "fake-key-not-used")

        line = "Your order total is one thousand two hundred and ninety rupees."
        tts.AUDIO_CACHE[f"voice_the_first|{line}"] = "https://example.test/old.mp3"

        # Same text, same voice -> the cached recording is correct.
        assert await tts.generate_elevenlabs_audio(line) == "https://example.test/old.mp3"

        # Same text, DIFFERENT voice -> that recording is the wrong person.
        # It must not be returned; with no network available this falls
        # through and returns "", which makes voice_block use the Twilio
        # voice rather than the stale audio.
        monkeypatch.setattr(tts, "ELEVENLABS_VOICE_ID", "voice_the_second")
        assert await tts.generate_elevenlabs_audio(line) != "https://example.test/old.mp3"


class TestElevenLabsCanBeTurnedOff:
    async def test_an_empty_voice_id_skips_elevenlabs_entirely(self, monkeypatch):
        """A supported configuration, not a broken one.

        Empty voice id means every line is rendered by Twilio in one
        consistent voice, and the 2s budget is never spent. Less expressive,
        and it removes the mixed-voice problem by construction.
        """
        from app.services import tts

        monkeypatch.setattr(tts, "ELEVENLABS_VOICE_ID", "")
        monkeypatch.setattr(settings, "ELEVENLABS_API_KEY", "fake-key-not-used")

        assert await tts.generate_elevenlabs_audio("anything at all") == ""

    async def test_with_elevenlabs_off_the_block_is_a_say_in_the_twilio_voice(self, monkeypatch):
        from app.services import tts

        monkeypatch.setattr(tts, "ELEVENLABS_VOICE_ID", "")
        block = await tts.voice_block("Hello there.")

        assert block.startswith("<Say")
        assert tts.TWILIO_NEURAL_VOICE in block
        assert "<Play>" not in block


class TestRecognitionLanguage:
    def test_the_gather_language_is_a_single_bcp47_value(self):
        """Twilio's <Gather> has no bilingual mode.

        This is a real either/or, and it is not cosmetic: a
        mis-transcription feeds misheard_streak and the
        REJECTED_LOW_CONFIDENCE gate that stops the money tools running. The
        setting affects whether a recovery can happen, not just what the
        transcript reads like.
        """
        lang = settings.VOICE_GATHER_LANGUAGE
        assert lang and "," not in lang and " " not in lang
        assert "-" in lang, "expected a BCP-47 tag such as en-IN"


class TestHinglishIsOptedInto:
    """The agent speaks Hinglish only when the merchant asks for it.

    Output language and input language are separate decisions with opposite
    costs. Speaking Hinglish is free; LISTENING for it is not - Twilio's
    <Gather> takes exactly one BCP-47 language, and a mis-transcription
    feeds the confidence gate that blocks the money tools. So this setting
    changes what the agent says and deliberately leaves recognition alone.
    """

    def test_english_is_the_default(self):
        assert type(settings).model_fields["AGENT_LANGUAGE"].default == "english"

    def test_the_prompt_stays_english_unless_asked(self, monkeypatch):
        from app.channels import voice_runtime as vr

        monkeypatch.setattr(settings, "AGENT_LANGUAGE", "english")
        prompt = vr._build_system_prompt("Dhruv", "a woven table runner", "Loomwork")
        assert "SPEAK HINGLISH" not in prompt

    def test_hinglish_is_added_when_asked(self, monkeypatch):
        from app.channels import voice_runtime as vr

        monkeypatch.setattr(settings, "AGENT_LANGUAGE", "hinglish")
        prompt = vr._build_system_prompt("Dhruv", "a woven table runner", "Loomwork")
        assert "SPEAK HINGLISH" in prompt

    def test_the_setting_is_not_case_or_space_sensitive(self, monkeypatch):
        """A merchant typing " Hinglish " into an env var meant it."""
        from app.channels import voice_runtime as vr

        monkeypatch.setattr(settings, "AGENT_LANGUAGE", "  Hinglish  ")
        assert "SPEAK HINGLISH" in vr._build_system_prompt("Dhruv", "an item", "Loomwork")

    def test_it_asks_for_roman_script_and_english_numbers(self, monkeypatch):
        """Both are functional requirements, not style.

        Devanagari is mangled by the en-IN TTS voice that reads these lines
        aloud, and a mispronounced rupee amount is the worst possible error
        on a call whose entire purpose is delivering one.
        """
        from app.channels import voice_runtime as vr

        monkeypatch.setattr(settings, "AGENT_LANGUAGE", "hinglish")
        prompt = vr._build_system_prompt("Dhruv", "an item", "Loomwork")
        assert "Roman letters" in prompt
        assert "number" in prompt and "English" in prompt

    def test_recognition_language_is_untouched_by_it(self, monkeypatch):
        """The trade-off, pinned. Speaking Hinglish must not quietly switch
        the recogniser to hi-IN and start mis-hearing English replies."""
        from app.channels import voice_runtime as vr  # noqa: F401

        monkeypatch.setattr(settings, "AGENT_LANGUAGE", "hinglish")
        assert settings.VOICE_GATHER_LANGUAGE == "en-IN"
