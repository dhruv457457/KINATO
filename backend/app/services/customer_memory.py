"""
What this customer already told us.

Every recovery attempt before this one began from nothing. A customer who
said "the shipping time is too slow" on Monday was asked the same opening
question on Wednesday, as if the first call had never happened - which, as
far as the agent was concerned, it had not. That is the difference between
an agent and a script that runs twice.

Three constraints shape everything here, and they are all about restraint:

  * **Bounded.** The brief is hard-capped (see MAX_BRIEF_CHARS). A live
    voice turn has a 9-second reasoning budget and a Twilio webhook
    deadline behind it; memory that grows with history would slowly eat
    the turn that memory was supposed to improve, and the failure would
    show up as dropped calls, not as a memory bug.
  * **Facts, not summaries.** What is recalled is what was recorded - the
    barrier they stated, the offer they were made, whether they promised
    and whether they kept it. Nothing here asks a model to summarise a
    previous call, because a summary of a summary is how an agent ends up
    confidently telling a customer something they never said.
  * **Never a reason to contact anyone.** This module answers "what do we
    already know?" It has no opinion on whether to call, and every stopping
    rule still runs exactly as it did. A customer who opted out has a
    memory row and no outreach.
"""
import logging
from typing import Any, Dict, List, Optional

from app.db.repositories import conversation_turns as turns_repo
from app.db.repositories import recovery_attempts as recovery_attempts_repo

logger = logging.getLogger(__name__)

# The whole brief, including its heading. Deliberately small: this competes
# for the same context and the same seconds as the rules the agent must not
# forget, and the rules win.
MAX_BRIEF_CHARS = 400

# How much of the customer's own words to keep for the barrier they stated.
MAX_QUOTE_CHARS = 120


def _last_customer_utterance(customer_id: str, exclude_attempt_id: Optional[str]) -> str:
    """The last substantive thing this customer actually said, in an
    EARLIER attempt.

    Their own words rather than our characterisation of them, for the same
    reason `promise_words` is stored verbatim: "it's too expensive" and
    "I'd need to check with my wife first" are both "price-adjacent" and
    call for completely different second calls.
    """
    for turn in turns_repo.list_recent_for_customer(customer_id, limit=40):
        if turn.get("recovery_attempt_id") == exclude_attempt_id:
            continue
        if turn.get("speaker") != turns_repo.CUSTOMER:
            continue
        text = (turn.get("text") or "").strip()
        # Keypad presses are recorded as turns but are not something the
        # customer "said", and reading one back would be nonsense.
        if not text or text.startswith("[pressed "):
            continue
        # A turn we already knew we might have misheard is a bad thing to
        # quote back to someone with confidence a week later.
        confidence = turn.get("stt_confidence")
        if confidence is not None and confidence < 0.6:
            continue
        return text[:MAX_QUOTE_CHARS]
    return ""


def build_brief(
    customer_id: Optional[str],
    exclude_attempt_id: Optional[str] = None,
) -> str:
    """A short block for the agent's system prompt, or "" when there is
    nothing worth saying.

    Empty is the common case and the correct one for a first contact.
    Returning a "no prior history" line instead would spend context saying
    nothing and invite the model to remark on it.
    """
    if not customer_id:
        return ""

    try:
        attempts = recovery_attempts_repo.list_for_customer(customer_id, limit=10)
    except Exception as e:
        # Memory is an improvement, never a precondition. A call must not
        # fail because we could not remember the last one.
        logger.warning(f"customer_memory: could not load prior attempts (non-fatal): {e}")
        return ""

    prior = [a for a in attempts if a["recovery_attempt_id"] != exclude_attempt_id]
    if not prior:
        return ""

    facts: List[str] = []

    # Promises first: this is the fact most likely to change how the call
    # should open, in both directions.
    promised = next((a for a in prior if a.get("promised_at")), None)
    if promised:
        kept = promised.get("state") == "RECOVERED"
        if kept:
            facts.append("They promised to pay before, and they did.")
        else:
            facts.append(
                f"They promised to pay by {str(promised['promised_at'])[:10]} and have not yet."
            )

    offered = next((a for a in prior if a.get("approved_discount_percent")), None)
    if offered:
        facts.append(f"They were already offered {int(offered['approved_discount_percent'])}% off.")

    quote = _last_customer_utterance(customer_id, exclude_attempt_id)
    if quote:
        facts.append(f'Last time they said: "{quote}"')

    if not facts:
        # Prior attempts exist but produced nothing worth recalling - a
        # call that was never answered, say. The count alone is still worth
        # knowing, because a third unanswered call should sound different
        # from a first.
        facts.append(f"We have tried to reach them {len(prior)} time(s) before.")

    brief = "WHAT YOU ALREADY KNOW ABOUT THEM: " + " ".join(facts)
    if len(brief) > MAX_BRIEF_CHARS:
        brief = brief[: MAX_BRIEF_CHARS - 1].rstrip() + "…"
    return brief
