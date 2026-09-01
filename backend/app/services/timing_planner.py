"""
When to try again, decided by code.

Every recovery this system has ever run had exactly one moment available to
it: now. A payment fails, the sweeper notices, and the customer is contacted
whenever that happened to be. For a bank timeout at 11am that is right. For
"I don't have the money until the 1st" on the 28th it is precisely wrong -
and no amount of conversational skill fixes a call placed three days too
early.

So the moments move here, next to the discount ceiling and the calling
hours, for the same reason those did: a merchant is entitled to assume
timing is a rule rather than a mood.

Two design points that everything else follows from.

  * **This is pure.** No database, no network, no LLM. Given the same
    failure and the same clock it returns the same windows forever, which is
    what makes it testable and what makes a suggestion explainable to a
    merchant who asks why.

  * **It is anchored on `failed_at`, never on `now()`.** A plan computed the
    moment a payment failed and recomputed a fortnight later is the same
    plan. That is what lets the windows be derived wherever they are needed
    instead of written to a table - and a plan that is never stored is a
    plan that can never disagree with the row it came from. `now` is passed
    in for exactly one purpose: marking which windows have already gone by.

**What this module does not do is act.** Nothing here contacts anybody,
schedules anything, or moves money. It produces a suggestion. The agent may
offer it, the merchant may read it, and the only thing that ever puts a
future commitment into the system is the customer agreeing to a date - which
goes through `record_promise_to_pay`, exactly as it did before this file
existed. That boundary is deliberate: a planner that could dispatch would be
a second way to contact a customer, and every stopping rule in
`outreach_guards` would need re-proving against it.
"""
import calendar
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional

from app.services.failure_diagnosis import (
    HARD_DECLINE,
    INSUFFICIENT_FUNDS,
    RAIL_DOWN,
    SOFT_DECLINE,
)
from app.services.outreach_guards import IST

logger = logging.getLogger(__name__)


# How far out a suggestion may reach. Past about a week a recovery window
# has closed on its own - the customer has bought elsewhere, or forgotten
# the cart entirely - and a date that far away reads as a brush-off rather
# than a plan.
HORIZON_DAYS = 7

# Except when the customer has told us the date themselves. "I get paid on
# the 1st" said on the 2nd means waiting twenty-nine days, and waiting is
# the correct answer - it is the one objection money off does not solve.
# Holding INSUFFICIENT_FUNDS to a week made payday_proximity structurally
# unreachable: every payday it could name fell outside the horizon and was
# dropped, so the class that most needed timing was the one class that
# never got any.
PAYDAY_HORIZON_DAYS = 35

# Three is enough to show a plan exists and few enough to say out loud. A
# list of six dates is not a plan, it is a timetable nobody will follow.
MAX_WINDOWS = 3

# Where in the calling window to aim. The merchant's opening hour plus half
# an hour: safely inside the window rather than balanced on its edge, where
# a few minutes of drift would put the call outside the merchant's own
# policy.
MINUTES_PAST_OPENING = 30

# A transient failure deserves a retry soon, but not immediately. An issuer
# timeout retried three seconds later fails three seconds later.
QUICK_RETRY_HOURS = 6

# Salary lands around these dates for most salaried customers in India. This
# is a heuristic about the calendar and it is treated as one - it decides
# which day we SUGGEST, and it never becomes a claim spoken to a customer
# about why that day is better.
PAYDAY_DAYS = (1, 15)

# Tue/Wed/Thu. Monday carries the weekend's backlog and Friday empties out.
MIDWEEK = (1, 2, 3)


@dataclass(frozen=True)
class Window:
    """One suggested moment.

    `say_window` and `say_reason` are finished phrases, handed over ready to
    read for the same reason `say_amount` is: a model asked to format a date
    in the middle of a spoken sentence will sometimes get it wrong, and
    there is no prompt that makes it reliably right. The machine-readable
    `at` stays for the dashboard and the audit trail; it is simply not the
    thing the agent is told to speak.
    """
    at: datetime
    reason_code: str
    say_window: str
    say_reason: str
    is_past: bool = False


