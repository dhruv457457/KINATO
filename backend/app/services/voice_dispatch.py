"""
The one place that actually dials Twilio. Extracted out of
app/channels/voice_runtime.py so call_orchestrator.py (the real recovery
path) and any manual test utility share the exact same call-placing code -
previously voice_runtime.py's /voice/call-me was the *only* thing that ever
actually called Twilio, and it was wired to nothing but a manual button, not
the real checkout.abandoned/payment.failed pipeline.

Uses `settings` (backend/.env via app/core/config.py), not raw os.getenv -
consistent with the rest of the app post-Day-2, and means a missing
credential is a clear Pydantic-settings-backed default, not a scattered
`os.getenv(..., "")` that silently produces an empty string three files away
from where it's used.
"""
import logging
from urllib.parse import urlencode

from app.core.config import settings

logger = logging.getLogger(__name__)


class VoiceDispatchError(Exception):
    """Raised when a call can't be placed - missing Twilio config, no phone
    number, or the Twilio API call itself failing. There is no silent
    no-op path that looks like a call happened when it didn't."""


def _client():
    if not (settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN):
        raise VoiceDispatchError(
            "Twilio is not configured (TWILIO_ACCOUNT_SID/TWILIO_AUTH_TOKEN missing in backend/.env)."
        )
    from twilio.rest import Client

    return Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)


def place_outbound_call(to_phone: str, recovery_attempt_id: str, record: bool = False) -> str:
    """Places a real outbound call and points Twilio's webhook at
    /voice/outbound?recovery_attempt_id=... - voice_runtime.py loads the
    real recovery_attempt/checkout/customer/plan from that id rather than
    trusting anything passed through the URL beyond the id itself.

    Returns the Twilio Call SID. Raises VoiceDispatchError on any failure -
    callers (call_orchestrator) must treat that as the recovery attempt
    failing to reach the customer, not as a successful dial.
    """
    if not to_phone:
        raise VoiceDispatchError("No phone number on file for this customer.")
    if not settings.TWILIO_PHONE_NUMBER:
        raise VoiceDispatchError("TWILIO_PHONE_NUMBER is not configured.")
    if not settings.NGROK_URL:
        raise VoiceDispatchError("NGROK_URL is not configured - Twilio has no public webhook to call back to.")

    client = _client()
    query = urlencode({"recovery_attempt_id": recovery_attempt_id})
    webhook_url = f"{settings.NGROK_URL}/voice/outbound?{query}"

    # Trial Twilio accounts reject the `record` parameter outright - even
    # explicitly set to False - as a "disallowed parameter" (confirmed
    # live). Only include it in the request at all when actually True, so
    # a trial account's default (unrecorded) calls keep working.
    create_kwargs = {"to": to_phone, "from_": settings.TWILIO_PHONE_NUMBER, "url": webhook_url}
    if record:
        create_kwargs["record"] = True

    try:
        call = client.calls.create(**create_kwargs)
    except Exception as e:
        raise VoiceDispatchError(f"Twilio call creation failed: {e}") from e

    logger.info(f"Outbound call placed: SID={call.sid} recovery_attempt_id={recovery_attempt_id} record={record}")
    return call.sid


def get_recording_urls(call_sid: str) -> list:
    """Fetches recording URLs for a call after it ends - Twilio needs a few
    seconds post-hangup to finish processing. Returns [] (never raises) if
    Twilio isn't configured or the call had no recording."""
    if not (settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN):
        return []
    try:
        client = _client()
        recordings = client.recordings.list(call_sid=call_sid)
        return [
            f"https://api.twilio.com{r.uri.replace('.json', '')}.mp3"
            for r in recordings
        ]
    except Exception as e:
        logger.warning(f"Could not fetch recordings for call {call_sid}: {e}")
        return []
