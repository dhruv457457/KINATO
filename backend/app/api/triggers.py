"""
Manual trigger routes. The old scripted "golden path" / "AI buyer" demo
triggers (fabricated customer, cart, product, and even a canned "it's too
expensive" transcript) were deleted - that was mock/seed content dressed up
as a live demo. The one real trigger that remains actually places a Twilio
call; there is currently no way to trigger a real recovery flow from the UI
until real event ingestion (checkout/webhook-driven) lands.
"""
from fastapi import APIRouter, Depends
from app.core.auth import get_current_merchant

trigger_router = APIRouter()


@trigger_router.post("/trigger/test-call")
async def trigger_test_call(current_merchant: dict = Depends(get_current_merchant)):
    """Places a real outbound Twilio call to CUSTOMER_PHONE (backend/.env) -
    for manually testing the voice agent, not a scripted demo."""
    from app.channels.voice_runtime import trigger_call_to_user
    return await trigger_call_to_user()
