import asyncio
import logging
import uuid
from typing import Callable, Dict, List, Any, Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

class EventBus:
    """
    Kinato Central Event Bus (Robust).
    Routes events using explicit event_ids, correlation_ids, and idempotency_keys.
    """
    def __init__(self):
        self._subscribers: Dict[str, List[Callable]] = {}
        self._event_log: List[Dict[str, Any]] = []
        self._processed_idempotency_keys = set() # Simple idempotency store for demo
        # When True, every publish() also durably persists to the `events`
        # table (fire-and-forget, never blocks/raises into the publisher).
        # Tests set this False (see tests/conftest.py) so the 30-test suite
        # stays fast and doesn't depend on a live database.
        self.persist: bool = True

    def subscribe(self, event_type: str, callback: Callable):
        """Register an async callback for a specific event type."""
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        self._subscribers[event_type].append(callback)
        logger.debug(f"Subscribed {callback.__name__} to {event_type}")

    async def publish(self,
                      event_type: str,
                      payload: Dict[str, Any],
                      correlation_id: str,
                      merchant_id: str,
                      idempotency_key: Optional[str] = None):
        """Publish a fully traceable event asynchronously.

        Idempotency dedup: when self.persist is True (the real app, never
        tests), a given idempotency_key is checked and claimed against the
        `events` table's UNIQUE constraint - the durable source of truth -
        BEFORE subscribers run, and the whole publish is dropped on a
        conflict. This closes a real gap the old design had: an in-memory
        set() alone forgets every key on a process restart, and a Railway
        redeploy landing between two Razorpay webhook retries (Razorpay
        retries on any non-2xx or timeout) could otherwise double-fire
        payment.succeeded and double-count recovered revenue - the exact
        number the batch recovery report claims to measure precisely. When
        self.persist is False (tests - see conftest.py), the in-memory set
        is used instead, keeping the suite fast and DB-independent."""

        already_persisted = False
        if idempotency_key:
            if self.persist:
                claimed = await self._claim_idempotency_key(event_type, payload, merchant_id, correlation_id, idempotency_key)
                if not claimed:
                    logger.warning(f"Idempotency key {idempotency_key} already processed (durable check). Dropping event: {event_type}")
                    return
                already_persisted = True  # _claim_idempotency_key's persist_event() already inserted this row
            else:
                if idempotency_key in self._processed_idempotency_keys:
                    logger.warning(f"Idempotency key {idempotency_key} already processed. Dropping event: {event_type}")
                    return
                self._processed_idempotency_keys.add(idempotency_key)

        event = {
            "event_id": f"evt_{uuid.uuid4().hex[:12]}",
            "correlation_id": correlation_id,
            "event_type": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "merchant_id": merchant_id,
            "idempotency_key": idempotency_key,
            "payload": payload
        }

        self._event_log.append(event)
        logger.info(f"EventBus Published: {event_type} | Correlation: {correlation_id}")

        if self.persist and not already_persisted:
            asyncio.create_task(self._persist(event))

        if event_type in self._subscribers:
            for callback in self._subscribers[event_type]:
                asyncio.create_task(self._safe_execute(callback, event))

    async def _claim_idempotency_key(
        self, event_type: str, payload: Dict[str, Any], merchant_id: str, correlation_id: str, idempotency_key: str
    ) -> bool:
        """Atomically claims idempotency_key via the events table's UNIQUE
        constraint, ahead of subscriber dispatch. Returns True if this call
        won the claim (first time seen), False if it was already claimed. If
        persistence itself is unreachable, fails open (returns True) rather
        than silently dropping every event with an idempotency_key whenever
        the DB has a hiccup - the in-memory event log/subscribers still need
        to run for the app to function; a DB outage is a separate, visible
        failure mode (see database.py's own logging), not one this dedup
        check should mask by dropping real events."""
        try:
            from app.db.database import run_db_async
            from app.db.repositories.events import persist_event
            return await run_db_async(persist_event, event_type, payload, merchant_id, correlation_id, idempotency_key)
        except Exception as e:
            logger.warning(f"Idempotency claim failed open for {event_type} (treating as first-seen): {e}")
            return True

    async def _persist(self, event: Dict[str, Any]):
        """Fire-and-forget durable write. Deliberately swallows all errors -
        a persistence failure must never affect the in-memory bus's delivery
        to subscribers, which is the hot path."""
        try:
            from app.db.database import run_db_async
            from app.db.repositories.events import persist_event
            await run_db_async(
                persist_event,
                event["event_type"],
                event["payload"],
                event.get("merchant_id"),
                event.get("correlation_id"),
                event.get("idempotency_key"),
            )
        except Exception as e:
            logger.warning(f"Event persistence failed for {event['event_type']} (non-fatal): {e}")

    async def _safe_execute(self, callback: Callable, event: Dict[str, Any]):
        """Executes a subscriber callback and catches any exceptions to prevent bus crashes."""
        try:
            await callback(event)
        except Exception as e:
            logger.error(f"Error in subscriber {callback.__name__} for event {event['event_type']}: {str(e)}", exc_info=True)

    def get_recent_events(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Fetch the most recent events for the Merchant Command Center UI."""
        return self._event_log[-limit:]

    def has_matching_event(self, event_type: str, match_fn: Callable[[Dict[str, Any]], bool]) -> bool:
        """
        Scans the full event log for an event of the given type whose payload satisfies
        match_fn. This is the authoritative re-check used to close race windows (e.g.
        confirming a payment already succeeded before firing an abandonment event) - since
        this system is event-sourced in-memory rather than backed by a database.
        """
        return any(
            e["event_type"] == event_type and match_fn(e.get("payload", {}))
            for e in self._event_log
        )

# Singleton instance for demo purposes. 
# In a true distributed production system, this would wrap Redis Streams.
bus = EventBus()
