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
import httpx


def ipv4_transport() -> httpx.AsyncHTTPTransport:
    return httpx.AsyncHTTPTransport(local_address="0.0.0.0")


def ipv4_client(**kwargs) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ipv4_transport(), **kwargs)
