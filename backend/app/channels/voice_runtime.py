"""
The Conversation Agent - the live half of the Twilio Gather+Play voice call
("Turn-based, optimized" architecture; real-time Media Streams was
evaluated and explicitly not chosen - see the rebuild plan).

Day 6 rewrite. Previously this file:
  - Hardcoded every customer/product fact ("Dhruv", a ₹3,499 bamboo lamp)
    regardless of who was actually being called.
  - Baked an explicit 5-level discount ladder with real rupee amounts
    straight into the LLM's system prompt - the exact "LLM decides the
    money" anti-pattern the rest of this rebuild exists to close.
  - Detected "agreement" by scanning the model's own reply text for
    substrings like "10%" or "3,149", and detected customer agreement by
    scanning their speech for a fixed keyword list.
  - Fell back to the *standard* (non-Neural) `Polly.Aditi` voice reading
    the exact same "warm human" script whenever ElevenLabs failed or timed
    out - a noticeably robotic voice used as a silent fallback nobody
    would notice was different.

Update, after live testing on this deployment's network: ElevenLabs was
diagnosed to fail consistently during an active Twilio call window on this
specific machine (confirmed unrelated to IPv4/IPv6 - see app/services/tts.py).
Twilio's own Neural voice (a real, modern TTS engine, not the old robotic
standard voice above) is now the reliable primary via tts.py's
voice_block() - rendered entirely on Twilio's infrastructure, so this
class of failure is structurally impossible. ElevenLabs is still tried
first opportunistically; nothing here special-cases "degraded" anymore
for a voice-vendor hiccup specifically - voice_block() always returns
something.
  - Ran a completely separate, parallel LLM classification
    (customer_intelligence.py's `voice.turn_completed` -> `customer.
    understood` -> call_orchestrator._process_offer_request) to decide
    whether to actually issue a discount - a second reasoning path
    duplicating what this file's own LLM call already decided in English.

Now: one LLM turn per exchange, run through the Day 5 agent runtime
(app/agents/runtime.py) with real tools. "Agreement" is never inferred from
text - it's the model actually calling issue_offer, which can only ever act
on a server-computed offer_token (see app/agents/tools.py). "Opt-out" is
the model calling record_opt_out, which revokes real consent immediately.
The discount ladder lives entirely in merchant_policies, never in a prompt.
"""
import json
import logging
import uuid
from typing import Any, Dict, Optional
from xml.sax.saxutils import escape

from fastapi import APIRouter, Request
from fastapi.responses import Response

from app.core.config import settings
from app.gateway.event_bus import bus
from app.agents.state import AgentContext
from app.agents.tools import ALL_TOOLS
from app.agents import runtime as agent_runtime
from app.services.tts import voice_block as tts_voice_block, TWILIO_NEURAL_VOICE
from app.db.repositories import recovery_attempts as recovery_attempts_repo
from app.db.repositories import checkouts as checkouts_repo
from app.db.repositories import customers as customers_repo

logger = logging.getLogger(__name__)
voice_router = APIRouter()


# Confirmed live on Railway: get_cart and get_policy_limits each ran in
# ~750ms with real, fast tool latency - the earlier max_iterations=2 was
# too tight for a full negotiation (cart -> policy -> check_offer is 3
# sequential steps when the model doesn't batch them, which it often
# doesn't), cutting the turn off before it could ever reach check_offer.
# Twilio's own webhook-response deadline is ~15s; this leaves headroom
# for both the reasoning budget and voice_block()'s own ELEVENLABS_BUDGET_S
# (4s) afterward, with margin to spare.
VOICE_MAX_ITERATIONS = 4
VOICE_DEADLINE_S = 8.0

if not (settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN):
    logger.warning("TWILIO_ACCOUNT_SID/TWILIO_AUTH_TOKEN not set - voice calling is disabled until backend/.env is configured.")
    twilio_client = None
else:
    from twilio.rest import Client
    twilio_client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)

# call_id (Twilio CallSid) -> {ctx: AgentContext, opening_line: str, ...}.
# Each call gets its own entry and its own message thread in
# agent_runtime's conversation store (keyed by the same call_id), so
# concurrent calls never share state - this is what makes multiple
# simultaneous calls work correctly, not a shared/global session.
CALL_SESSIONS: Dict[str, Dict[str, Any]] = {}