def _round_the_clock(start: int, end: int) -> bool:
    """The two ways a merchant expresses "any time", per within_calling_hours.

    `start == end` is the one that matters. A merchant setting 0 and 0 means
    midnight to midnight; read naively the arithmetic says `0 <= hour < 0`,
    which is never, and they would have switched calling off while believing
    they had switched it fully on.
    """
    return start == end or (start == 0 and end >= 24)


def _inside(hour: int, start: int, end: int) -> bool:
    if _round_the_clock(start, end):
        return True
    # An overnight window (22:00-06:00) wraps midnight and cannot be
    # expressed as a single comparison.
    return start <= hour < end if start <= end else (hour >= start or hour < end)


def _clamp(moment: datetime, start: int, end: int) -> datetime:
    """Move a moment into the merchant's calling hours, never out of them.

    Always forwards. Pulling a suggestion backwards could land it before the
    failure it is a response to, which is how you suggest a time that has
    already passed.
    """
    if _inside(moment.hour, start, end):
        return moment
    opening = moment.replace(hour=start % 24, minute=MINUTES_PAST_OPENING, second=0, microsecond=0)
    if opening <= moment:
        opening += timedelta(days=1)
    return opening


def _opening_on(day: datetime, start: int) -> datetime:
    return day.replace(hour=start % 24, minute=MINUTES_PAST_OPENING, second=0, microsecond=0)


def _next_midweek_openings(after: datetime, start: int, count: int) -> List[datetime]:
    out: List[datetime] = []
    cursor = after
    for _ in range(14):
        if len(out) >= count:
            break
        cursor = cursor + timedelta(days=1)
        if cursor.weekday() in MIDWEEK:
            out.append(_opening_on(cursor, start))
    return out


def _next_paydays(after: datetime, start: int, count: int) -> List[datetime]:
    """The next salary dates strictly after the failure.

    Month-end is included alongside the 1st and 15th because it is the third
    common payroll date, and because a failure on the 28th should not have to
    wait until the 1st when the 30th may serve.
    """
    out: List[datetime] = []
    cursor = after
    for _ in range(70):
        if len(out) >= count:
            break
        cursor = cursor + timedelta(days=1)
        last_day = calendar.monthrange(cursor.year, cursor.month)[1]
        if cursor.day in PAYDAY_DAYS or cursor.day == last_day:
            out.append(_opening_on(cursor, start))
    return out


def _ordinal(day: int) -> str:
    """1st, 2nd, 3rd, 4th ... 21st. "the 3th of September" is a machine
    talking, and this phrase is read aloud to a customer."""
    if 11 <= day % 100 <= 13:
        return f"{day}th"
    return f"{day}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th') }".replace(" ", "")


def _say_when(moment: datetime) -> str:
    """A date a person would say out loud. Never an ISO timestamp."""
    hour = moment.hour
    if hour < 12:
        part = "in the morning"
        clock = f"{hour if hour else 12}:{moment.minute:02d}"
    elif hour < 17:
        part = "in the afternoon"
        clock = f"{hour - 12 if hour > 12 else 12}:{moment.minute:02d}"
    else:
        part = "in the evening"
        clock = f"{hour - 12}:{moment.minute:02d}"
    return (
        f"{moment.strftime('%A')} the {_ordinal(moment.day)} of "
        f"{moment.strftime('%B')}, around {clock} {part}"
    )


# Descriptive only. Each of these says something about the CALENDAR or about
# the merchant's own settings - facts we hold. None of them explains how a
# bank, an issuer or a payment rail behaves, because we have no evidence for
# that and a confident invented justification to a customer is the exact
# failure this codebase keeps finding. See test_timing_planner's forbidden
# vocabulary for the enforced version of this paragraph.
_REASONS = {
    "transient_quick_retry": "a few hours from now, once the checkout has had time to settle",
    "business_hours": "inside the hours this merchant takes calls",
}


