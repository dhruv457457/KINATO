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
import asyncio
import contextvars
import dataclasses
import json
import time
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from xml.sax.saxutils import escape

from fastapi import APIRouter, Request
from fastapi.responses import Response

from app.core.config import settings
from app.services import agent_language
from app.gateway.event_bus import bus
from app.agents.state import AgentContext
from app.agents.tools import ALL_TOOLS, record_opt_out, LOW_CONFIDENCE_FLOOR
from app.agents.audit import execute_tool
from app.agents import runtime as agent_runtime
from app.services.tts import voice_block as tts_voice_block, TWILIO_NEURAL_VOICE, elevenlabs_active
from app.db.repositories import conversation_turns as turns_repo
from app.db.repositories import recovery_attempts as recovery_attempts_repo
from app.db.repositories import checkouts as checkouts_repo
from app.db.repositories import customers as customers_repo
from app.db.repositories import merchants as merchants_repo
from app.services import failure_diagnosis
from app.services.discovery_agent import contains_placeholder
from app.services import outreach_guards
from app.db.database import run_db_async

logger = logging.getLogger(__name__)
voice_router = APIRouter()


# Confirmed live on Railway: get_cart and get_policy_limits each ran in
# ~750ms with real, fast tool latency - the earlier max_iterations=2 was
# too tight for a full negotiation (cart -> policy -> check_offer is 3
# sequential steps when the model doesn't batch them, which it often
# doesn't), cutting the turn off before it could ever reach check_offer.
# Twilio's own webhook-response deadline is ~15s, and blowing it DROPS THE
# CALL ("we can't reach your server"), which is exactly what happened on a
# real call after a few turns.
#
# The old 8s reasoning budget plus voice_block()'s then-4s ElevenLabs budget
# gave a 12s worst case, before network and DB time - too close to 15s once
# a turn did two LLM round trips around a tool that itself took 2.5s
# (check_offer, measured live).
#
# These are deliberately tight because the two failure modes are not
# equally bad: exceeding Twilio's deadline kills the call outright, while
# exhausting the reasoning budget just degrades that one turn and the
# conversation continues. Always lose the reasoning, never the call.
# Revised again after a live call: 6s proved too tight because each DB-backed
# tool costs ~2-2.8s from Railway to Supabase (see policies.py's cache note).
# A turn that called get_cart then get_policy_limits burned the whole budget
# and degraded at the exact moment the customer asked for the checkout link.
#
# The durable fix was removing the redundancy - check_offer already loads the
# cart and policy itself, so the tool descriptions and prompt now tell the
# model to call it directly - but the budget also needs to fit a real
# check_offer -> issue_offer sequence.
#
# That sequence is now two model round trips rather than three: issue_offer
# is a terminal tool, so the runtime no longer goes back to the model for a
# closing sentence the caller was always going to discard (see
# agents/tools.py's `terminal` field). And the budget is no longer a
# constant at all - see _remaining_reasoning_budget below.
VOICE_MAX_ITERATIONS = 4
# A floor and a ceiling, not the budget itself.
#
# This used to be a flat 7.5s, cut from 9.0 after a live call died on the
# check_offer turn. A fixed number is the wrong shape: it has to assume the
# worst about everything that already happened this turn (session
# rehydration on a restarted worker, the paid-guard read), and on a warm
# turn where none of that cost anything it throws the slack away. The turn
# that blew the budget was starved of reasoning time that was sitting
# unused.
#
# _respond now DERIVES the real deadline from the wall clock - see
# _remaining_reasoning_budget - and these two only stop the derived value
# from being absurd in either direction.
VOICE_DEADLINE_MIN_S = 1.5
VOICE_DEADLINE_MAX_S = 4.0

# --- Surviving speech-to-text ------------------------------------------
# Twilio posts a `Confidence` float (0.0-1.0) with every SpeechResult, and
# for the whole life of this project nothing read it. Accents and line
# noise degrade Gather's transcription badly, and - this is the dangerous
# part - they degrade it SILENTLY: it returns a fluent, confident-looking
# sentence that is simply not what the customer said. The agent then
# reasons perfectly about words nobody spoke.
#
# Two thresholds, because there are two different mistakes to avoid:
#
#   Below STT_TRUST_FLOOR the turn may still be answered - the agent can
#   acknowledge, confirm, close warmly - but NO money tool may run. That is
#   enforced in tools.py (see LOW_CONFIDENCE_FLOOR, which this mirrors),
#   not by asking the prompt to be careful.
#
#   Below STT_UNUSABLE_FLOOR we do not spend an LLM call at all. There is
#   nothing to reason about; the honest move is to say the line broke up
#   and ask them to repeat it. This also saves ~2-6s of the turn budget on
#   exactly the turns most likely to run out of it.
STT_TRUST_FLOOR = LOW_CONFIDENCE_FLOOR
STT_UNUSABLE_FLOOR = 0.3

# After this many consecutive unusable turns, stop asking them to repeat
# themselves. Being asked "sorry, could you say that again?" three times in
# a row is the point at which a recovery call becomes an irritation, and
# the customer is very likely on a bad line rather than being unclear. We
# close by offering the link instead, which needs no transcription at all.
MAX_MISHEARD_STREAK = 2

# What Twilio actually allows a webhook before it gives up and plays "we
# cannot reach your server" to the customer. Not configurable by us - it is
# their clock - which is exactly why every budget below has to fit inside
# it with room to spare.
TWILIO_WEBHOOK_DEADLINE_S = 15.0
# Log loudly past this. Two thirds of the deadline is the point where a
# turn is working but has stopped having any margin.
TURN_BUDGET_WARN_S = 3.5
# The whole turn, cut off here whatever it is doing. Comfortably inside
# Twilio's deadline so there is still time to render and return a spoken
# line after the cut - a timeout that fires at the deadline is worth
# nothing, because answering late is the same as not answering.
#
# MEASURED, not documented. Twilio's stated webhook budget is ~15s and this
# deployment's real one is about FIVE, and every constant in this file was
# tuned against the wrong number for the whole life of the project.
#
# Six live turns settle it:
#
#     1.2s  1.3s  1.5s  4.0s   call continued
#     5.2s  5.7s  7.2s          call KILLED
#
# and the arithmetic on the 5.7s one is exact: the request arrived at
# 17:17:31.4, Twilio gave up around 36.4, played "an application error has
# occurred" for ~3.5s, and the call record ends at 17:17:40. Our reply
# arrived at 17:17:37 - a fraction of a second after Twilio had already
# stopped listening, which is also why that turn has no 200 in the access
# log while every faster turn does.
#
# So the customer was never hanging up on dead air. We were missing a
# deadline four times tighter than the one being designed for, on the one
# turn that commits the sale.
#
# 4.5 leaves room to answer BEFORE Twilio's error rather than after it,
# which is the whole point of having our own timeout: the cut-off branch
# speaks a real line and the call continues. Until now that branch was
# unreachable - Twilio always got there first.
TURN_HARD_TIMEOUT_S = 4.5

# What a turn still has to do AFTER the agent stops reasoning: render one
# TTS line and return the TwiML. The reasoning budget is whatever is left
# once these are set aside, which is what makes the arithmetic below add up
# by construction rather than by three constants agreeing with each other
# by hand. tests/test_turn_budget.py asserts it.
TTS_BUDGET_S = 2.0
RESPONSE_RESERVE_S = 0.3

# When this turn started, per request. A ContextVar rather than a parameter
# because the turn's start is set in the outermost handler and needed
# several frames down; asyncio copies the context into the task that
# wait_for creates, so each concurrent call reads its own.
_turn_started: contextvars.ContextVar[float] = contextvars.ContextVar("kinato_turn_started", default=0.0)

# Which language to LISTEN in, for this request.
#
# A ContextVar for the same reason the turn clock is one: _gather_twiml is
# called from eight places, several of them error paths that have no session
# and no merchant to look one up from. Threading a parameter through all of
# them to serve the two that know the answer would put the argument in the
# hands of the sites least able to supply it.
#
# Set once per request from the session; falls back to the deployment
# default when nothing set it, which is exactly the error-path case.
_gather_language: contextvars.ContextVar[str] = contextvars.ContextVar(
    "kinato_gather_language", default=""
)