SYSTEM_PROMPT_TEMPLATE = """You are a warm, professional shopping concierge on a real phone call with \
{customer_label} about {item_description}, which they started buying but didn't finish.

Sound like a genuine, friendly person - natural phrasing, brief warm acknowledgements, no corporate script \
reading. Keep every reply to 1-2 short sentences (this is a live phone call, not an email).

You have tools to look up the real cart, the real discount policy, and to check and issue a discount. \
NEVER state a specific discount percent or price unless it came from check_offer's response - you do not \
know the merchant's real limits until you ask. If the customer hesitates on price, call get_policy_limits \
and check_offer before offering anything. Only call issue_offer after the customer has clearly agreed to a \
specific offer you already proposed via check_offer.

If the customer asks not to be contacted again, or clearly wants to end the conversation permanently, call \
record_opt_out immediately and end the call politely - do not keep negotiating.
"""


def _build_system_prompt(customer_name: str, item_description: str) -> str:
    customer_label = customer_name if customer_name else "a customer"
    return SYSTEM_PROMPT_TEMPLATE.format(customer_label=customer_label, item_description=item_description)


def _describe_cart(checkout: Dict[str, Any]) -> str:
    try:
        line_items = json.loads(checkout.get("line_items") or "[]")
    except (TypeError, ValueError):
        line_items = []
    names = [li.get("name") for li in line_items if isinstance(li, dict) and li.get("name")]
    return ", ".join(names[:3]) if names else "their order"


def _load_session_for_call(recovery_attempt_id: str) -> Optional[Dict[str, Any]]:
    attempt = recovery_attempts_repo.get_recovery_attempt(recovery_attempt_id)
    if not attempt:
        return None
    checkout = checkouts_repo.get_checkout(attempt["checkout_id"])
    customer = customers_repo.get_customer(attempt["customer_id"]) if attempt.get("customer_id") else None
    try:
        plan = json.loads(attempt.get("plan") or "{}")
    except (TypeError, ValueError):
        plan = {}

    ctx = AgentContext(
        merchant_id=attempt["merchant_id"],
        correlation_id=recovery_attempt_id,
        customer_id=attempt.get("customer_id"),
        checkout_id=attempt["checkout_id"],
        recovery_attempt_id=recovery_attempt_id,
    )
    return {
        "ctx": ctx,
        "opening_line": plan.get("opening_line") or "Hi there! I wanted to help you finish your order.",
        # Pre-generated by call_orchestrator.py before dialing, when
        # possible - see its docstring for why the greeting is generated
        # ahead of connect-time rather than lazily inside this webhook.
        "opening_voice_block": plan.get("voice_block") or "",
        "customer_name": (customer or {}).get("name", ""),
        "item_description": _describe_cart(checkout) if checkout else "their order",
        "turns": 0,
    }


def _gather_twiml(voice_block: str, retry_message: str = "Are you still there?") -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Response>\n'
        f'    <Gather input="speech" action="{settings.NGROK_URL}/voice/respond" method="POST" speechTimeout="auto" timeout="5" language="en-IN">\n'
        f'        {voice_block}\n'
        '    </Gather>\n'
        f'    <Gather input="speech" action="{settings.NGROK_URL}/voice/respond" method="POST" speechTimeout="auto" timeout="5" language="en-IN">\n'
        f'        <Say voice="{TWILIO_NEURAL_VOICE}">{escape(retry_message)}</Say>\n'
        '    </Gather>\n'
        '</Response>'
    )


