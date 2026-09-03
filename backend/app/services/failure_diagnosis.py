"""
Why the payment actually failed, decided by code.

Razorpay tells us a great deal about a failed payment - `error_code`,
`error_reason`, `error_source`, `error_step`, `error_description`, and the
`method` that was attempted. Until now exactly one of those (`error_reason`)
travelled as far as the event bus, and nothing read even that. Every failed
payment produced the same call: a bank timeout, a stolen-card block and a
genuine "I don't have the money right now" were treated identically.

They are not remotely the same conversation.

The rule this module encodes existed already, as an English sentence in the
voice agent's system prompt:

    "A declined card, an expired session, or a checkout that errored is NOT
     a price objection. Those customers want to pay you the full amount;
     they just need a working link."

That is correct, and it is also exactly the kind of guarantee this codebase
otherwise refuses to leave in a prompt. FINDINGS #1 is the story of a prompt
rule that was subtly against the merchant's interest and cost real margin
before anyone noticed, because nothing failed. So the rule moves here, where
it is a table and a `check_offer` refusal rather than a paragraph the model
is trusted to have internalised.

Two deliberate design points:

  * This is pure. No LLM, no I/O, no database. Given the same failure object
    it returns the same class forever, which is what makes it testable and
    what makes the resulting refusal explainable to a merchant.
  * The model never sees the raw failure object. It receives a class and one
    plain sentence (see `describe`). Handing a model a JSON blob and hoping
    it draws the right conclusion is how you end up with a "bank timeout"
    being negotiated as a price objection.
"""
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# --- The classes -------------------------------------------------------
# Named for what they mean to the CONVERSATION, not for Razorpay's internal
# taxonomy - the whole value here is the translation.

SOFT_DECLINE = "SOFT_DECLINE"
HARD_DECLINE = "HARD_DECLINE"
AUTH_DROP = "AUTH_DROP"
INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"
USER_ABANDON = "USER_ABANDON"
RAIL_DOWN = "RAIL_DOWN"
UNKNOWN = "UNKNOWN"

# The classes where a discount is the wrong instrument entirely, because
# the customer never objected to the price - their payment broke. Offering
# money off here gives away margin on a sale that was already won, which is
# FINDINGS #1 measured in rupees.
FULL_PRICE_FIRST_CLASSES = frozenset({SOFT_DECLINE, HARD_DECLINE, AUTH_DROP})

# The classes where price or timing genuinely might be the barrier, so the
# agent may go looking for one.
PRICE_MAY_BE_THE_BARRIER = frozenset({INSUFFICIENT_FUNDS, USER_ABANDON, UNKNOWN})


@dataclass(frozen=True)
class Diagnosis:
    failure_class: str
    # One plain sentence for the model. Never the raw failure object.
    summary: str
    # What the pipeline should do before any conversation happens.
    default_move: str
    # True when this instrument should not simply be retried as-is.
    retry_same_instrument: bool = True


# Razorpay's own vocabulary, lowercased for matching. Kept as substring
# checks rather than exact codes on purpose: the gateway's error strings
# vary by issuer and acquirer, and a classifier that only recognises the
# exact strings we happened to see in test mode would silently degrade to
# UNKNOWN in production - the failure mode this module exists to prevent.
_HARD_DECLINE_MARKERS = (
    "pickup_card", "pickup card", "stolen", "lost_card", "lost card",
    "do_not_honour", "do not honour", "do_not_honor", "card_blocked",
    "restricted_card", "fraud", "suspected",
)
_SOFT_DECLINE_MARKERS = (
    "timeout", "timed out", "issuer_down", "issuer down", "bank_down",
    "gateway_error", "gateway error", "server_error", "service_unavailable",
    "temporarily", "try_again", "try again",
)
_INSUFFICIENT_MARKERS = (
    "insufficient_funds", "insufficient funds", "insufficient_balance",
    "insufficient balance", "exceeds_balance", "limit_exceeded",
)


def _haystack(failure: Dict[str, Any]) -> str:
    """Everything Razorpay said about the failure, as one lowercase string.

    Matching across all of the fields together rather than each in turn is
    deliberate: which field carries the useful words differs by payment
    method (a card decline names it in `error_reason`, a UPI failure often
    only in `error_description`), and a classifier that reads the wrong
    field returns UNKNOWN with total confidence.
    """
    return " ".join(
        str(failure.get(k) or "")
        for k in ("error_code", "error_reason", "error_description", "error_source", "error_step")
    ).lower()