def _remaining_reasoning_budget() -> float:
    """How long the agent may reason, given what this turn has already spent.

    The old flat VOICE_DEADLINE_S had to assume the worst about everything
    before it - a cold session rehydration, a slow paid-guard read - on
    every turn, including the turns where none of that happened. This asks
    the clock instead. What is left after setting aside one TTS render and
    the time to return TwiML is the budget, and it shrinks on its own when
    the turn has already been expensive rather than being wrong about it in
    advance.
    """
    started = _turn_started.get()
    if not started:
        # No turn clock (a direct unit-test call, the batch harness). Fall
        # back to the ceiling rather than inventing a number.
        return VOICE_DEADLINE_MAX_S
    # Only reserve TTS time if TTS will actually cost any.
    #
    # With ElevenLabs off - no voice id, or the account refused it -
    # voice_block returns a Twilio <Say> with no network call at all. Setting
    # aside two seconds for that hands two seconds of a five-second budget to
    # something that takes microseconds, and takes them from the only thing
    # left to spend them on.
    tts_cost = TTS_BUDGET_S if elevenlabs_active() else 0.0
    remaining = TURN_HARD_TIMEOUT_S - (time.monotonic() - started) - tts_cost - RESPONSE_RESERVE_S
    return max(VOICE_DEADLINE_MIN_S, min(VOICE_DEADLINE_MAX_S, remaining))

# The keypad. This is the ONLY input path in the system that speech
# recognition cannot corrupt - a DTMF digit arrives as a digit, at
# confidence 1.0 by construction. That is precisely why opt-out lives here
# as well as on speech: "take me off your list" is the one instruction a
# customer must never have to repeat because we misheard it.
DTMF_OPT_OUT = "9"
DTMF_YES = "1"
DTMF_NO = "2"
DTMF_CALLBACK = "0"

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

# Strong references to fire-and-forget writes.
#
# asyncio only holds a WEAK reference to a running task, so a bare
# create_task whose result nobody keeps can be garbage collected before it
# finishes - the write simply vanishes, occasionally, under load, with no
# error. Bookkeeping moved off the critical path must still actually
# happen; "off the critical path" is not "optional".
_BACKGROUND_WRITES: set = set()


def _spawn_write(coro) -> None:
    task = asyncio.create_task(coro)
    _BACKGROUND_WRITES.add(task)
    task.add_done_callback(_BACKGROUND_WRITES.discard)

SYSTEM_PROMPT_TEMPLATE = """You are a warm, professional sales representative {from_business}making an \
OUTBOUND phone call to {customer_label} about {item_description}, which they started buying but didn't finish.

YOU called THEM. They are not expecting this call and have no idea who you are yet. So never open with \
"how can I help you?" or "what can I do for you?" - that is what an inbound support line says, and it will \
confuse them. You are the one with a reason for calling; give it.

THE SALE COMES FIRST. This sequence exists to rescue a sale, never to delay one. The moment the customer \
says they want to buy, or asks you to send the link/checkout, STOP the sequence and send it - call \
check_offer, then issue_offer, skipping any remaining steps.

You have ALREADY greeted them and they have replied - that is what you are responding to. If they have not \
yet said what they want, work through this sequence, ONE step per turn, waiting for their reply each time, \
and never deliver several steps in one breath. SKIP any step they have already answered: someone who opens \
by asking for the link has answered all of them, and repeating a step back at them loses the sale.

1. Only if you cannot tell whether they heard you, check. Do not re-introduce yourself - you already did.
2. Say why you are calling{business_intro}. Do NOT give yourself a personal name - you have not been given \
one, and inventing one (or emitting anything in brackets) is worse than simply not having one. Name the \
business and move on. Mention that you noticed they were looking at {item_description} on the site and \
didn't finish checking out.
3. Ask, openly, what stopped them - but ONLY if they have not already told you and have not asked you to \
send the link. If they have, do not ask - call check_offer with requested_discount_percent=0 and then issue_offer with the token it gives you. Asking this of someone who just said "send me the link"\
is the single worst thing you can do on this call. Do not guess the reason and do not lead with a discount - the real \
barrier might be shipping, size, timing, or trust, and offering money to someone who was worried about \
delivery just wastes margin.
4. Respond to the barrier they actually name. ONLY if price is genuinely the issue, call check_offer \
DIRECTLY and propose exactly what it approved. check_offer already loads the real cart and the real policy \
itself - calling get_cart or get_policy_limits first is wasted time on a live call and can run the turn out \
of budget before any offer is made.
5. Ask them plainly whether they would like you to send it. Accept a clear yes or a clear no.

At what price? If they have NOT raised price as their reason, send FULL PRICE: check_offer with \
requested_discount_percent=0. But if they HAVE said the price is too high, or asked for a discount, do NOT \
send full price merely because they then said yes - they already told you what the barrier was. Call \
check_offer with a real discount percentage and let the policy engine decide what is affordable. Ignoring a \
stated price objection and quoting the same price back is how you lose the sale you had just rescued.

Never ask a second time why they didn't complete the order. If they decline to say, or just repeat that \
they want the link, that is a complete answer - send the link.

NEVER volunteer a discount. Do not ask "would you like me to check for a discount" and do not mention \
discounts at all unless the customer has themselves said the price is too high and is still hesitating. A \
customer who is ready to buy at full price must never be offered less than full price - that is the \
merchant's margin, and giving it away on a sale you had already won is worse than making no call at all.

A declined card, an expired session, or a checkout that errored is NOT a price objection. Those customers \
want to pay you the full amount; they just need a working link. Send one - do not discount.

Sound like a real person on a real phone: natural phrasing, brief warm acknowledgements, no script reading. \
Keep every reply to 1-2 short sentences - this is a live call, not an email. Never say a discount percent \
or price that did not come from check_offer's response; you do not know the merchant's real limits until \
you ask. Call issue_offer once the customer has agreed to an offer you already proposed.

WHEN YOU SAY AN AMOUNT, read the tool's `say_amount` field exactly as written - it is already in rupees and \
already phrased for speech. The `_paise` numbers beside it are for the system, not for the customer: they \
are a hundred times larger, and saying one aloud quotes a price nobody can act on.

A customer who wants to pay gets a LINK, never a callback. "My card was declined", "it failed", "let me \
try again" all mean send one now - do not offer them a date instead, and do not call get_timing_plan at all. \
Booking a follow-up for someone who was ready to buy loses the sale you had already won.

IF THEY GENUINELY CANNOT PAY RIGHT NOW - "not till payday", "after the 1st", "no money this week" - that is a timing \
problem, not a price problem, and money off does not solve it. Call get_timing_plan and offer one of the \
dates it returns. Read its `say_window` exactly as written; do not restate or recalculate the date. \
NOTHING IS SCHEDULED AND NOTHING RETRIES ITSELF - never tell a customer we will try their card again \
automatically, because we will not. If they agree to a date, call record_promise_to_pay with it; that is \
the thing that actually holds it. If get_timing_plan returns no windows, do not suggest a time at all - \
that payment will not succeed on the same card whenever it is retried.

Read agreement the way a person would, not by matching exact words. A real customer will never know they \
are supposed to say a particular phrase. "Okay", "sure", "yeah", "haan", "theek hai", "ji", "go ahead", \
"send it", "fine", or simply "yes" are ALL agreement - act on them. Their speech reaches you through \
imperfect phone transcription, so it may be garbled or clipped; judge intent, not wording. Only ask again \
if you genuinely cannot tell whether they said yes or no, or if they sound confused about what you offered \
- and if so ask once, simply ("Shall I send it across?"), never repeatedly.

IF THEY TURN DOWN AN OFFER AND ASK FOR MORE, CALL check_offer AGAIN. Turning one down is exactly what \
earns the next step - the number you were given last time was a step, not a ceiling, and there may be more \
room behind it. Never say "that is the maximum" or "the most I can do" from memory: you do not know the \
maximum, you only know what you were approved a moment ago. Ask, then tell them what comes back.

YOU MAY NEVER SAY A DISCOUNT IS UNAVAILABLE UNLESS check_offer HAS JUST REFUSED ONE. You do not know the \
merchant's limits and you are not the one who decides them. If the customer asks for a discount, or confirms \
the price is what is stopping them, CALL check_offer - even if you asked them a confirming question last turn \
and they have now answered it. That answer is what the question was for. Saying "I can't offer a discount" \
without calling it is telling them an answer nobody worked out.

If a tool refuses you, it will say why in a REJECTED_ code, and the code tells you what to do next: ask them \
to repeat themselves, read the barrier back, or send full price. Do exactly that - never argue with it and never \
guess your way past it.

They can also press 1 for yes, 2 for no, 9 to be removed from the list, or 0 for a callback. Mention it only \
if the line is clearly bad.

Sending a payment link is not a risk worth stalling over: nothing is charged, no money moves, and the \
customer decides at their own leisure. Refusing to send when they meant yes costs the merchant a sale they \
had already won, which is the more expensive mistake.

If they say they WILL pay but not right now - "I'll pay tomorrow", "after payday", "end of the week" - call record_promise_to_pay with the date they named and their own words. Then STOP selling: they have already agreed, so do not negotiate further and do not offer a discount. Still send the link so it is waiting for them. Continuing to push someone who has just committed is how a helpful call turns into harassment.

record_opt_out REVOKES CONSENT PERMANENTLY - that customer can never be contacted again, about this order \
or any future one. Call it ONLY when they actually ask not to be contacted: "don't call me again", "take \
me off your list", "stop contacting me". Declining THIS purchase is not opting out. "I changed my mind", \
"I don't want it now", "I already bought one elsewhere", "not interested" all mean no to this sale and \
nothing more - thank them, close warmly, and leave their consent intact. Treating a polite no as a \
permanent opt-out throws away every future recovery for that customer.
"""


