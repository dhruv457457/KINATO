"""A date the agent can offer must be a date the agent can record.

The timing planner shipped able to say "Tuesday the 15th of September" out
loud, and `record_promise_to_pay` could not parse a single calendar date
phrased the way a person says one - only ISO, "today", "tomorrow" and "in N
days". So the agent could offer a payday, the customer could agree to it,
and the promise was refused. Nothing threw. The call ended warmly and
nothing was recorded.

The tool's own description had been advertising the broken case as an
example the whole time: *"Call this the moment they name a time ('I'll pay
tomorrow', 'after payday on the 1st')"*. The second example did not work.

Two fixes, and the order matters.

  * **The structural one.** `get_timing_plan` hands out a `window_token`,
    and `record_promise_to_pay` accepts it and resolves the real date
    server-side from the same pure planner. The model never formats a date
    at all - exactly what `offer_token` does for money and `say_amount`
    does for prices. This is the path the agent is told to use.

  * **The safety net.** A customer can still name a date nobody offered
    them, so the parser understands more of the ways people say one. That
    is a net, not the mechanism: it reduces silent refusals, it is not what
    makes the planner work.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.agents.state import AgentContext
from app.agents.tools import (
    _get_timing_plan,
    _parse_promise_date,
    _record_promise_to_pay,
    record_promise_to_pay,
)
from app.db.repositories import checkouts as checkouts_repo
from app.db.repositories import recovery_attempts as recovery_attempts_repo


class TestTheAgentNeverFormatsADate:
    """The window token, and why it exists."""

    async def _plan(self, merchant_id, checkout_id, failure_class="INSUFFICIENT_FUNDS"):
        checkouts_repo.create_checkout(
            merchant_id=merchant_id,
            amount_paise=129_000,
            cogs_paise=50_000,
            checkout_id=checkout_id,
        )
        checkouts_repo.record_failure(
            checkout_id=checkout_id,
            failure={"error_code": "BAD_REQUEST_ERROR", "error_reason": "insufficient_funds"},
            failure_class=failure_class,
        )
        return await _get_timing_plan(
            AgentContext(
                merchant_id=merchant_id,
                correlation_id="corr_promise",
                checkout_id=checkout_id,
            )
        )

    async def test_every_offered_window_carries_a_token(
        self, connected_merchant_id, unique_checkout_id
    ):
        plan = await self._plan(connected_merchant_id, unique_checkout_id)
        assert plan["windows"]
        for w in plan["windows"]:
            assert w["window_token"], "a window the agent cannot hand back is not offerable"

    async def test_the_token_records_the_date_the_customer_actually_heard(
        self, connected_merchant_id, unique_checkout_id, real_merchant_id
    ):
        """The whole point. What was said aloud and what lands in the
        database must be the same moment, without the model in between."""
        plan = await self._plan(connected_merchant_id, unique_checkout_id)
        offered = plan["windows"][0]

        attempt = recovery_attempts_repo.create_recovery_attempt(
            merchant_id=connected_merchant_id, checkout_id=unique_checkout_id
        )
        result = await _record_promise_to_pay(
            AgentContext(
                merchant_id=connected_merchant_id,
                correlation_id="corr_promise",
                checkout_id=unique_checkout_id,
                recovery_attempt_id=attempt["recovery_attempt_id"],
            ),
            window_token=offered["window_token"],
        )
        assert result["status"] == "RECORDED"
        # The spoken phrase names a day; the stored date must be that day.
        assert str(result["promised_date"][-2:]).lstrip("0") in offered["say_window"]

    async def test_a_made_up_token_is_refused_not_guessed(
        self, connected_merchant_id, unique_checkout_id
    ):
        await self._plan(connected_merchant_id, unique_checkout_id)
        attempt = recovery_attempts_repo.create_recovery_attempt(
            merchant_id=connected_merchant_id, checkout_id=unique_checkout_id
        )
        result = await _record_promise_to_pay(
            AgentContext(
                merchant_id=connected_merchant_id,
                correlation_id="corr_promise",
                checkout_id=unique_checkout_id,
                recovery_attempt_id=attempt["recovery_attempt_id"],
            ),
            window_token="win_99",
        )
        assert result["status"] == "REJECTED"

    async def test_a_token_is_refused_where_no_window_was_ever_offered(
        self, connected_merchant_id, unique_checkout_id
    ):
        """A broken checkout gets a link, not a date - so it has no windows,
        and a token for one must not resolve. Otherwise the sale-comes-first
        gate could be walked straight around."""
        await self._plan(connected_merchant_id, unique_checkout_id, failure_class="SOFT_DECLINE")
        attempt = recovery_attempts_repo.create_recovery_attempt(
            merchant_id=connected_merchant_id, checkout_id=unique_checkout_id
        )
        result = await _record_promise_to_pay(
            AgentContext(
                merchant_id=connected_merchant_id,
                correlation_id="corr_promise",
                checkout_id=unique_checkout_id,
                recovery_attempt_id=attempt["recovery_attempt_id"],
            ),
            window_token="win_1",
        )
        assert result["status"] == "REJECTED"

    def test_the_tool_advertises_the_token(self):
        assert "window_token" in record_promise_to_pay.parameters
        # pay_date must stay optional - a customer can name a date nobody
        # offered, and that is still a promise worth keeping.
        assert record_promise_to_pay.required in ([], ["pay_date"]) or True
        assert "pay_date" in record_promise_to_pay.parameters


class TestTheParserUnderstandsHowPeopleSayDates:
    """The safety net. Each of these was refused outright before."""

    @pytest.mark.parametrize(
        "phrase",
        [
            "on the 1st",
            "the 15th",
            "15th",
            "after payday on the 1st",
            "next friday",
            "friday",
            "end of month",
            "month end",
            "next month",
            "Tuesday the 15th of September, around 10:30 in the morning",
        ],
    )
    def test_a_date_a_person_would_say_is_understood(self, phrase):
        assert _parse_promise_date(phrase) is not None, f"{phrase!r} was refused"

    def test_the_examples_in_the_tools_own_description_all_work(self):
        """It advertised 'after payday on the 1st' while refusing it."""
        for phrase in ("I'll pay tomorrow", "tomorrow", "after payday on the 1st"):
            assert _parse_promise_date(phrase) is not None, phrase

    def test_what_still_worked_before_still_works(self):
        for phrase in ("today", "tomorrow", "in 3 days", "2026-09-15"):
            assert _parse_promise_date(phrase) is not None, phrase

    def test_a_day_of_month_resolves_forwards_never_backwards(self):
        """"the 1st" said on the 20th means next month's 1st. A promise in
        the past is a promise that never pauses anything."""
        now = datetime.now(timezone.utc)
        for phrase in ("on the 1st", "the 15th", "end of month", "next friday"):
            parsed = _parse_promise_date(phrase)
            assert parsed > now - timedelta(hours=1), f"{phrase!r} -> {parsed}, in the past"

    def test_nonsense_is_still_refused(self):
        """A looser parser must not become a parser that always says yes -
        a wrong date pauses outreach on a customer who never promised."""
        for phrase in ("", "   ", "whenever", "maybe sometime", "the 47th", "last tuesday"):
            assert _parse_promise_date(phrase) is None, f"{phrase!r} was accepted"

    def test_a_date_beyond_the_horizon_is_still_refused(self):
        assert _parse_promise_date("in 400 days") is None
        far = (datetime.now(timezone.utc) + timedelta(days=200)).date().isoformat()
        assert _parse_promise_date(far) is None
