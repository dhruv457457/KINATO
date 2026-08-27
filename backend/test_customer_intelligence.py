import asyncio
import logging
import uuid
from typing import List, Dict, Any

# Configure logging to see the pipeline clearly
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(name)s | %(message)s")
logger = logging.getLogger("TestCustomerIntel")

from app.gateway.event_bus import bus
from app.services.customer_intelligence import CustomerIntelligenceService

# Hook up the subscriber
bus.subscribe("voice.turn_completed", CustomerIntelligenceService.handle_turn_completed)

# Simple interceptor to catch output
emitted_states = []
async def intercept_understood(event: Dict[str, Any]):
    emitted_states.append(event)
bus.subscribe("customer.understood", intercept_understood)

test_cases = [
    "It's too expensive yaar. I was looking for something around 3000.",
    "I'll buy it tomorrow",
    "Don't call me again",
    "I'm just looking",
    "Send me the link",
    "I need to ask my wife first",
    "malformed json response please"
]

async def run_tests():
    logger.info("=== STARTING CUSTOMER INTELLIGENCE TESTS ===")
    
    for idx, transcript in enumerate(test_cases):
        global emitted_states
        emitted_states = []  # reset
        
        logger.info(f"\n--- Test Case {idx + 1}: '{transcript}' ---")
        
        await bus.publish(
            event_type="voice.turn_completed",
            payload={
                "call_id": f"call_{idx}",
                "transcript": transcript,
                "recovery_attempt_id": f"rec_{idx}"
            },
            correlation_id=f"corr_{idx}",
            merchant_id="jiva_demo"
        )
        
        # wait for async processing
        await asyncio.sleep(0.1)
        
        if "malformed" in transcript:
            if not emitted_states:
                logger.info("✅ SUCCESS: Malformed JSON correctly failed closed. No state emitted.")
            else:
                logger.error("❌ FAILED: Malformed JSON bypassed validation!")
            continue
            
        if emitted_states:
            state = emitted_states[0]["payload"]
            logger.info(f"✅ SUCCESS: Emitted next_action -> {state['next_action']}")
            logger.info(f"   Immutable customer_words -> {state['customer_words']}")
        else:
            logger.error("❌ FAILED: Valid transcript did not produce an emitted state.")

if __name__ == "__main__":
    asyncio.run(run_tests())