_PLACEHOLDER_IDENTITY = re.compile(
    r"(?:this is|i'm|i am|my name is)\s+(?:\[[^\]]*\]|\{[^}]*\}|<[^>]*>)\s*(?:,)?\s*",
    re.IGNORECASE,
)


def _strip_placeholder_identity(text: str) -> str:
    """Removes a self-introduction built around a template slot.

    "Great! This is [Your Name] from Dhruv." -> "Great! From Dhruv."
    Losing the introduction is far better than speaking brackets aloud, and
    the agent still names the business it is calling from.
    """
    cleaned = _PLACEHOLDER_IDENTITY.sub("", text)
    cleaned = re.sub(r"\[[^\]]*\]|\{[^}]*\}|<[^>]*>", "", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return cleaned or "Hello - I'm calling about the order you started with us."


# Card data solicited over the phone.
#
# On a live call, after issue_offer had failed four times, the agent said:
# "I can assist you in completing your order if you'd like to provide your
# card details again." It asked a customer to read their card number to an
# automated system - something no tool here can accept, nothing in this
# codebase is authorised to receive, and which would be a serious
# compliance problem if a customer complied.
#
# It reached for that because the sanctioned path kept failing, and a model
# under pressure improvises. The instructive refusals in tools.py should
# stop the cascade that led here; this is the structural check for when
# something else leads there instead. Deliberately narrow: it matches
# soliciting card DATA, not discussing a card that was declined, which the
# agent must still be able to do.
_CARD_SOLICITATION = re.compile(
    r"\b(card|cvv|cvc|pin)\s*(number|details|detail|digits|info|information)\b"
    r"|\bcvv\b|\bcvc\b"
    r"|\b(provide|share|give|read|tell|enter|confirm|verify)\s+(me\s+)?(your|the)\s+card\b"
    r"|\bexpiry\s*(date)?\b",
    re.IGNORECASE,
)

# What to say instead. Not an apology for a rule - a redirect to the only
# way this agent can actually take money, which is a link.
#
# Careful with the wording: an earlier version said "I can't take card
# details over the phone", which trips the pattern above. A replacement
# that the guard would itself catch is a replacement that cannot be trusted
# to terminate, and a test asserts it does not.
_CARD_SOLICITATION_REPLACEMENT = (
    "I'm not able to take payment information over the phone - the only way I can help you pay is "
    "with a secure link. Let me get that sorted and sent across to you."
)


def solicits_card_details(text: str) -> bool:
    return bool(text) and bool(_CARD_SOLICITATION.search(text))


# Saying no to a discount is a decision, and it belongs to the policy
# engine. This catches the agent making it alone.
#
# From a live call, on a merchant whose ceiling was 8% and whose ladder
# opens at 3%:
#
#   customer: "No, I was thinking to getting any discount."   (heard at 0.90)
#   agent:    "Unfortunately, I can't offer a discount at this time."
#   tools:    []
#
# The barrier gate had already opened on the previous turn - the state
# machine did its job - and the model walked straight past it, declined on
# its own authority, and told a customer a policy outcome no policy engine
# had produced. A discount was available. It never asked.
#
# The negated forms are the whole pattern: "can't offer", "unable to give",
# "no discount available". An OFFER of a discount contains most of the same
# words and must not trip it, which is why the negation has to be adjacent
# rather than anywhere in the sentence.
_DISCOUNT_REFUSAL = re.compile(
    r"\b(?:"
    r"can(?:'|’)?t|cannot|can not|unable to|not able to|won(?:'|’)?t be able|"
    r"no|not|don(?:'|’)?t have|isn(?:'|’)?t|aren(?:'|’)?t"
    r")\b[^.!?]{0,40}?\b(?:discount|off the price|reduction|lower(?:ed)? price)\b"
    r"|\bno\s+discount\b"
    r"|\bdiscount[^.!?]{0,25}\b(?:not available|unavailable|isn(?:'|’)?t available)\b",
    re.IGNORECASE,
)


# Announcing a ceiling the engine did not just produce.
#
# From a live call, on a merchant whose ladder is [3, 7, 10]:
#
#   check_offer -> approved 3% (REJECTED_LADDER_STEP)
#   agent:    "I can offer you a discount of three percent..."
#   customer: "No, can we go for 15%, that is too low for me."
#   agent:    "the maximum I can offer right now is three percent"
#   tools:    []
#
# The customer refused rung one, which is the exact event the ladder exists
# to answer, and the agent declared that rung a maximum without asking
# anything. A second check_offer returns seven. The sale closed at three on
# a policy that would have gone further to win it.
#
# Sibling of _DISCOUNT_REFUSAL and the same mistake underneath - a limit
# stated that no engine produced this turn - but it shares none of the
# wording, which is why the first guard sailed past it. There is not a
# refusal word in the sentence.
#
# Quoting an approved number is fine and must stay fine: "I can offer you
# three percent" is the agent doing its job. Only the claim that the number
# is the CEILING is caught.
_MAXIMUM_CLAIM = re.compile(
    r"\b(?:the\s+)?(?:maximum|max|most|best|highest|limit)\b[^.!?]{0,60}?"
    r"\b(?:can|could|able to|i(?:'|’)?m able|available|offer|do|give|go)\b"
    r"|\b(?:that(?:'|’)?s|that\s+is|this\s+is)\s+(?:the\s+)?"
    r"(?:most|best|maximum|max|highest|limit)\b"
    r"|\bmaximum\s+discount\b",
    re.IGNORECASE,
)


def claims_a_maximum(text: str) -> bool:
    """True when the reply announces a ceiling rather than reporting one.

    Only meaningful alongside "and check_offer did not run this turn" - the
    caller checks that. After a real call to the engine, a maximum it
    returned is a fact worth relaying, and at the top rung it is simply
    true.
    """
    return bool(text) and bool(_MAXIMUM_CLAIM.search(text))


# What to say instead: go and ask. Deliberately asserts no number in either
# direction - inventing a bigger concession would be this same bug pointed
# the other way, and the customer would hear a figure that may never come.
_LADDER_NOT_CLIMBED_REPLACEMENT = (
    "Let me see what else I can do on the price for you - one moment."
)


def claims_discount_refused(text: str) -> bool:
    """True when the reply tells the customer no discount is possible.

    Only meaningful alongside "and no tool refused one this turn" - the
    caller checks that. A refusal the policy engine actually produced is a
    fact the agent SHOULD be relaying.
    """
    return bool(text) and bool(_DISCOUNT_REFUSAL.search(text))


# What to say instead. Deliberately not the opposite claim: promising a
# discount the engine may be about to refuse would be the same bug pointed
# the other way, and the customer would hear a number that never arrives.
# This commits to exactly one thing - looking - which is the thing that was
# skipped.
_DISCOUNT_REFUSAL_REPLACEMENT = (
    "Let me check what I can do on the price for you - one moment."
)


# One agent turn at a time, per call.
#
# When a webhook takes longer than Twilio is willing to wait, Twilio RETRIES
# the POST with identical parameters - and nothing here noticed. The retry
# rehydrated the session and ran the agent AGAIN on the same sentence, which
# on the wrong turn means a second offer token and a second payment link for
# one thing the customer said once. Every other duplicate-delivery path in
# this system is guarded (the event bus has durable idempotency keys, the
# payment webhook dedupes on payment_id); this one, the only path a customer
# actually hears, was not.
#
# An in-flight guard rather than a replay cache, deliberately. Two designs
# were tried and this is the only one that is correct:
#
#   Keying on the turn index does not work at all - the first attempt
#   increments `turns`, so a retry never matches the key the original wrote.
#
#   Keying on the SPOKEN WORDS alone works for retries and is wrong for
#   people: a customer who says "yes" twice in twenty seconds is not Twilio
#   retrying, and replaying the previous answer to a different question is
#   its own bug.
#
# What actually distinguishes the two is concurrency. Twilio only retries
# when it received NO response, so a retry arrives while the original is
# still running. A human always speaks after we have answered. So: refuse to
# start a second turn while one is in flight, and let anything that arrives
# after we answered through as the new turn it is.
#
# In-process, like CALL_SESSIONS, with the same single-worker caveat.
_TURNS_IN_FLIGHT: set = set()


# How the agent speaks, when the merchant has asked for Hinglish.
#
# Written as "produce this", never as "don't produce that". FINDINGS #15 is
# the story of a prompt rule phrased as a prohibition that made the thing it
# forbade MORE likely, because stating it put the forbidden text in front of
# the model. So this describes the register to write in and stops there.
#
# Two constraints that are not stylistic:
#
#   Roman script, because the line is read aloud by an Indian-English TTS
#   voice. Devanagari is either skipped or spelled out; the same words in
#   Latin letters are pronounced correctly by an en-IN voice.
#
#   Numbers and money in English, because the amount is the one thing on
#   this call that must be unambiguous. A mispronounced rupee figure is
#   worse than a slightly stilted sentence, and it is the sentence the whole
#   call exists to deliver.
_HINGLISH_STYLE = """SPEAK HINGLISH - the everyday mix an Indian salesperson actually uses on the phone, \
not textbook Hindi. Hindi carries the warmth and the connective words; English carries the ones people \
genuinely say in English anyway: order, link, payment, card, email, discount, website.

Write it in Roman letters, never Devanagari - your words are read aloud by an Indian-English voice, and it \
pronounces "aapka order ready hai" correctly while Devanagari comes out as nonsense.

Say every number, price and rupee amount in English. The amount is the one thing on this call that has to \
be unambiguous.

If they reply in English, stay in Hinglish anyway - warm and local is the point, and switching to match \
them makes the call sound like a machine adjusting."""


def _tool_succeeded(result, tool: str) -> bool:
    """Did this tool actually DO the thing, or merely get attempted?

    `tool_calls_made` means "ran", not "worked" - runtime.py appends the name
    immediately after execution, before looking at the result, and that is
    deliberate: the batch scoreboard needs the record of everything that
    executed even when the turn later times out.

    But the reply below is a statement to a customer about their money, and
    "attempted" is not a thing worth saying out loud. A refused issue_offer
    used to reach the same branch as a successful one, so on any turn where
    the model then produced no closing sentence - max_iterations_reached and
    a hit deadline both do exactly that - the agent said "I've sent that
    offer to your email" having sent nothing.

    The answer was already on the same object, fifteen lines from where it
    was needed: `refusals` is read just above to drive the
    barrier-confirmation state machine.

    Conservative by construction: if ANY attempt of this tool was refused
    this turn, it does not count as succeeded, even if a later attempt
    worked. Understating a send costs one extra sentence; overstating one
    cannot be taken back.
    """
    if tool not in (result.tool_calls_made or []):
        return False
    return not any(r.get("tool") == tool for r in (result.refusals or []))


def _build_system_prompt(
    customer_name: str,
    item_description: str,
    business_name: str = "",
    failure_class: Optional[str] = None,
    memory_brief: str = "",
    emi_available: bool = False,
    voice_persona: str = "",
    voice_language: str = "",
    barrier_confirmed: bool = False,
) -> str:
    """Builds the live-call system prompt.

    business_name is threaded through so the agent can say who is calling.
    When the merchant has no usable name on file it is omitted entirely
    rather than filled with a placeholder - an earlier version left the
    model to invent one and it read "[Your Company]" aloud to a customer.
    """
    customer_label = customer_name if customer_name else "the customer"
    prompt = SYSTEM_PROMPT_TEMPLATE.format(
        customer_label=customer_label,
        item_description=item_description,
        from_business=f"from {business_name} " if business_name else "",
        business_intro=f" - you are calling from {business_name}" if business_name else "",
    )
    # One plain sentence about why the payment failed, never the raw
    # Razorpay error object. Appended rather than interpolated so that when
    # there is nothing useful to say the prompt simply does not mention it,
    # instead of asserting something hollow - the same rule the email
    # templates follow. The hard behaviour still lives in check_offer's
    # REJECTED_FULL_PRICE_FIRST; this only stops the agent sounding
    # ignorant of something it is being held to.
    # Appended before the diagnosis and the memory brief, so the register
    # is established before the call-specific facts arrive - a language
    # instruction buried under two paragraphs of context gets followed less
    # reliably than one that reads as part of the role.
    # The merchant's own choice wins; settings.AGENT_LANGUAGE is only the
    # deployment-wide fallback for merchants who have not set one. This read
    # used to be the global alone, so a merchant who picked Hinglish got the
    # right <Gather> recogniser and an agent that still spoke English - it
    # listened correctly and answered in the wrong language.
    lang = agent_language.resolve(voice_language or settings.AGENT_LANGUAGE)
    if lang.code == "hinglish":
        # Hinglish keeps its own longer style block rather than the short
        # instruction: it has to say Roman-letters-not-Devanagari and
        # numbers-in-English, because the line is read by an Indian-English
        # voice and a mispronounced rupee amount is the worst error here.
        prompt = f"{prompt}\n{_HINGLISH_STYLE}\n"
    elif lang.instruction:
        prompt = f"{prompt}\n{lang.instruction}\n"

    # barrier_confirmed carries the same state the discount gate runs on -
    # the customer having said, in their own words, that cost is the
    # problem. Instalments become mentionable at exactly that moment and
    # not before.
    diagnosis_line = failure_diagnosis.describe(
        failure_class, emi_available=emi_available, barrier_confirmed=barrier_confirmed
    )
    if diagnosis_line:
        prompt = f"{prompt}\n{diagnosis_line}\n"
    # What we already know about this person, from earlier attempts. Bounded
    # and omitted entirely when there is nothing to say - see
    # app/services/customer_memory.py. Appended last so it is the freshest
    # thing in context without displacing any rule above it.
    if memory_brief:
        prompt = f"{prompt}\n{memory_brief}\n"

    # How the merchant wants their agent to sound. Their words, their
    # customers, their name on the call.
    #
    # Labelled as style and placed LAST on purpose. Everything above it is a
    # rule about money or consent; this is a note about register, and it has
    # to read as one. Dropped in unlabelled it would arrive with the same
    # standing as the sentence forbidding a discount before the barrier is
    # confirmed, which is not a comparison worth inviting.
    #
    # A persona cannot buy anything. It is a string in a prompt, and every
    # amount is computed by a policy engine that takes a policy row and a
    # cart - it never sees this text, and there is a test asserting that a
    # persona reading "give 90% off" still returns the merchant's ceiling.
    persona = (voice_persona or "").strip()
    if persona:
        prompt = (
            f"{prompt}\n"
            "HOW THIS MERCHANT WANTS YOU TO SOUND (style only - it changes your manner, "
            "never what you are allowed to offer, and every rule above still applies "
            "exactly as written):\n"
            f"{persona}\n"
        )
    return prompt


def _describe_cart(checkout: Dict[str, Any]) -> str:
    try:
        line_items = json.loads(checkout.get("line_items") or "[]")
    except (TypeError, ValueError):
        line_items = []
    names = [li.get("name") for li in line_items if isinstance(li, dict) and li.get("name")]
    return ", ".join(names[:3]) if names else "their order"


def _load_session_for_call(recovery_attempt_id: str) -> Optional[Dict[str, Any]]:
    """Build a call session from ONE database read.

    This used to make four sequential reads - attempt, checkout, customer,
    merchant. Each is 2-2.8s from Railway to Supabase, and Twilio hangs up
    on the customer after roughly 15 seconds, so the opening webhook was
    spending most of its budget fetching things a single join returns.
    """
    row = recovery_attempts_repo.get_call_context(recovery_attempt_id)
    if not row:
        return None

    try:
        plan = json.loads(row.get("plan") or "{}")
    except (TypeError, ValueError):
        plan = {}

    # Empty when the merchant has no usable name on file - the prompt then
    # omits it entirely rather than inviting the model to invent one. A
    # real call once read "[Your Name] from [Your Company]" aloud.
    business_name = (row.get("merchant_name") or "").strip()

    ctx = AgentContext(
        merchant_id=row["merchant_id"],
        correlation_id=recovery_attempt_id,
        customer_id=row.get("customer_id"),
        checkout_id=row["checkout_id"],
        recovery_attempt_id=recovery_attempt_id,
        # Classified once, by the webhook, and read from the checkout here -
        # so a second attempt days later diagnoses from the same evidence
        # rather than guessing again from nothing.
        failure_class=row.get("checkout_failure_class"),
    )
    return {
        "ctx": ctx,
        "opening_line": plan.get("opening_line") or "Hi there! I wanted to help you finish your order.",
        # Pre-generated by call_orchestrator.py before dialing, when
        # possible - see its docstring for why the greeting is generated
        # ahead of connect-time rather than lazily inside this webhook.
        "opening_voice_block": plan.get("voice_block") or "",
        "customer_name": (row.get("customer_name") or ""),
        "item_description": _describe_cart({"line_items": row.get("checkout_line_items")}),
        "business_name": business_name,
        # Carried on the session so every turn's prompt is built the same
        # way as the opening one - a persona that applies only to the first
        # sentence is a voice that changes halfway through a call.
        "voice_persona": (row.get("voice_persona") or "").strip(),
        # The merchant's language choice, resolved once per call so the
        # prompt, the voice and the recogniser cannot disagree with each
        # other partway through.
        "agent_language": (row.get("agent_language") or "").strip(),
        "failure_class": row.get("checkout_failure_class"),
        # Pre-computed by call_orchestrator BEFORE dialling and carried in
        # the plan, for the same reason the opening voice block is: this
        # webhook answers inside Twilio's ~15s deadline, and blowing that
        # deadline does not degrade the call, it ENDS it.
        "memory_brief": plan.get("memory_brief") or "",
        # Same pre-dial plan, same reason. False when absent, which covers
        # every attempt created before this existed - and False is the safe
        # direction, because the failure mode of guessing True is promising
        # a customer instalments their checkout cannot give them.
        "emi_available": bool(plan.get("emi_available")),
        "turns": 0,
        # Zero for a fresh call, which costs no query; _rehydrate_session
        # overrides it with the real value read from the transcript, the
        # only path where it can be non-zero.
        "turn_index": 0,
        # Consecutive turns we could not make out at all. Reset by any
        # usable turn; MAX_MISHEARD_STREAK ends the attempt gracefully
        # instead of asking a third time.
        "misheard_streak": 0,
        # Set once check_offer has bounced a discount with
        # REJECTED_UNCONFIRMED_BARRIER. Deriving it from the refusal rather
        # than by scanning the customer's words for "yes" is deliberate:
        # keyword-matching agreement is the pattern this file's own rewrite
        # deleted.
        "discount_bounced": False,
    }


def _gather_twiml(
    voice_block: str,
    retry_message: str = "Are you still there?",
    gather_language: str = "",
) -> str:
    """Every Gather accepts speech AND a keypad digit.

    `input="speech dtmf"` costs nothing when the customer just talks, and
    gives them a path that transcription cannot corrupt when it is not
    working. numDigits="1" means a single press submits immediately rather
    than waiting for a terminating key nobody knows to press.
    """
    # <Gather> takes exactly ONE language - there is no bilingual recogniser.
    # This is the setting with teeth: a mis-transcription feeds the
    # confidence gate that blocks the money tools, so choosing it badly
    # costs recoveries, not merely transcript quality.
    attrs = (
        f'input="speech dtmf" numDigits="1" action="{settings.NGROK_URL}/voice/respond" '
        f'method="POST" speechTimeout="auto" timeout="5" '
        f'language="{gather_language or _gather_language.get() or settings.VOICE_GATHER_LANGUAGE}"'
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Response>\n'
        f'    <Gather {attrs}>\n'
        f'        {voice_block}\n'
        '    </Gather>\n'
        f'    <Gather {attrs}>\n'
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
    """Same contract as /voice/respond: this may only ever answer with
    TwiML. A 500 here is a call that is dropped before the customer has
    heard a word."""
    try:
        return await _outbound(request)
    except Exception as e:
        logger.error(f"/voice/outbound raised, ending the call gracefully: {e}", exc_info=True)
        return Response(
            content=_escalation_twiml(
                "Sorry, I'm having trouble pulling up your order right now. We'll follow up by email."
            ),
            media_type="text/xml",
        )


async def _outbound(request: Request):
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
    if session:
        _gather_language.set(agent_language.resolve(session.get("agent_language")).gather)
    if not session:
        logger.error(f"voice/outbound: no recovery_attempt found for id={recovery_attempt_id!r}, ending call.")
        return Response(
            content=_escalation_twiml("Sorry, I'm having trouble pulling up your order right now. We'll follow up by email."),
            media_type="text/xml",
        )

    CALL_SESSIONS[call_id] = session

    # Persisted AFTER the response, never before it.
    #
    # Twilio gives this webhook about 15 seconds before it hangs up on the
    # customer with "we cannot reach your server". _load_session_for_call
    # already costs four sequential reads, and each one is 2-2.8s from
    # Railway to Supabase (see policies.py). Adding two writes in front of
    # the TwiML put the total straight through that ceiling and killed real
    # calls - the ONLY thing this handler owes Twilio is the XML.
    #
    # Neither write is needed to produce it. The CallSid mapping matters to
    # a later turn, and the opening line matters to the transcript; both can
    # land a moment after the customer has already started hearing it.
    async def _persist_opening() -> None:
        try:
            await run_db_async(
                recovery_attempts_repo.update_state,
                recovery_attempt_id,
                "CALLING",
                twilio_call_sid=call_id,
            )
            await _record_turn(session, turns_repo.AGENT, session["opening_line"])
        except Exception as e:
            logger.warning(f"[CALL {call_id}] Post-answer bookkeeping failed (non-fatal): {e}")

    _spawn_write(_persist_opening())
    # And tell the AGENT it said it, not just the transcript. Twilio plays
    # this line; the model never saw it, so it introduced itself again on
    # the very next turn.
    agent_runtime.seed_opening(
        call_id,
        _build_system_prompt(
            session["customer_name"],
            session["item_description"],
            session.get("business_name", ""),
            session.get("failure_class"),
            session.get("memory_brief", ""),
            session.get("emi_available", False),
            session.get("voice_persona", ""),
            session.get("agent_language", ""),
            session.get("discount_bounced", False),
        ),
        session["opening_line"],
    )

    # Prefer the pre-generated block (see call_orchestrator.py) - only
    # synthesize live here if pre-generation wasn't done. voice_block()
    # always returns something playable (ElevenLabs, or Twilio's own
    # Neural voice) - see tts.py's module docstring.
    block = session["opening_voice_block"] or await tts_voice_block(session["opening_line"])

    # One short, spoken mention of the keypad, on the opening turn only.
    # It is here rather than in the prompt because it is a promise the code
    # keeps, not a line the model may or may not remember to say: the
    # keypad is the only route out of this call that transcription cannot
    # swallow, and a customer who is being misheard is exactly the customer
    # most likely to want out. Deliberately one sentence and no menu - a
    # recited IVR list on a sales call is its own kind of failure. Rendered
    # by Twilio itself, so it costs no ElevenLabs budget.
    keypad_note = (
        f'<Say voice="{TWILIO_NEURAL_VOICE}">'
        "Just so you know, you can press 9 at any time to be removed from our list."
        "</Say>"
    )

    return Response(content=_gather_twiml(block + keypad_note), media_type="text/xml")


async def _record_turn(
    session: Dict[str, Any],
    speaker: str,
    text: str,
    stt_confidence: Optional[float] = None,
    input_mode: str = "speech",
) -> None:
    """Persist one side of one exchange.

    Never fatal. A transcript is evidence about a call, not a precondition
    for one - failing the customer's turn because the write failed would
    trade a real conversation for a record of it. Same rule the audit log
    and the event bus already follow.
    """
    ctx = session["ctx"]
    if not ctx.recovery_attempt_id or not (text or "").strip():
        return

    # The index is claimed SYNCHRONOUSLY, before anything is awaited, so
    # two turns can never race for the same slot even though the writes
    # themselves land in the background.
    index = session["turn_index"]
    session["turn_index"] = index + 1

    async def _write() -> None:
        await run_db_async(
            turns_repo.record_turn,
            merchant_id=ctx.merchant_id,
            recovery_attempt_id=ctx.recovery_attempt_id,
            turn_index=index,
            speaker=speaker,
            text=text,
            customer_id=ctx.customer_id,
            channel="voice",
            stt_confidence=stt_confidence,
            input_mode=input_mode,
        )

    async def _safe_write() -> None:
        try:
            await _write()
        except Exception as e:
            logger.warning(f"conversation_turns write failed (non-fatal): {e}")

    # Fire and forget. A transcript is evidence ABOUT a call, never a
    # precondition FOR one, and Twilio hangs up at ~15s: two writes at
    # 2-2.8s each, sitting between the customer speaking and hearing a
    # reply, is the whole budget spent on bookkeeping. Same mistake as the
    # opening webhook, one layer down.
    _spawn_write(_safe_write())


def _rehydrate_session(call_id: str) -> Optional[Dict[str, Any]]:
    """Rebuild a lost call session from the database.

    A mid-call Twilio webhook carries the CallSid and nothing else, so when
    CALL_SESSIONS has no entry - after a restart, or on a second worker
    that never handled this call's /voice/outbound - the CallSid recorded
    on the recovery attempt is the way back. Before this existed the only
    possible answer was "Sorry, I lost track of our order details", said to
    a customer who was mid-sentence.
    """
    attempt = recovery_attempts_repo.get_by_call_sid(call_id)
    if not attempt:
        return None
    session = _load_session_for_call(attempt["recovery_attempt_id"])
    if session:
        _gather_language.set(agent_language.resolve(session.get("agent_language")).gather)
    if not session:
        return None

    turns = turns_repo.list_for_attempt(attempt["recovery_attempt_id"])
    session["turn_index"] = turns_repo.next_turn_index(attempt["recovery_attempt_id"])
    session["turns"] = sum(1 for t in turns if t["speaker"] == turns_repo.CUSTOMER)
    agent_runtime.restore_thread(
        call_id,
        _build_system_prompt(
            session["customer_name"],
            session["item_description"],
            session.get("business_name", ""),
            session.get("failure_class"),
            session.get("memory_brief", ""),
            session.get("emi_available", False),
            session.get("voice_persona", ""),
            session.get("agent_language", ""),
            session.get("discount_bounced", False),
        ),
        turns,
    )
    CALL_SESSIONS[call_id] = session
    logger.warning(
        f"[CALL {call_id}] Session was missing and has been rebuilt from the database "
        f"({len(turns)} turn(s)) - the conversation continues instead of ending."
    )
    return session


def _parse_confidence(raw: Any) -> Optional[float]:
    """Twilio's Gather `Confidence`, clamped to 0..1.

    Returns None when the field is absent or unparseable, which means "this
    input did not come from speech recognition" - a keypad press, or a
    payload shape we do not recognise. None is NOT "assume it was fine":
    the money tools treat None as not-applicable, so every path that can
    produce None must be a path where speech was not the input.
    """
    try:
        return max(0.0, min(1.0, float(raw)))
    except (TypeError, ValueError):
        return None


async def _run_agent_turn(
    call_id: str,
    session: Dict[str, Any],
    user_message: str,
    confidence: Optional[float],
    from_speech: bool,
) -> Response:
    """One conversational turn, shared by the speech path and the keypad
    path. `confidence` is None for keypad input - a pressed digit is not
    something we can mishear - and `from_speech` says which path we are on
    independently of whether Twilio sent a Confidence field."""
    ctx = session["ctx"]
    await _record_turn(
        session,
        turns_repo.CUSTOMER,
        user_message,
        stt_confidence=confidence,
        input_mode="speech" if from_speech else "dtmf",
    )

    # A fresh per-turn context. AgentContext is frozen, and that matters
    # here: what we heard, and whether the barrier has been confirmed, are
    # facts about THIS turn, not about the call. Mutating one shared
    # context would let a clearly-heard turn silently vouch for a later
    # garbled one.
    turn_ctx = dataclasses.replace(
        ctx,
        stt_confidence=confidence,
        # A keypad turn arrives with confidence None and is not speech; a
        # spoken turn is speech whether or not Twilio troubled itself to
        # send a Confidence field with it.
        input_is_speech=confidence is not None or from_speech,
        barrier_confirmed=session["discount_bounced"],
    )

    result = await agent_runtime.run_agent(
        system_prompt=_build_system_prompt(
            session["customer_name"],
            session["item_description"],
            session.get("business_name", ""),
            session.get("failure_class"),
            session.get("memory_brief", ""),
            session.get("emi_available", False),
            session.get("voice_persona", ""),
            session.get("agent_language", ""),
            session.get("discount_bounced", False),
        ),
        user_message=user_message,
        ctx=turn_ctx,
        tools=ALL_TOOLS,
        thread_id=call_id,
        max_iterations=VOICE_MAX_ITERATIONS,
        deadline_s=_remaining_reasoning_budget(),
    )

    # The discount was refused for want of a confirmed barrier. The agent's
    # reply to this turn is therefore the confirming question, so the
    # customer's NEXT turn is an answer to it and the discount path opens.
    # This is a state machine over real tool refusals, not an inference
    # about what the customer meant.
    # REJECTED_FULL_PRICE_FIRST belongs here too, and its absence was a bug.
    #
    # check_offer's own comment says the rule is "full price FIRST, not full
    # price forever", and it opens the gate on ctx.barrier_confirmed. But
    # only UNCONFIRMED_BARRIER ever set that flag, so on a SOFT_DECLINE the
    # gate had no key at all.
    #
    # Live, on a cart the customer had already told us was too expensive:
    #
    #   customer: "I found that too much expensive for me"
    #   customer: "can I get any discount over here?"
    #   customer: "I want a discount because I found that too much expensive"
    #   check_offer: DENY REJECTED_FULL_PRICE_FIRST
    #   agent: "the checkout failed due to a temporary issue, not the price"
    #
    # Told three times, and the agent argued with them - because the failure
    # class said the payment broke, and nothing could ever record that the
    # customer had since said otherwise. That is FINDINGS #1 in the
    # direction that costs a sale rather than a margin: refusing to
    # negotiate with someone who is plainly negotiating.
    #
    # Both refusals mean the same thing operationally - the agent has to put
    # the barrier to the customer and hear it confirmed before money moves -
    # so both open the same gate. The confirmation itself is still required;
    # this only makes it POSSIBLE.
    bounced = {"REJECTED_UNCONFIRMED_BARRIER", "REJECTED_FULL_PRICE_FIRST"}
    if any(r.get("reason") in bounced for r in result.refusals):
        session["discount_bounced"] = True
        logger.info(
            f"[CALL {call_id}] Discount bounced pending barrier confirmation - "
            "the agent asks the confirming question this turn."
        )

    # Checked BEFORE the degraded/no-content fallback, deliberately: a
    # timeout or dropped final response can still follow real, already-
    # executed tool calls (see app/agents/runtime.py's tool_calls_tracker -
    # a confirmed live bug where a real issue_offer had already sent a
    # real payment link, but the generic "technical difficulty" message
    # played anyway because the wrapping result looked degraded). The
    # customer must never be told "we'll follow up" when something real
    # already happened this turn.
    #
    # _tool_succeeded, not `in tool_calls_made`: that list records what RAN,
    # including what was refused, so the canned lines below - which are
    # claims about money and about consent - were reachable after a refusal
    # on any turn where the model produced no text of its own.
    if _tool_succeeded(result, "issue_offer"):
        reply_text = result.output.get("content") if (result.output or {}).get("content") else (
            "Great news - I've sent that offer to your email, you should see it any moment."
        )
    elif _tool_succeeded(result, "record_opt_out"):
        reply_text = result.output.get("content") if (result.output or {}).get("content") else (
            "Understood, I won't contact you about this again. Take care."
        )
    elif not result.ok or result.degraded or not (result.output or {}).get("content"):
        # Never fabricate a scripted line here - a short, honest, generic
        # reply is what "degraded" means for a live call.
        reply_text = "I hear you - let me have someone from our team follow up with you by email on this."
        await _publish_escalation(call_id, ctx, result.error or "agent_degraded_or_empty_reply")
    else:
        reply_text = result.output["content"]

    # A reply is SPOKEN ALOUD. The opening line has been guarded against
    # template slots since a call read "[Your Name] from [Your Company]" to a
    # customer - but that guard only covered the opening. A live turn later
    # said "This is [Your Name] from Dhruv", proving the guard was on the
    # wrong layer alone. Every spoken line goes through it now.
    if contains_placeholder(reply_text):
        logger.warning(
            f"[CALL {call_id}] Model reply contained a placeholder ({reply_text!r}) - "
            "replacing it rather than reading brackets aloud."
        )
        reply_text = _strip_placeholder_identity(reply_text)

    # Never ask a customer to say their card details out loud. Replaced
    # wholesale rather than edited: a sentence that got here is one whose
    # whole purpose was to solicit card data, and trimming the offending
    # clause would leave the offer standing.
    if solicits_card_details(reply_text):
        logger.error(
            f"[CALL {call_id}] Model reply solicited card details ({reply_text!r}) - "
            "replaced. Nothing in this system can accept them."
        )
        reply_text = _CARD_SOLICITATION_REPLACEMENT

    # Saying no to a discount is the policy engine's decision to make, and
    # the agent has now been caught making it alone. Live, on a merchant
    # whose ceiling was 8% and whose ladder opens at 3%:
    #
    #   customer: "No, I was thinking to getting any discount."  (heard 0.90)
    #   agent:    "Unfortunately, I can't offer a discount at this time."
    #   tools:    []
    #
    # A discount was available. The barrier gate had opened on the previous
    # turn, exactly as the state machine intends, and the model walked past
    # it and declined on its own authority - a claim about money that was
    # not true about anything that happened, which is FINDINGS #2's shape
    # pointed at the merchant's revenue instead of the customer's trust.
    #
    # Only when NOTHING refused this turn. A refusal the engine really
    # produced is a fact the agent should be relaying, and rewriting that
    # would hide the one thing the customer needs to hear.
    if claims_discount_refused(reply_text) and not result.refusals:
        logger.error(
            f"[CALL {call_id}] Model declined a discount it never asked for "
            f"({reply_text!r}) - replaced. tools={result.tool_calls_made}. "
            "Only check_offer may refuse a discount."
        )
        reply_text = _DISCOUNT_REFUSAL_REPLACEMENT

    # And the same rule for the other wording. Announcing a ceiling without
    # consulting the engine is the ladder never being climbed: the rung the
    # agent remembers is not a maximum, and saying so ends a negotiation the
    # merchant's own policy was willing to continue.
    #
    # Gated on check_offer NOT having run this turn, rather than on refusals
    # - after a real call, a maximum the engine returned is a fact the agent
    # should be relaying, and at the top rung it is true.
    if claims_a_maximum(reply_text) and "check_offer" not in (result.tool_calls_made or []):
        logger.error(
            f"[CALL {call_id}] Model announced a maximum without asking for one "
            f"({reply_text!r}) - replaced. tools={result.tool_calls_made}. "
            "A refused offer earns the next rung; only check_offer knows what it is."
        )
        reply_text = _LADDER_NOT_CLIMBED_REPLACEMENT

    logger.info(
        f"[CALL {call_id}] Agent ({'degraded' if result.degraded else 'ok'}): {reply_text!r} | "
        f"tools: {result.tool_calls_made} | refusals: {[r['reason'] for r in result.refusals]}"
    )

    await _record_turn(session, turns_repo.AGENT, reply_text)

    # Only hang up on a RECORDED opt-out. Ending the call here on a refused
    # one is the worst version of this bug: the customer hears "I won't
    # contact you about this again", the call ends so they cannot say it
    # twice, and nothing was written down - so the next attempt dials them
    # anyway. A promise never to call someone again, made while failing to
    # record it, is the one this system least deserves to get wrong.
    if _tool_succeeded(result, "record_opt_out"):
        agent_runtime.discard_thread(call_id)
        return Response(content=_escalation_twiml(reply_text), media_type="text/xml")

    block = await tts_voice_block(reply_text)
    return Response(content=_gather_twiml(block), media_type="text/xml")


async def _handle_keypad(call_id: str, session: Dict[str, Any], digit: str) -> Response:
    """The one input path speech recognition cannot corrupt.

    Opt-out and callback are handled here WITHOUT the model: a customer who
    presses 9 has given an unambiguous instruction, and routing it through
    an LLM turn can only add latency and ways to get it wrong. They still
    produce real audit rows, because they go through the same execute_tool
    choke point every other tool call does.
    """
    ctx = session["ctx"]
    session["misheard_streak"] = 0
    await _record_turn(session, turns_repo.CUSTOMER, f"[pressed {digit}]", input_mode="dtmf")

    if digit == DTMF_OPT_OUT:
        logger.info(f"[CALL {call_id}] Keypad {DTMF_OPT_OUT} - opting out, no model involved.")
        await execute_tool(record_opt_out, {}, ctx)
        agent_runtime.discard_thread(call_id)
        return Response(
            content=_escalation_twiml(
                "Understood - I've taken you off our list, and you won't hear from us again. Take care."
            ),
            media_type="text/xml",
        )

    if digit == DTMF_CALLBACK:
        logger.info(f"[CALL {call_id}] Keypad {DTMF_CALLBACK} - callback requested.")
        if ctx.recovery_attempt_id:
            await run_db_async(
                recovery_attempts_repo.update_state,
                ctx.recovery_attempt_id,
                "CALLBACK_REQUESTED",
                callback_requested_at=datetime.now(timezone.utc),
            )
        await bus.publish(
            event_type="recovery.callback_requested",
            payload={"recovery_attempt_id": ctx.recovery_attempt_id, "checkout_id": ctx.checkout_id},
            correlation_id=ctx.correlation_id,
            merchant_id=ctx.merchant_id,
        )
        agent_runtime.discard_thread(call_id)
        return Response(
            content=_escalation_twiml(
                "No problem at all - I'll arrange for someone to call you back at a better time. "
                "Thanks for your time."
            ),
            media_type="text/xml",
        )

    if digit in (DTMF_YES, DTMF_NO):
        # Turned into speech the agent can reason about, at confidence None
        # - a pressed key is not a transcription, so the mishearing gate
        # does not apply to it and a money tool may run on this turn.
        synthetic = "Yes, please go ahead." if digit == DTMF_YES else "No, thank you."
        logger.info(f"[CALL {call_id}] Keypad {digit} -> {synthetic!r}")
        return await _run_agent_turn(call_id, session, synthetic, confidence=None, from_speech=False)

    block = await tts_voice_block(
        "Sorry, I didn't catch that. You can just tell me, or press 1 for yes, 2 for no, "
        "or 9 to be removed from our list."
    )
    return Response(content=_gather_twiml(block), media_type="text/xml")


@voice_router.api_route("/voice/respond", methods=["GET", "POST"])
async def twilio_voice_respond(request: Request):
    """Twilio posts live speech transcriptions - and keypad digits - here.

    Nothing that happens inside may result in anything other than TwiML.
    An unhandled exception here becomes a 500, and Twilio answers a 500 by
    telling the customer "we cannot reach your server" and hanging up - so
    a bug anywhere in a turn ends the call rather than degrading it. The
    agent runtime already promises never to raise; this is the promise for
    everything around it.
    """
    started = time.monotonic()
    # Everything downstream derives its own budget from this one clock (see
    # _remaining_reasoning_budget). Set before the task is created so the
    # task inherits it.
    _turn_started.set(started)
    try:
        # A hard ceiling on the whole turn, not just on the reasoning.
        #
        # The agent runtime has its own deadline and the TTS has its own
        # budget, but that only bounds the parts we thought to bound. A live
        # call died with no response line and no traceback - meaning
        # something took longer than Twilio was willing to wait, or stopped
        # returning entirely, in a part of the turn nobody had put a clock
        # on. This is the clock on everything.
        #
        # Set below Twilio's own deadline on purpose: answering late is the
        # same as not answering, so the only useful timeout is one that
        # leaves time to say something afterwards.
        response = await asyncio.wait_for(_respond(request), timeout=TURN_HARD_TIMEOUT_S)
        # Twilio hangs up at roughly 15s. Logging where each turn actually
        # lands turns "the call died" into a number: a turn at 4s and a
        # turn at 14s look identical from the outside and are nothing alike.
        elapsed = time.monotonic() - started
        if elapsed > TURN_BUDGET_WARN_S:
            logger.warning(
                "voice/respond took %.1fs - Twilio gives about %.0fs before it "
                "hangs up on the customer.", elapsed, TWILIO_WEBHOOK_DEADLINE_S,
            )
        else:
            logger.info("voice/respond answered in %.1fs", elapsed)
        return response
    except asyncio.TimeoutError:
        logger.error(
            "voice/respond exceeded %.1fs and was cut off - answering anyway so the "
            "customer hears a person rather than Twilio's error.", TURN_HARD_TIMEOUT_S,
        )
        return Response(
            content=_gather_twiml(
                f'<Say voice="{TWILIO_NEURAL_VOICE}">Sorry, I lost my train of thought there. '
                "Could you say that again?</Say>"
            ),
            media_type="text/xml",
        )
    except Exception as e:
        # Deliberately broad. There is no failure here worth converting
        # into a dead call, and the alternative to a graceful line is
        # Twilio's own error message read aloud to a real customer.
        logger.error(f"/voice/respond raised, keeping the call alive: {e}", exc_info=True)
        try:
            block = await tts_voice_block(
                "Sorry, something went wrong on our side just then. Could you say that again?"
            )
            return Response(content=_gather_twiml(block), media_type="text/xml")
        except Exception:
            # Even the fallback failed - say it with Twilio's own voice,
            # which needs nothing from us but the words.
            return Response(
                content=_escalation_twiml(
                    "Sorry, we are having a technical problem. We will follow up by email."
                ),
                media_type="text/xml",
            )


async def _respond(request: Request):
    """One agent turn per exchange, through the Day 5 agent runtime, with
    real tools."""
    if request.method == "POST":
        source: Any = await request.form()
    else:
        source = request.query_params
    customer_speech = (source.get("SpeechResult") or "").strip()
    call_id = source.get("CallSid", "unknown")
    digits = (source.get("Digits") or "").strip()
    # Twilio has sent this on every speech turn since the first call this
    # project ever placed. It is finally read.
    confidence = _parse_confidence(source.get("Confidence"))

    session = CALL_SESSIONS.get(call_id)
    if not session:
        # The in-memory cache is no longer the only copy - see
        # _rehydrate_session. This branch used to be the end of the call.
        session = await run_db_async(_rehydrate_session, call_id)
    if not session:
        logger.error(f"voice/respond: no session and no persisted call for call_id={call_id!r}.")
        return Response(
            content=_escalation_twiml("Sorry, I lost track of our order details. We'll follow up by email."),
            media_type="text/xml",
        )

    # A turn for this call is already running, so this is Twilio retrying a
    # webhook it thinks we failed to answer - not the customer saying
    # something new. Running the agent again here is how one "yes" becomes
    # two payment links.
    #
    # <Pause> then <Redirect> rather than a spoken line: the original turn
    # is about to answer, and talking over it would have the customer hear
    # two different replies to one sentence. This just holds the line and
    # asks Twilio to come back.
    if call_id in _TURNS_IN_FLIGHT:
        logger.warning(
            f"[CALL {call_id}] A turn is already in flight - this is a Twilio retry, "
            "not a new utterance. Holding rather than running the agent twice."
        )
        return Response(
            content=(
                '<?xml version="1.0" encoding="UTF-8"?><Response>'
                '<Pause length="2"/>'
                f'<Redirect method="POST">{settings.NGROK_URL}/voice/respond</Redirect>'
                "</Response>"
            ),
            media_type="text/xml",
        )
    _TURNS_IN_FLIGHT.add(call_id)
    try:
        return await _respond_turn(session, call_id, customer_speech, digits, confidence)
    finally:
        _TURNS_IN_FLIGHT.discard(call_id)


async def _respond_turn(
    session: Dict[str, Any],
    call_id: str,
    customer_speech: str,
    digits: str,
    confidence: Optional[float],
):
    """The turn itself. Split out so the in-flight guard above owns a single
    try/finally around the whole thing - a guard that can leak its own flag
    on an exception is worse than no guard, because the call would then
    refuse every remaining turn."""
    session["turns"] += 1
    logger.info(
        f"[CALL {call_id}] Customer: {customer_speech!r} "
        f"(confidence={confidence if confidence is not None else 'n/a'}, digits={digits!r})"
    )

    # Paid-while-talking. The pre-dial guard checks this once, before the
    # phone rings - but a customer can pay from the earlier link, or on
    # another device, at any point DURING the call. Continuing to sell to
    # someone who has already paid is the single most damaging thing this
    # agent could do, so it is re-checked every turn rather than once.
    ctx = session["ctx"]
    if ctx.checkout_id:
        paid_ok, paid_reason = await run_db_async(outreach_guards.not_already_paid, ctx.checkout_id)
        if not paid_ok:
            logger.warning(f"[CALL {call_id}] STOP_ALREADY_PAID mid-call - ending without an offer.")
            if ctx.recovery_attempt_id:
                await run_db_async(
                    recovery_attempts_repo.update_state, ctx.recovery_attempt_id, "RECOVERED"
                )
            await bus.publish(
                event_type="recovery.blocked",
                payload={
                    "checkout_id": ctx.checkout_id,
                    "recovery_attempt_id": ctx.recovery_attempt_id,
                    "reason": "already_paid",
                    "detail": "customer paid during the call",
                },
                correlation_id=ctx.correlation_id,
                merchant_id=ctx.merchant_id,
            )
            return Response(
                content=_escalation_twiml(
                    "Ah - I can see your payment has just come through. That's all sorted, "
                    "so I won't take any more of your time. Thank you!"
                ),
                media_type="text/xml",
            )

    # The keypad wins over speech. If both arrived, the digit is the
    # deliberate act and the transcription is whatever noise accompanied it.
    if digits:
        return await _handle_keypad(call_id, session, digits)

    # Twilio heard silence. Re-prompt rather than handing an empty string
    # to the model, which will confidently reply to something nobody said.
    if not customer_speech:
        block = await tts_voice_block("Sorry, I didn't hear anything there - are you still with me?")
        return Response(content=_gather_twiml(block), media_type="text/xml")

    # Too garbled to reason about. Do not spend an LLM call on it, and do
    # not let the agent form an opinion about words probably never spoken.
    if confidence is not None and confidence < STT_UNUSABLE_FLOOR:
        session["misheard_streak"] += 1
        logger.warning(
            f"[CALL {call_id}] Unusable transcription (confidence={confidence:.2f} < {STT_UNUSABLE_FLOOR}), "
            f"streak={session['misheard_streak']} - not calling the model."
        )
        if session["misheard_streak"] >= MAX_MISHEARD_STREAK:
            # Asking a third time is where this stops being a bad line and
            # starts being an irritating call. Hand them the one input
            # channel that still works instead.
            block = await tts_voice_block(
                "I'm really struggling to hear you - the line isn't great. Press 1 and I'll email you "
                "the checkout link, or press 9 if you'd rather we didn't contact you again."
            )
            return Response(content=_gather_twiml(block), media_type="text/xml")
        block = await tts_voice_block("Sorry, the line broke up there - could you say that again?")
        return Response(content=_gather_twiml(block), media_type="text/xml")

    session["misheard_streak"] = 0
    return await _run_agent_turn(call_id, session, customer_speech, confidence, from_speech=True)



@voice_router.get("/api/call-sessions")
async def get_call_sessions():
    """Read-only debug view of in-memory call state - not persisted, not
    an API contract, just an inspection aid."""
    return {
        call_id: {
            "customer_name": s["customer_name"],
            "item_description": s["item_description"],
            "business_name": s.get("business_name", ""),
            "turns": s["turns"],
            "recovery_attempt_id": s["ctx"].recovery_attempt_id,
        }
        for call_id, s in CALL_SESSIONS.items()
    }