def diagnose(failure: Optional[Dict[str, Any]], rail_degraded: bool = False) -> Diagnosis:
    """Classify one failed payment.

    `failure` is the fields lifted off Razorpay's payment entity (see
    app/payments/webhooks.py). None or empty means there was no failed
    payment at all - the customer simply walked away mid-checkout, which is
    a genuinely different situation and the only one where "so what stopped
    you?" is the right opening.
    """
    # The rail being down outranks everything else the payment object says.
    # A failure recorded during a Razorpay outage tells us about Razorpay,
    # not about the customer or their bank, and calling someone about it
    # would be blaming them for our own outage.
    if rail_degraded:
        return Diagnosis(
            failure_class=RAIL_DOWN,
            summary="This payment failed during a reported Razorpay outage, so the failure is very likely ours rather than theirs.",
            default_move="hold outreach until the rail recovers",
            retry_same_instrument=True,
        )

    # ANY field present means a payment failed, even if what it says is
    # useless to us. Only a completely empty object is a walk-away.
    # error_source belongs in this list: without it, a payment.failed whose
    # error object was sparse got classified USER_ABANDON, and USER_ABANDON
    # tells the agent "nothing failed" - which is a plainly false thing to
    # say to someone whose card had just been declined. An unhelpful
    # failure object should make us say less (UNKNOWN), never say something
    # untrue.
    if not failure or not any(
        failure.get(k)
        for k in ("error_code", "error_reason", "error_description", "error_step", "error_source")
    ):
        return Diagnosis(
            failure_class=USER_ABANDON,
            summary="They started checking out and left without a payment failing, so nothing is known to be broken.",
            default_move="ask what stopped them, then a policy-gated offer",
        )

    text = _haystack(failure)
    step = str(failure.get("error_step") or "").lower()

    # 3DS / OTP abandoned. Checked before the decline markers because an
    # authentication drop often carries a generic decline-ish description
    # while being a completely different situation: nothing was declined,
    # the customer simply never finished the bank's own step.
    if "authentication" in step:
        return Diagnosis(
            failure_class=AUTH_DROP,
            summary="They got as far as their bank's verification step and it didn't complete, so the payment never went through.",
            default_move="send a fresh link for the same amount",
        )

    if any(m in text for m in _INSUFFICIENT_MARKERS):
        return Diagnosis(
            failure_class=INSUFFICIENT_FUNDS,
            summary="Their payment was declined for insufficient funds, so this may genuinely be about money or timing.",
            default_move="conversation allowed; a discount or a later date may both be legitimate",
        )

    if any(m in text for m in _HARD_DECLINE_MARKERS):
        return Diagnosis(
            failure_class=HARD_DECLINE,
            summary="Their bank refused this card outright, so the same card is not going to work.",
            default_move="full-price link; do not retry the same instrument",
            retry_same_instrument=False,
        )

    if any(m in text for m in _SOFT_DECLINE_MARKERS):
        return Diagnosis(
            failure_class=SOFT_DECLINE,
            summary="Their payment failed on a temporary bank or gateway problem, not on anything they did.",
            default_move="full-price link; a retry will very likely just work",
        )

    # A real decline we cannot place more precisely. Treated as SOFT rather
    # than UNKNOWN deliberately: something demonstrably broke in the
    # payment, and the safe reading of "a payment broke" is that the
    # customer still wants to pay. UNKNOWN is reserved for "we have no
    # failure information at all", where price genuinely might be the
    # issue and the agent should be free to ask.
    if step == "payment" or "declined" in text or "failed" in text:
        return Diagnosis(
            failure_class=SOFT_DECLINE,
            summary="Their payment was declined, and nothing suggests the price was the problem.",
            default_move="full-price link",
        )

    logger.info(f"failure_diagnosis: unrecognised failure shape, classifying UNKNOWN: {text[:200]!r}")
    return Diagnosis(
        failure_class=UNKNOWN,
        summary="Their payment didn't complete, and we don't know why.",
        default_move="ask what stopped them, then a policy-gated offer",
    )


