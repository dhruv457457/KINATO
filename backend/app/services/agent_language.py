"""What language the agent speaks, and the three things that has to change.

`AGENT_LANGUAGE` was an environment variable, which makes it a property of
the *deployment* - one setting for every merchant on the platform, changed
only by a redeploy. Language is not a property of a deployment. It belongs
to the merchant whose customers are being phoned, and it belongs in their
policy row where they can change it and hear the difference on the next
call.

**A language is three settings, not one.** Getting this wrong is why a
half-configured agent sounds broken rather than foreign:

  1. What it WRITES - the instruction in both prompts.
  2. What VOICE reads it - a Devanagari sentence handed to an English voice
     comes out as noise, and an English sentence handed to a Hindi voice is
     little better.
  3. What it LISTENS for - Twilio's `<Gather>` takes exactly one BCP-47
     language and there is no bilingual recogniser. This is the one with
     teeth: a mis-transcription feeds the confidence gate that blocks the
     money tools, so choosing this badly costs recoveries, not just
     transcript quality.

Changing one without the others is the actual failure mode. An agent told
to speak Hindi, rendered by an English voice, listening in `en-IN`, is worse
than one that simply spoke English.

**Hinglish is deliberately not "Hindi".** It is written in Roman letters
and keeps numbers in English, because the line is read by an Indian-English
voice: Devanagari through that voice is nonsense, and a mispronounced rupee
amount is the worst error this call can make. It listens in `en-IN`, which
transcribes code-switched speech far better than `hi-IN` transcribes
English.
"""
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass(frozen=True)
class Language:
    code: str
    label: str
    # The sentence added to both prompts. Empty for English, which is what
    # the prompts already assume - an instruction saying "speak English" to
    # a prompt written in English is noise the model has to read on every
    # turn of a five-second call.
    instruction: str
    # Twilio <Say> voice. Must be able to pronounce `instruction`'s script.
    voice: str
    # Twilio <Gather> recognition language. One only; there is no bilingual
    # mode.
    gather: str
    # Said plainly in the UI, because a merchant choosing a language is
    # choosing what their customers hear AND what the agent can understand.
    note: str


_HINGLISH = (
    "Speak natural Hinglish - the way people actually talk in Indian cities: Hindi and English "
    "mixed in the same sentence. Write it in ROMAN letters, never Devanagari, and keep every "
    "number, price and date in English. Warm and conversational, not formal Hindi. "
)

_HINDI = (
    "Speak Hindi, written in Devanagari. Keep every number, price and date in Latin digits so "
    "they are read correctly. Warm and respectful, using aap. "
)

_TELUGU = (
    "Speak Telugu, written in Telugu script. Keep every number, price and date in Latin digits so "
    "they are read correctly. Warm and respectful. "
)

LANGUAGES: Dict[str, Language] = {
    "english": Language(
        code="english",
        label="English",
        instruction="",
        voice="Google.en-IN-Neural2-B",
        gather="en-IN",
        note="Indian English. The most reliable option: speech recognition is strongest here.",
    ),
    "hinglish": Language(
        code="hinglish",
        label="Hinglish (Hindi + English)",
        instruction=_HINGLISH,
        voice="Google.en-IN-Neural2-B",
        gather="en-IN",
        note=(
            "Hindi and English mixed, in Roman letters, spoken by an Indian-English voice. "
            "Listens in English, which handles mixed speech better than Hindi does."
        ),
    ),
    "hindi": Language(
        code="hindi",
        label="Hindi",
        instruction=_HINDI,
        voice="Google.hi-IN-Neural2-B",
        gather="hi-IN",
        note=(
            "Full Hindi, with a Hindi voice, listening in Hindi. Customers who answer in "
            "English will be transcribed less accurately - pick Hinglish if they mix."
        ),
    ),
    "telugu": Language(
        code="telugu",
        label="Telugu",
        instruction=_TELUGU,
        voice="Google.te-IN-Standard-B",
        gather="te-IN",
        note=(
            "Telugu voice and Telugu recognition. Telugu has no Neural2 voice on Twilio yet, "
            "so it is a Standard voice and sounds flatter than the others."
        ),
    ),
}

DEFAULT = "english"


def resolve(value: Optional[str]) -> Language:
    """Never raises. An unknown or empty value falls back to English.

    A language that failed to load must not take the call down with it -
    an agent speaking the wrong language still recovers carts, and one that
    throws on the opening webhook recovers none.
    """
    key = (value or "").strip().lower()
    return LANGUAGES.get(key, LANGUAGES[DEFAULT])


def choices() -> list:
    """For the dashboard's selector, so the UI cannot drift from what the
    backend actually supports."""
    return [
        {"code": lang.code, "label": lang.label, "note": lang.note}
        for lang in LANGUAGES.values()
    ]