def _payday_reason(moment: datetime) -> str:
    """One reason_code, three different dates - so the phrase has to be
    derived from the date rather than fixed to the code.

    Caught by reading the output rather than by a test: a window on the 15th
    was being described as "just after the usual end-of-month date", which
    is simply false, said to a customer, with total confidence. Every phrase
    here is a statement about the calendar and nothing else.
    """
    last_day = calendar.monthrange(moment.year, moment.month)[1]
    if moment.day == 1:
        return "just after the 1st of the month"
    if moment.day == 15:
        return "just after the middle of the month"
    if moment.day == last_day:
        return "just after the end of the month"
    return "inside the hours this merchant takes calls"


def plan_windows(
    failure_class: Optional[str],
    failed_at: datetime,
    calling_start_hour: int = 10,
    calling_end_hour: int = 20,
    now: Optional[datetime] = None,
) -> List[Window]:
    """Suggest when this failure is worth another approach.

    Returns an empty list when the answer is "never" - which is a real
    answer, and one worth showing a merchant rather than hiding.
    """
    # A hard decline does not become payable by waiting. `retry_same_
    # instrument=False` has been computed by failure_diagnosis since it
    # shipped and read by nothing; this is the thing that finally reads it.
    # Suggesting a time here would imply the card might work later. It will
    # not, and a retried fraud block is a signal against the merchant.
    if failure_class == HARD_DECLINE:
        return []

    failed_at = failed_at.astimezone(IST)
    now = (now or datetime.now(IST)).astimezone(IST)
    start, end = int(calling_start_hour), int(calling_end_hour)

    candidates: List[tuple] = []

    if failure_class in (SOFT_DECLINE, RAIL_DOWN):
        # Rounded to the next half hour. This one window is derived by
        # arithmetic on the failure rather than from a calendar date, so
        # without rounding it inherits the exact minute the payment broke -
        # and "around 4:17 in the afternoon" is not a time anybody offers
        # somebody. Every other window already lands on :30 by construction.
        quick = failed_at + timedelta(hours=QUICK_RETRY_HOURS)
        quick = quick.replace(second=0, microsecond=0)
        quick += timedelta(minutes=(-quick.minute) % 30)
        candidates.append((quick, "transient_quick_retry"))
        candidates += [(m, "business_hours") for m in _next_midweek_openings(failed_at, start, 2)]
    elif failure_class == INSUFFICIENT_FUNDS:
        # Paydays only, deliberately. A midweek courtesy call to someone who
        # has no money until the 15th is the pointless contact this module
        # exists to prevent, and sorted by date it would displace the one
        # window that matters.
        candidates += [(m, "payday_proximity") for m in _next_paydays(failed_at, start, 3)]
    else:
        # AUTH_DROP, USER_ABANDON, UNKNOWN and anything unclassified. These
        # people did not hit a wall the clock can fix, so there is nothing
        # cleverer to do than reach them at a civil hour.
        candidates += [(m, "business_hours") for m in _next_midweek_openings(failed_at, start, 3)]

    horizon_days = PAYDAY_HORIZON_DAYS if failure_class == INSUFFICIENT_FUNDS else HORIZON_DAYS
    horizon = failed_at + timedelta(days=horizon_days)
    windows: List[Window] = []
    seen = set()
    for moment, reason in sorted(candidates, key=lambda c: c[0]):
        moment = _clamp(moment, start, end)
        if moment <= failed_at or moment > horizon or moment in seen:
            continue
        seen.add(moment)
        windows.append(
            Window(
                at=moment,
                reason_code=reason,
                say_window=_say_when(moment),
                say_reason=(
                    _payday_reason(moment) if reason == "payday_proximity" else _REASONS[reason]
                ),
                is_past=moment < now,
            )
        )
        if len(windows) >= MAX_WINDOWS:
            break

    return windows
