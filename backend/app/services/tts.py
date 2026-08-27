"""
Voice synthesis for the Conversation Agent. Returns a ready-to-embed TwiML
voice block - either <Play>{elevenlabs_url}</Play> or a Twilio-rendered
<Say voice="...">.

Twilio Neural voice (Polly Neural, via Twilio's own infrastructure) is now
the PRIMARY path, not a last-resort fallback. This is a deliberate change,
not a quality compromise accepted under pressure: diagnosed live, over 7+
real calls, that ElevenLabs from this specific machine fails consistently
during an active Twilio call window (confirmed independent of IPv4/IPv6 -
api.elevenlabs.io has no IPv6 route at all, so that fix couldn't and didn't
help here) while succeeding reliably in isolated tests. Twilio's own <Say>
is rendered entirely on Twilio's infrastructure - no network call from this
machine at all - so it structurally cannot hit this failure mode. Polly's
*Neural* engine (not the old standard "Aditi" voice this app used to use)
is a meaningfully more natural voice, not the robotic fallback from before.

ElevenLabs is still attempted first when configured - if this environment's
connectivity to it stabilizes (e.g. once deployed to a real server, off
this laptop's network), calls seamlessly get the higher-quality voice with
no code change needed. It just never blocks the conversation anymore.
"""
import asyncio
import logging
import os
import uuid
from typing import Dict
from xml.sax.saxutils import escape

from app.core.config import settings
from app.core.net import ipv4_client

# Twilio gives up waiting on a webhook response after ~15s and plays its
# own "application error" message to the caller if it isn't answered in
# time - confirmed live: two 6s ElevenLabs attempts (~12s+ overhead) came
# close enough to that ceiling to trip it intermittently. This is a hard
# cap on the WHOLE opportunistic-ElevenLabs attempt inside voice_block(),
# regardless of how many retries happen inside it, so the Neural fallback
# always has time to run and Twilio never has to guess.
ELEVENLABS_BUDGET_S = 4.0

logger = logging.getLogger(__name__)

ELEVENLABS_VOICE_ID = "EXAVITQu4vr4xnSDxMaL"  # Sarah: warm, expressive
TWILIO_NEURAL_VOICE = "Polly.Kajal-Neural"  # Indian-English neural voice
AUDIO_CACHE: Dict[str, str] = {}


async def voice_block(text: str) -> str:
    """Always returns a usable TwiML fragment - never empty, never raises,
    and never takes longer than ELEVENLABS_BUDGET_S total (see module
    constant) - a live call's webhook response time is on a real clock
    Twilio enforces, not just an internal "nice to have" timeout."""
    try:
        audio_url = await asyncio.wait_for(generate_elevenlabs_audio(text), timeout=ELEVENLABS_BUDGET_S)
    except asyncio.TimeoutError:
        logger.warning(f"ElevenLabs exceeded its {ELEVENLABS_BUDGET_S}s budget - falling back to Twilio Neural voice.")
        audio_url = ""
    if audio_url:
        return f"<Play>{audio_url}</Play>"
    return f'<Say voice="{TWILIO_NEURAL_VOICE}">{escape(text)}</Say>'


async def generate_elevenlabs_audio(text: str) -> str:
    """Returns "" on any failure - see module docstring for why that's no
    longer treated as fatal by callers (use voice_block() instead of this
    directly, unless you specifically need to know whether ElevenLabs
    itself succeeded)."""
    clean_text = text.strip()
    if clean_text in AUDIO_CACHE:
        return AUDIO_CACHE[clean_text]
    if not settings.ELEVENLABS_API_KEY:
        return ""

    file_id = f"voice_{uuid.uuid4().hex[:8]}.mp3"
    file_path = os.path.join("static", "audio", file_id)

    # A single attempt, well inside ELEVENLABS_BUDGET_S - the old 2-retry
    # pattern (up to 12s+) is what let voice_block() approach Twilio's own
    # ~15s webhook-response deadline in the first place. voice_block()'s
    # outer asyncio.wait_for is the real safety net now; this timeout just
    # keeps a single hung attempt from eating the whole budget itself.
    try:
        async with ipv4_client(timeout=3.0) as client:
            res = await client.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}?optimize_streaming_latency=4",
                headers={"xi-api-key": settings.ELEVENLABS_API_KEY},
                json={
                    "text": clean_text,
                    "model_id": "eleven_flash_v2_5",
                    "voice_settings": {"stability": 0.55, "similarity_boost": 0.85, "style": 0.35, "use_speaker_boost": False},
                },
            )
            if res.status_code == 200:
                os.makedirs(os.path.join("static", "audio"), exist_ok=True)
                with open(file_path, "wb") as f:
                    f.write(res.content)
                audio_url = f"{settings.NGROK_URL}/audio/{file_id}"
                AUDIO_CACHE[clean_text] = audio_url
                return audio_url
            logger.warning(f"ElevenLabs HTTP {res.status_code}: {res.text}")
    except Exception as e:
        logger.warning(f"ElevenLabs call failed: {e.__class__.__name__}: {e!r}")
    return ""