def describe(
    failure_class: Optional[str],
    emi_available: bool = False,
    barrier_confirmed: bool = False,
) -> str:
    """The one line the agent's system prompt gets. Empty when there is
    nothing useful to say, so the prompt simply omits the section rather
    than asserting something hollow.

    `emi_available` adds instalments PROACTIVELY to the one class where the
    barrier is genuinely cashflow rather than price, and ON REQUEST to every
    other class - never volunteered there, but answerable when asked. It is
    a parameter and not a constant because EMI has to be enabled on the merchant's own Razorpay
    account: offering instalments a checkout cannot provide tells a customer
    something untrue about their money, which is the failure this codebase
    keeps finding. Defaults False, so silence is what happens when nobody
    has said otherwise.
    """
    if not failure_class or failure_class == UNKNOWN:
        return ""
    line = _PROMPT_LINES.get(failure_class, "")
    # Only INSUFFICIENT_FUNDS. A declined card is not a money problem, and
    # someone who simply wandered off has not said anything about price -
    # offering either of them instalments answers a question they did not
    # ask. This is the same restraint as not pushing UPI at a walk-away.
    if line and emi_available and failure_class == INSUFFICIENT_FUNDS:
        line = f"{line} {_EMI_LINE}"
    elif line and emi_available and barrier_confirmed:
        # Only once the customer has RAISED cost themselves.
        #
        # A live SOFT_DECLINE call asked for EMI twice and was handed 7% off
        # instead, because nothing in the prompt said instalments existed -
        # the expensive instrument reached for because the cheap one was
        # unmentioned. So the agent has to know. But telling it on every
        # call and adding "do not volunteer this" is a promise in prose, and
        # this session has twice watched the model walk past exactly that
        # kind of sentence.
        #
        # So it is gated on the same signal the discount gate uses: the
        # barrier the customer confirmed in their own words. Before that the
        # agent cannot mention instalments because it has not been told they
        # exist, which is a mechanism; after it, mentioning them is precisely
        # what the customer asked for.
        line = f"{line} {_EMI_ON_REQUEST_LINE}"
    return line


# Instalments, offered before money off.
#
# "I can't afford that right now" is a CASHFLOW objection, and a discount is
# the most expensive possible answer to it: the merchant loses margin on a
# sale EMI would have closed at full revenue. The policy engine only knows
# one instrument - a discount percent - so the cheaper instrument has to be
# reached for here, in what the agent says, before the expensive one is
# reached for in what it does.
#
# Deliberately phrased as "if that helps", not as a pitch. Someone who has
# just been told they have insufficient funds does not need to be sold to.
_EMI_LINE = (
    "This merchant offers EMI, so if the issue is paying it all at once, say they can split it into "
    "monthly instalments on the same link - offer that BEFORE any discount, because it costs them "
    "nothing and you keep the full sale."
)

# EMI exists, but must not be VOLUNTEERED outside INSUFFICIENT_FUNDS - the
# restraint above still holds. This line exists so the agent is not ignorant
# of instalments when the customer raises cost or asks for EMI outright.
# On a real SOFT_DECLINE call the customer asked for EMI twice and was handed
# 7% off instead, because nothing in the prompt said instalments existed at
# all - the expensive instrument reached for because the cheap one was unmentioned.
_EMI_ON_REQUEST_LINE = (
    "This merchant offers EMI. Do not volunteer it, but if the customer raises the cost or asks about "
    "instalments, tell them the same link can be split into monthly instalments - and say that BEFORE "
    "offering any discount, because it costs nothing and keeps the full sale."
)

_PROMPT_LINES = {
    SOFT_DECLINE: (
        "WHY THIS FAILED: their payment was declined by a temporary bank or gateway problem - nothing they did, "
        "and not the price. They want to pay you. Send a working link at the full amount. Do not offer a discount. "
        "If they sound unsure about the same card, mention the link also takes UPI."
    ),
    HARD_DECLINE: (
        "WHY THIS FAILED: their bank refused that card outright, so that card will NOT work again - sending the "
        "same link and saying nothing wastes their time twice. This is not a price objection. Send a working link "
        "at the full amount and tell them plainly to use UPI or a different card."
    ),
    AUTH_DROP: (
        "WHY THIS FAILED: they reached their bank's verification step and it did not complete. Nothing was declined "
        "and nothing was charged. Send a fresh link for the same amount. Do not offer a discount. UPI skips that "
        "verification step entirely, so it is worth mentioning."
    ),
    INSUFFICIENT_FUNDS: (
        "WHY THIS FAILED: their payment was declined for insufficient funds. This one really might be about money "
        "or timing, so it is fair to ask - and if they say they will pay later, record that rather than pushing."
    ),
    USER_ABANDON: (
        "WHY THIS FAILED: nothing failed. They started checking out and did not finish, so ask openly what stopped "
        "them before assuming anything."
    ),
    RAIL_DOWN: (
        "WHY THIS FAILED: Razorpay itself was having an outage. This was our problem, not theirs - do not imply "
        "otherwise."
    ),
}
