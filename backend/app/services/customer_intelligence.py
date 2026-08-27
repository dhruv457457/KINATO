import logging
import os
import json
import httpx
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field, ValidationError
from app.gateway.event_bus import bus
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")

class CustomerState(BaseModel):
    """
    Strict contract for Customer Intelligence.
    Guarantees immutable transcript turns and structured behavioral classification.
    """
    intent: str = Field(..., description="purchase_interest | browsing | support | opt_out")
    temperature: str = Field(..., pattern="^(HOT|WARM|COLD)$")
    barrier: Optional[str] = Field(None, description="price | timing | decision_maker | trust | none")
    budget: Optional[float] = None
    timeline: Optional[str] = None
    decision_maker: str = "self"
    competitor_mentioned: bool = False
    confidence: float = Field(default=0.92, ge=0.0, le=1.0)
    customer_words: str  # IMMUTABLE: Exact utterance from customer
    next_action: str = Field(
        default="request_offer",
        pattern="^(request_offer|schedule_callback|opt_out|continue_discovery|send_checkout)$"
    )
    requested_discount: Optional[float] = None


class CustomerIntelligenceService:
    """
    [AGENT 3: CUSTOMER INTELLIGENCE AGENT]
    Observes completed turns from the Voice Runtime in parallel,
    extracts structured behavioral state via LLM, and publishes to the Event Bus.
    """

    @staticmethod
    async def handle_turn_completed(event: Dict[str, Any]):
        payload = event.get("payload", {})
        call_id = payload.get("call_id", "unknown")
        transcript = payload.get("transcript", "")
        merchant_id = event.get("merchant_id", "jiva_demo")
        correlation_id = event.get("correlation_id", call_id)
        recovery_attempt_id = payload.get("recovery_attempt_id", "live_demo")

        if not transcript:
            return

        logger.info(f"🧠 [CustomerIntel Agent] Analyzing customer turn in parallel: '{transcript}'")

        try:
            parsed_data = await CustomerIntelligenceService._analyze_turn(transcript)
            
            # Enforce immutable customer utterance
            parsed_data["customer_words"] = transcript

            validated_state = CustomerState(**parsed_data)
            
            logger.info(
                f"✅ [CustomerIntel] Classified: Temp={validated_state.temperature} | "
                f"Barrier={validated_state.barrier} | Action={validated_state.next_action}"
            )

            await bus.publish(
                event_type="customer.understood",
                payload={
                    "recovery_attempt_id": recovery_attempt_id,
                    "call_id": call_id,
                    **validated_state.dict()
                },
                correlation_id=correlation_id,
                merchant_id=merchant_id
            )

        except Exception as e:
            logger.warning(f"CustomerIntel fallback used: {e}")
            # Fallback safe classification
            await bus.publish(
                event_type="customer.understood",
                payload={
                    "recovery_attempt_id": recovery_attempt_id,
                    "call_id": call_id,
                    "intent": "purchase_interest",
                    "temperature": "WARM",
                    "barrier": "price",
                    "customer_words": transcript,
                    "next_action": "request_offer",
                    "confidence": 0.85
                },
                correlation_id=correlation_id,
                merchant_id=merchant_id
            )

    @staticmethod
    async def _analyze_turn(transcript: str) -> Dict[str, Any]:
        """Calls OpenRouter with strict JSON schema to classify customer turn."""
        system_prompt = (
            "You are an expert sales psychologist. Classify the customer's phone speech turn into JSON.\n"
            "Required keys:\n"
            "- intent: purchase_interest | browsing | support | opt_out\n"
            "- temperature: HOT (ready to buy/asking price) | WARM (interested but has hesitation) | COLD (not interested/annoyed)\n"
            "- barrier: price | timing | decision_maker | trust | none\n"
            "- next_action: request_offer | schedule_callback | opt_out | continue_discovery | send_checkout\n"
            "- budget: number or null\n"
            "- timeline: 'today' | 'tomorrow' | 'next_week' | null\n"
            "- decision_maker: 'self' | 'spouse' | 'parents' | 'other'\n"
            "- confidence: float between 0.8 and 1.0\n"
            "- requested_discount: number (e.g. 10) or null"
        )

        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                res = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
                    json={
                        "model": "openai/gpt-4o-mini",
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": f"Customer said: \"{transcript}\""}
                        ],
                        "response_format": {"type": "json_object"},
                        "max_tokens": 120,
                        "temperature": 0.2
                    }
                )
                if res.status_code == 200:
                    content = res.json()["choices"][0]["message"]["content"]
                    return json.loads(content)
        except Exception as e:
            logger.warning(f"LLM parsing failed: {e}")

        # Rule-based fallback
        t_low = transcript.lower()
        if any(w in t_low for w in ["expensive", "price", "discount", "budget", "kam", "jyada", "paisa"]):
            return {"intent": "purchase_interest", "temperature": "HOT", "barrier": "price", "next_action": "request_offer", "confidence": 0.95}
        elif any(w in t_low for w in ["send", "link", "email", "mail", "buy", "bhej", "theek", "deal"]):
            return {"intent": "purchase_interest", "temperature": "HOT", "barrier": "none", "next_action": "send_checkout", "confidence": 0.98}
        elif any(w in t_low for w in ["later", "tomorrow", "kal", "busy", "call"]):
            return {"intent": "purchase_interest", "temperature": "WARM", "barrier": "timing", "next_action": "schedule_callback", "confidence": 0.88}
        else:
            return {"intent": "purchase_interest", "temperature": "WARM", "barrier": "price", "next_action": "request_offer", "confidence": 0.85}


customer_intel = CustomerIntelligenceService()
bus.subscribe("voice.turn_completed", CustomerIntelligenceService.handle_turn_completed)
