"""
Tests the deterministic keyword-based fallback in
app/services/customer_intelligence.py._analyze_turn - the safety net that
must fire when the LLM call fails or times out, so a call is never left
unclassified. We monkeypatch the LLM call to fail so this is fast and
network-independent; a live-LLM smoke test lives separately in
backend/test_customer_intelligence.py (manual script, requires OPENROUTER_API_KEY).
"""
import httpx
import pytest

from app.services.customer_intelligence import CustomerIntelligenceService


@pytest.fixture(autouse=True)
def _force_llm_failure(monkeypatch):
    async def _raise(*args, **kwargs):
        raise httpx.ConnectTimeout("simulated LLM outage")
    monkeypatch.setattr(httpx.AsyncClient, "post", _raise)


@pytest.mark.parametrize(
    "transcript,expected_next_action",
    [
        ("It's too expensive, can you do anything on price?", "request_offer"),
        ("Yeah go ahead, send me the link", "send_checkout"),
        ("I'm busy right now, call me tomorrow", "schedule_callback"),
        ("Just looking around, nothing specific", "request_offer"),  # generic fallback
    ],
)
async def test_fallback_classification_is_never_empty(transcript, expected_next_action):
    result = await CustomerIntelligenceService._analyze_turn(transcript)
    assert result["next_action"] == expected_next_action
    assert result["temperature"] in ("HOT", "WARM", "COLD")
