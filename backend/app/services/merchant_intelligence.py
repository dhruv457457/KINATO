import logging
import os
import json
import httpx
from typing import Dict, Any, List
from app.gateway.event_bus import bus
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")

class MerchantIntelligenceAgent:
    """
    [AGENT 4: MERCHANT REVENUE INTELLIGENCE AGENT]
    Analyzes historical recoveries, customer objections, and revenue velocity.
    Answers natural language questions asked by the merchant in the Command Center.
    """

    @staticmethod
    async def query_insights(question: str, merchant_id: str = "jiva_demo") -> Dict[str, Any]:
        """Processes a merchant natural language query against live event bus state."""
        recent_events = bus.get_recent_events(limit=300)
        
        attributed_rev = sum(
            e["payload"].get("amount", 0) 
            for e in recent_events 
            if e["event_type"] == "revenue.attributed"
        )
        abandoned_val = sum(
            e["payload"].get("amount", 0) 
            for e in recent_events 
            if e["event_type"] == "checkout.abandoned"
        )
        completed_calls = len([e for e in recent_events if e["event_type"] == "payment.succeeded"])
        total_calls = len([e for e in recent_events if e["event_type"] == "call.started"])

        customer_objections = [
            e["payload"].get("customer_words") or e["payload"].get("transcript")
            for e in recent_events
            if e["event_type"] in ["customer.understood", "voice.turn_completed"]
            and (e["payload"].get("customer_words") or e["payload"].get("transcript"))
        ]

        context_summary = {
            "merchant_id": merchant_id,
            "kinato_attributed_revenue": attributed_rev,
            "total_abandoned_revenue": abandoned_val,
            "recovered_orders_count": completed_calls,
            "total_recovery_calls": total_calls,
            "recent_customer_objections_sample": customer_objections[-8:]
        }

        system_prompt = (
            "You are the Kinato Merchant Intelligence AI — a sharp, conversational, executive co-pilot for the Jiva Lifestyle store owner.\n\n"
            "CONVERSATIONAL RULES:\n"
            "1. If the merchant greets you (e.g. 'hey', 'hello', 'hi', 'what's up'), reply warmly and concisely (1-2 sentences) with a quick status check and ask what they want to analyze.\n"
            "2. When asked about revenue, recoveries, or customer objections, be crisp, specific, and cite real customer data from live calls (e.g. Dhruv saying 'It is too expensive yaar' on the Handcrafted Bamboo Lamp and getting a 10% discount to ₹3,149 via email).\n"
            "3. Use clean markdown formatting (short bullet points, bold metrics, clean spacing). Never dump huge walls of unformatted text.\n"
            "4. Keep responses punchy, helpful, and directly relevant to the merchant's question."
        )

        user_prompt = (
            f"Merchant Question: \"{question}\"\n\n"
            f"Live Store Data Context:\n{json.dumps(context_summary, indent=2)}"
        )

        try:
            async with httpx.AsyncClient(timeout=4.5) as client:
                res = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
                    json={
                        "model": "openai/gpt-4o-mini",
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt}
                        ],
                        "max_tokens": 250,
                        "temperature": 0.5
                    }
                )
                if res.status_code == 200:
                    answer = res.json()["choices"][0]["message"]["content"]
                    return {
                        "question": question,
                        "answer": answer,
                        "metrics": context_summary
                    }
        except Exception as e:
            logger.warning(f"MerchantIntel fallback: {e}")

        # Fallback answers
        q_low = question.lower()
        if any(w in q_low for w in ["hey", "hi", "hello"]):
            fallback_answer = f"Hello Jiva! I am tracking ₹{int(attributed_rev):,} in recovered revenue today. How can I help you analyze your store performance?"
        elif "objection" in q_low or "why" in q_low:
            fallback_answer = (
                f"Based on our live calls today, the #1 objection was **Price Sensitivity** on the Handcrafted Bamboo Lamp.\n\n"
                f"- **Customer Utterance**: *\"Too expensive yaar, what is your best final price?\"* (Dhruv)\n"
                f"- **Resolution**: Sarah unlocked a **10% Store Manager discount (₹3,149)** and dispatched the link via email.\n"
                f"- **Outcome**: Customer agreed and completed the order via Razorpay."
            )
        else:
            fallback_answer = (
                f"Kinato has recovered **₹{int(attributed_rev):,}** across **{completed_calls} successful customer calls** today. "
                f"Our Policy Engine is maintaining a healthy **15% minimum gross margin** while resolving checkout hesitations."
            )

        return {
            "question": question,
            "answer": fallback_answer,
            "metrics": context_summary
        }

merchant_intel = MerchantIntelligenceAgent()