def _escalation_twiml(message: str) -> str:
    """Ends the call after a brief spoken message - used for genuine data
    problems (no recovery_attempt found, lost session) and the opt-out
    goodbye, not for a voice-vendor hiccup anymore (see tts.py's
    voice_block(), which always returns something playable)."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Response>\n'
        f'    <Say voice="{TWILIO_NEURAL_VOICE}">{escape(message)}</Say>\n'
        '    <Hangup/>\n'
        '</Response>'
    )


async def _publish_escalation(call_id: str, ctx: AgentContext, reason: str):
    await bus.publish(
        event_type="voice.escalation_needed",
        payload={"call_id": call_id, "recovery_attempt_id": ctx.recovery_attempt_id, "reason": reason},
        correlation_id=ctx.correlation_id,
        merchant_id=ctx.merchant_id,
    )


@voice_router.api_route("/voice/outbound", methods=["GET", "POST"])
async def twilio_outbound_twiml(request: Request):
    """Twilio hits this when an outbound call connects. recovery_attempt_id
    identifies which real recovery this call is for - see
    app/services/voice_dispatch.py, which is the only thing that places
    the call in the first place."""
    # Twilio sends CallSid as POST form data, not a query param - only
    # recovery_attempt_id (which voice_dispatch.py put there itself) is in
    # the query string. Reading CallSid from query_params here (as this
    # code once did) silently generates a fake, unmatchable call_id, so
    # /voice/respond's real Twilio-supplied CallSid can never find the
    # session this route just created - every real call would panic-reset
    # ("no session") or, in the *old* pre-rebuild code, silently fabricate
    # a brand new session with hardcoded defaults instead of erroring.
    if request.method == "POST":
        form_data = await request.form()
        call_id = form_data.get("CallSid", f"call_{uuid.uuid4().hex[:6]}")
    else:
        call_id = request.query_params.get("CallSid", f"call_{uuid.uuid4().hex[:6]}")
    recovery_attempt_id = request.query_params.get("recovery_attempt_id", "")
    logger.info(f"[CALL {call_id}] /voice/outbound HIT - recovery_attempt_id={recovery_attempt_id!r}, method={request.method}")

    session = _load_session_for_call(recovery_attempt_id) if recovery_attempt_id else None
    if not session:
        logger.error(f"voice/outbound: no recovery_attempt found for id={recovery_attempt_id!r}, ending call.")
        return Response(
            content=_escalation_twiml("Sorry, I'm having trouble pulling up your order right now. We'll follow up by email."),
            media_type="text/xml",
        )

    CALL_SESSIONS[call_id] = session
    # Prefer the pre-generated block (see call_orchestrator.py) - only
    # synthesize live here if pre-generation wasn't done. voice_block()
    # always returns something playable (ElevenLabs, or Twilio's own
    # Neural voice) - see tts.py's module docstring.
    block = session["opening_voice_block"] or await tts_voice_block(session["opening_line"])

    return Response(content=_gather_twiml(block), media_type="text/xml")


@voice_router.api_route("/voice/respond", methods=["GET", "POST"])
async def twilio_voice_respond(request: Request):
    """Twilio posts live speech transcriptions here - one LLM turn per
    exchange, through the Day 5 agent runtime, with real tools."""
    if request.method == "POST":
        form_data = await request.form()
        customer_speech = form_data.get("SpeechResult", "")
        call_id = form_data.get("CallSid", "unknown")
    else:
        customer_speech = request.query_params.get("SpeechResult", "")
        call_id = request.query_params.get("CallSid", "unknown")

    session = CALL_SESSIONS.get(call_id)
    if not session:
        logger.error(f"voice/respond: no session for call_id={call_id!r} (outbound webhook never ran?).")
        return Response(content=_escalation_twiml("Sorry, I lost track of our order details. We'll follow up by email."), media_type="text/xml")

    session["turns"] += 1
    logger.info(f"[CALL {call_id}] Customer: '{customer_speech}'")

    result = await agent_runtime.run_agent(
        system_prompt=_build_system_prompt(session["customer_name"], session["item_description"]),
        user_message=customer_speech,
        ctx=session["ctx"],
        tools=ALL_TOOLS,
        thread_id=call_id,
        max_iterations=VOICE_MAX_ITERATIONS,
        deadline_s=VOICE_DEADLINE_S,
    )

    if not result.ok or result.degraded or not (result.output or {}).get("content"):
        # Never fabricate a scripted line here - a short, honest, generic
        # reply is what "degraded" means for a live call.
        reply_text = "I hear you - let me have someone from our team follow up with you by email on this."
        await _publish_escalation(call_id, session["ctx"], result.error or "agent_degraded_or_empty_reply")
    else:
        reply_text = result.output["content"]

    logger.info(f"[CALL {call_id}] Agent ({'degraded' if result.degraded else 'ok'}): '{reply_text}' | tools: {result.tool_calls_made}")

    if "record_opt_out" in result.tool_calls_made:
        agent_runtime.discard_thread(call_id)
        return Response(content=_escalation_twiml(reply_text), media_type="text/xml")

    block = await tts_voice_block(reply_text)
    return Response(content=_gather_twiml(block), media_type="text/xml")


@voice_router.get("/api/call-sessions")
async def get_call_sessions():
    """Read-only debug view of in-memory call state - not persisted, not
    an API contract, just an inspection aid."""
    return {
        call_id: {
            "customer_name": s["customer_name"],
            "item_description": s["item_description"],
            "turns": s["turns"],
            "recovery_attempt_id": s["ctx"].recovery_attempt_id,
        }
        for call_id, s in CALL_SESSIONS.items()
    }
