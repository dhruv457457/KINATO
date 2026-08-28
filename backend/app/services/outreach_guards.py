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

DEFAULT_MAX_CALLS_PER_CASE = 2


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


def under_call_cap(checkout_id: str, max_calls: int = DEFAULT_MAX_CALLS_PER_CASE) -> Tuple[bool, str]:
    """Caps how many times one checkout may be called. Without this a
    customer who never answers is dialled indefinitely, which is both a
    nuisance and a real compliance problem."""
    prior = recovery_attempts_repo.count_calls_for_checkout(checkout_id)
    if prior >= max_calls:
        return False, f"max_calls_reached ({prior}/{max_calls})"
    return True, ""


def not_already_paid(checkout_id: str) -> Tuple[bool, str]:
    """Re-checkable at any moment, including mid-call. A customer who pays
    while the phone is ringing must not then be sold to."""
    if checkouts_repo.is_paid(checkout_id):
        return False, "already_paid"
    return True, ""


def check_all(merchant_id: str, checkout_id: str, max_calls: int = DEFAULT_MAX_CALLS_PER_CASE) -> Tuple[bool, str]:
    """Every pre-dial hard stop, in one call. Returns (allowed, reason)."""
    for check in (
        lambda: not_already_paid(checkout_id),
        lambda: within_calling_hours(merchant_id),
        lambda: under_call_cap(checkout_id, max_calls),
    ):
        ok, reason = check()
        if not ok:
            return False, reason
    return True, ""
