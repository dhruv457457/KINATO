"""
Hard stops that must hold before Kinato ever contacts a customer.

These are deliberately separate from recovery_eligibility's checks. Those
decide whether a recovery *opportunity* exists at all; these decide whether
we may pick up the phone right now, for this attempt. They are the rules a
merchant is entitled to assume are enforced, and each returns a machine-
readable reason so a breach is countable rather than merely logged.

A "rule break" is any outreach that happened despite one of these being
true. The dashboard reports that count, and it must be zero.
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple

from app.db.repositories import policies as policies_repo
from app.db.repositories import checkouts as checkouts_repo
from app.db.repositories import recovery_attempts as recovery_attempts_repo

logger = logging.getLogger(__name__)

# India Standard Time. Calling hours are a courtesy to the CUSTOMER, so they
# must be evaluated in the customer's local time, not the server's - a
# container in Amsterdam calling an Indian customer at 04:00 IST because it
# was 22:30 UTC is exactly the breach this prevents.
IST = timezone(timedelta(hours=5, minutes=30))

def within_calling_hours(merchant_id: str, now: Optional[datetime] = None) -> Tuple[bool, str]:
    """merchant_policies.calling_start_hour/end_hour were editable in the
    dashboard and read by NOTHING. A merchant could set 10:00-20:00 and be
    called at any hour. Enforced here, in IST."""
    policy = policies_repo.get_policy(merchant_id)
    start = int(policy.get("calling_start_hour", 10))
    end = int(policy.get("calling_end_hour", 20))
    hour = (now or datetime.now(IST)).astimezone(IST).hour

    ok = start <= hour < end if start <= end else (hour >= start or hour < end)
    if not ok:
        return False, f"quiet_hours (IST hour {hour} outside {start}:00-{end}:00)"
    return True, ""


def not_already_paid(checkout_id: str) -> Tuple[bool, str]:
    """Re-checkable at any moment, including mid-call. A customer who pays
    while the phone is ringing must not then be sold to."""
    if checkouts_repo.is_paid(checkout_id):
        return False, "already_paid"
    return True, ""


def no_active_promise(checkout_id: str) -> Tuple[bool, str]:
    """A customer who said "I'll pay Friday" must not be called before Friday.

    Recording a promise is only meaningful if it actually stops outreach -
    otherwise it is a note in a database while the phone keeps ringing, which
    is worse than not asking at all.
    """
    with_promise = recovery_attempts_repo.active_promise_for_checkout(checkout_id)
    if with_promise:
        due = with_promise.get("promised_at")
        return False, f"promise_to_pay (customer committed to pay by {due})"
    return True, ""


# Every machine-readable stop code these guards can return. Listed so a
# counter can be initialised to zero for all of them: a stop reason that
# only appears in a report once it has fired is a reason nobody knows to
# look for, and "0 blocked on quiet hours" is a genuinely different
# statement from that row being absent.
STOP_CODES = (
    "already_paid",
    "quiet_hours",
    "max_calls_reached",
    "channel_cap_today",
    "promise_to_pay",
)


def stop_code(reason: str) -> str:
    """The machine-readable code from a guard's reason string.

    Guards return "quiet_hours (IST hour 4 outside 10:00-20:00)" - a code
    plus a human detail. Call sites were pulling the code out with
    `reason.split()[0]` inline, in two places, which is a parsing rule
    duplicated wherever anyone needs it and silently wrong the first time a
    code contains a space. One function, used everywhere.
    """
    return (reason or "").split()[0] if reason else ""


# One voice call and one email per case per rolling 24h, and no more than
# this many outreach attempts in total - unless the customer asked us to
# call back, which lifts the total by exactly one.
#
# The per-day limit and the lifetime limit answer different questions. A
# lifetime cap alone permits both attempts inside ten minutes; a daily cap
# alone permits a contact every day forever. A customer experiences both.
MAX_PER_CHANNEL_PER_DAY = 1
DEFAULT_MAX_OUTREACH_PER_CASE = 2


def under_outreach_cap(
    checkout_id: str,
    channel: str = "voice",
    max_total: int = DEFAULT_MAX_OUTREACH_PER_CASE,
) -> Tuple[bool, str]:
    """Contact limits, per channel per day and in total.

    Replaces under_call_cap, which counted CALLS only. That was correct
    while voice was the only way anyone was ever contacted; the moment
    email became a real channel it would have allowed two calls plus
    unlimited email while continuing to report that the cap was holding -
    the same shape as the opt-out that revoked one channel of two.
    """
    if recovery_attempts_repo.callback_requested(checkout_id):
        # They asked. That earns exactly one more attempt, not an exemption.
        max_total += 1

    total = recovery_attempts_repo.count_outreach_for_checkout(checkout_id)
    if total >= max_total:
        return False, f"max_calls_reached ({total}/{max_total} outreach attempts)"

    today = recovery_attempts_repo.count_recent_by_channel(checkout_id)
    on_this_channel = today.get(channel, 0)
    if on_this_channel >= MAX_PER_CHANNEL_PER_DAY:
        return False, (
            f"channel_cap_today ({on_this_channel} {channel} attempt(s) in the last 24h)"
        )
    return True, ""


def check_all(
    merchant_id: str,
    checkout_id: str,
    max_outreach: int = DEFAULT_MAX_OUTREACH_PER_CASE,
    now: Optional[datetime] = None,
    channel: str = "voice",
) -> Tuple[bool, str]:
    """Every pre-dial hard stop, in one call. Returns (allowed, reason).

    The limit is threaded through rather than merely accepted. After
    under_call_cap was superseded, this function still took a max_calls
    argument and quietly ignored it - a parameter a caller can set and be
    silently disobeyed by is worse than no parameter at all.

    `now` exists so a test or the scoreboard can pin the clock without
    reimplementing this sequence. The batch runner used to walk these
    guards itself, in its own order - a second copy of the rule that
    decides whether a customer may be contacted, free to drift from the
    one production uses. That is precisely the failure in FINDINGS #6,
    where the harness measured an agent that no longer existed.
    """
    for check in (
        lambda: not_already_paid(checkout_id),
        lambda: within_calling_hours(merchant_id, now=now),
        lambda: under_outreach_cap(checkout_id, channel=channel, max_total=max_outreach),
        lambda: no_active_promise(checkout_id),
    ):
        ok, reason = check()
        if not ok:
            return False, reason
    return True, ""
