"""
Forces outbound HTTP calls onto IPv4. Diagnosed live: intermittent
`httpx.ConnectTimeout` to api.elevenlabs.io (and, less often, openrouter.ai)
that never reproduced in an isolated quick test but reproduced consistently
under sustained network activity - the textbook signature of a machine
whose IPv6 route is present but broken/black-holed, so the OS tries IPv6
first, stalls out, and only sometimes recovers via IPv4 in time.

`local_address="0.0.0.0"` makes httpcore bind the outbound socket to an
IPv4 wildcard address, which forces IPv4 resolution/connection - no IPv6
attempt happens at all. Use this transport for every outbound call to an
external API (ElevenLabs, OpenRouter/OpenAI, etc.) from this backend.
"""
import asyncio
import logging
from typing import Any, Dict, Tuple

import httpx

logger = logging.getLogger(__name__)

# Connection reuse, keyed by (event loop, purpose).
#
# Every call to ipv4_client() used to build a brand-new transport with its
# own empty connection pool, so every OpenRouter call and every ElevenLabs
# call paid DNS + TCP + TLS before it could send a byte - inside a 2.0s TTS
# budget and, on the turn that matters, inside a budget that was already
# out of room.
#
# Keyed by event loop, not module-global, on purpose: an httpx client binds
# its pool to the loop that first used it, and the test suite runs a fresh
# loop per test. A single module-level client would work in production and
# raise "Event loop is closed" on the second test.
_SHARED_CLIENTS: Dict[Tuple[int, str], httpx.AsyncClient] = {}

_DEFAULT_LIMITS = httpx.Limits(max_connections=20, max_keepalive_connections=10, keepalive_expiry=60.0)


def ipv4_transport() -> httpx.AsyncHTTPTransport:
    return httpx.AsyncHTTPTransport(local_address="0.0.0.0")


def ipv4_client(**kwargs) -> httpx.AsyncClient:
    """A NEW client. Prefer shared_ipv4_client() on any hot path."""
    return httpx.AsyncClient(transport=ipv4_transport(), **kwargs)


def shared_ipv4_client(purpose: str, **kwargs: Any) -> httpx.AsyncClient:
    """A long-lived, connection-reusing client for `purpose`.

    Callers must NOT close it or use it as a context manager - it outlives
    any single request by design. `purpose` separates pools so a slow TTS
    call cannot queue behind the model's connections.

    The IPv4 pin is not optional and is not a default worth overriding: see
    this module's docstring for the black-holed-IPv6 failure it exists to
    prevent.
    """
    try:
        loop_key = id(asyncio.get_running_loop())
    except RuntimeError:
        # No loop yet (import time, a sync caller). Don't cache something we
        # cannot key correctly - hand back a throwaway.
        return ipv4_client(**kwargs)

    key = (loop_key, purpose)
    client = _SHARED_CLIENTS.get(key)
    if client is None or client.is_closed:
        kwargs.setdefault("limits", _DEFAULT_LIMITS)
        client = httpx.AsyncClient(transport=ipv4_transport(), **kwargs)
        _SHARED_CLIENTS[key] = client
    return client


async def close_shared_clients() -> None:
    """Close every shared client. Called from the app lifespan on shutdown."""
    for key, client in list(_SHARED_CLIENTS.items()):
        _SHARED_CLIENTS.pop(key, None)
        try:
            await client.aclose()
        except Exception as e:
            logger.debug(f"Closing shared HTTP client {key} failed (non-fatal): {e}")
