import logging
import os
import json
from typing import Dict, Any
from dotenv import load_dotenv
from app.core.config import settings
from app.core.net import shared_ipv4_client
from app.db.repositories import recovery_attempts as recovery_attempts_repo
from app.db.repositories import audit as audit_repo
from app.db.repositories import products as products_repo
from app.db.repositories import policies as policies_repo
from app.db.repositories import customers as customers_repo

load_dotenv()

logger = logging.getLogger(__name__)

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")


class MerchantIntelligenceAgent:
    """
    Answers a merchant's natural-language question about their own store,
    grounded only in that merchant's real DB rows (recovery_attempts,
    checkouts, audit_log) - never the process-wide event bus, which is
    unscoped across merchants and empty after every restart. An earlier
    version of this file read bus.get_recent_events() with no merchant_id
    filter, which meant one merchant's chat could see (and answer with)
    every other merchant's revenue and customer conversations - a real
    cross-tenant leak, not just a wording problem. Fixed by querying the
    same merchant-scoped repos the dashboard uses.
    """

    @staticmethod
    async def query_insights(question: str, merchant_id: str) -> Dict[str, Any]:
        """Processes a merchant natural language query against that
        merchant's own real, persisted data."""
        stats = recovery_attempts_repo.summary_stats(merchant_id)
        audit_rows = audit_repo.recent_audit(merchant_id, limit=300)

        customer_objections = []
        for row in audit_rows:
            if row.get("action") != "issue_offer" and row.get("action") != "check_offer":
                continue
            args = row.get("args")
            try:
                args = json.loads(args) if isinstance(args, str) else args
            except (TypeError, ValueError):
                continue
            reason = (args or {}).get("reason")
            if reason:
                customer_objections.append(reason)

        # Postgres SUM()/COUNT() come back as Decimal. FastAPI's own response
        # encoder handles that transparently, which is why /dashboard/overview
        # never tripped on it - but this dict gets json.dumps()'d by hand into
        # the LLM prompt below, and json.dumps has no idea what a Decimal is.
        # Coerce at the boundary rather than leaking Decimal into the prompt.
        def _num(v, cast=int):
            return None if v is None else cast(v)

        # The three things a merchant asks about that this could never
        # answer.
        #
        # Until now the context was seven summary numbers and a list of
        # refusal codes. So "what is my discount ceiling?", "which product
        # gets abandoned most?" and "who has opted out?" were all
        # unanswerable - and rule 3 below correctly forbids inventing an
        # answer, which meant the honest reply to most real questions was a
        # shrug. The data was one query away the whole time.
        #
        # Bounded on purpose. This is interpolated into a prompt that has to
        # answer inside a few seconds, so the catalogue is capped and
        # customers are counted rather than listed - a merchant asking about
        # a specific person should be reading the Customers screen, which
        # shows all of them, rather than hoping a language model recites a
        # table correctly.
        try:
            policy = policies_repo.get_policy(merchant_id)
        except Exception as e:
            logger.warning(f"Ask could not read policy for {merchant_id}: {e}")
            policy = {}
        try:
            products = products_repo.list_products(merchant_id)
        except Exception as e:
            logger.warning(f"Ask could not read catalogue for {merchant_id}: {e}")
            products = []
        try:
            customers = customers_repo.list_for_merchant(merchant_id, limit=200)
        except Exception as e:
            logger.warning(f"Ask could not read customers for {merchant_id}: {e}")
            customers = []

        context_summary = {
            "merchant_id": merchant_id,
            "revenue_recovered_paise": _num(stats["recovered_paise"]),
            "revenue_at_risk_paise": _num(stats["revenue_at_risk_paise"]),
            "recovered_orders_count": _num(stats["recovered_count"]),
            "total_recovery_attempts": _num(stats["total_attempts"]),
            "recovery_rate_pct": _num(stats["recovery_rate_pct"], float),
            "recent_offer_reasons_sample": customer_objections[-8:],
            "has_any_activity": _num(stats["total_attempts"]) > 0,

            # What the agent is ALLOWED to do. A merchant asking "can my AI
            # give 20% off?" is asking about this, and the answer is a fact,
            # not an opinion.
            "your_policy": {
                "max_discount_percent": policy.get("max_discount_percent"),
                "minimum_margin_percent": policy.get("minimum_margin_percent"),
                "offer_ladder_percent": policy.get("offer_ladder"),
                "auto_approval_threshold_inr": policy.get("auto_approval_threshold_inr"),
                "calling_hours_ist": (
                    f"{policy.get('calling_start_hour')}:00-{policy.get('calling_end_hour')}:00"
                    if policy.get("calling_start_hour") is not None else None
                ),
                "note_for_you": (
                    "The ladder is climbed, not jumped: the first offer opens at the lowest "
                    "rung and only reaches the ceiling after the customer turns down the "
                    "steps below it. The ceiling and the margin floor both cap it."
                ),
            },

            "catalogue": {
                "product_count": len(products),
                "products": [
                    {
                        "sku": p.get("product_id"),
                        "name": p.get("name"),
                        "price_paise": _num(p.get("price_paise")),
                        "cogs_paise": _num(p.get("cogs_paise")),
                        "margin_percent": (
                            round((p["price_paise"] - p["cogs_paise"]) / p["price_paise"] * 100, 1)
                            if p.get("price_paise") and p.get("cogs_paise") else None
                        ),
                        "in_stock": _num(p.get("inventory_count")),
                    }
                    for p in products[:40]
                ],
                "products_without_a_cost": [
                    p.get("product_id") for p in products if not p.get("cogs_paise")
                ][:10],
            },

            "customers": {
                "total": len(customers),
                "with_phone": sum(1 for c in customers if c.get("phone")),
                "with_email": sum(1 for c in customers if c.get("email")),
                "reachable_by_neither": sum(
                    1 for c in customers if not c.get("phone") and not c.get("email")
                ),
            },
        }

        system_prompt = (
            "You are the Kinato Merchant Intelligence AI - a sharp, concise analyst answering a merchant's "
            "question about their own store.\n\n"
            "RULES:\n"
            "1. ANSWER THE QUESTION ASKED, directly, in your first sentence, using the real figures from the "
            "Live Store Data Context. Only fall back to 'what would you like to analyze?' if the message is a "
            "bare greeting with no question in it.\n"
            "2. Money in the context is in PAISE. Convert to rupees (divide by 100) and format Indian-style "
            "(e.g. 10576500 -> ₹1,05,765). Never show a raw paise figure to the merchant.\n"
            "3. Answer ONLY from the Live Store Data Context. Never invent a customer name, a product, a quote, "
            "or a discount that isn't in that context.\n"
            "4. If `has_any_activity` is false, say plainly there is no recovery activity yet - do not fabricate "
            "an example to fill the gap.\n"
            "5. Be specific and useful: name the real number, then say what it implies or what to do next.\n"
            "6. Clean markdown, short bullets, bold the key metric. Under 120 words."
        )

        user_prompt = (
            f"Merchant Question: \"{question}\"\n\n"
            f"Live Store Data Context:\n{json.dumps(context_summary, indent=2)}"
        )

        if OPENROUTER_API_KEY:
            try:
                # shared_ipv4_client, not a bare httpx.AsyncClient: this was
                # the one LLM call site that bypassed app/core/net.py
                # entirely, so it kept the black-holed-IPv6 failure that
                # module exists to prevent, and paid a fresh handshake every
                # time on top.
                #
                # The model and base URL come from settings now. Hardcoding
                # them here meant a provider swap would silently leave this
                # endpoint on the old one, still billing, still slow.
                client = shared_ipv4_client("llm", timeout=4.5)
                res = await client.post(
                    f"{settings.LLM_BASE_URL.rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
                    json={
                        "model": settings.LLM_MODEL,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        "max_tokens": 250,
                        "temperature": 0.5,
                    },
                )
                if res.status_code == 200:
                    answer = res.json()["choices"][0]["message"]["content"]
                    return {
                        "question": question,
                        "answer": answer,
                        "metrics": context_summary,
                        "source": "llm",
                    }
            except Exception as e:
                logger.warning(f"MerchantIntel LLM call failed: {e}")

        # Heuristic fallback - grounded in the same real numbers, no
        # confidence figure and no fabricated anecdote, since there's
        # nothing to be confident (or not) about here.
        if not context_summary["has_any_activity"]:
            fallback_answer = "No recovery activity recorded yet for your store. Once a checkout fails or is abandoned, it'll show up here."
        else:
            fallback_answer = (
                f"Kinato has recovered **₹{(context_summary['revenue_recovered_paise'] or 0) / 100:,.0f}** "
                f"across **{context_summary['recovered_orders_count']}** of "
                f"**{context_summary['total_recovery_attempts']}** recovery attempts"
                + (f" ({context_summary['recovery_rate_pct']}% recovery rate)." if context_summary["recovery_rate_pct"] is not None else ".")
            )

        return {
            "question": question,
            "answer": fallback_answer,
            "metrics": context_summary,
            "source": "heuristic",
            "degraded": True,
        }


merchant_intel = MerchantIntelligenceAgent()
