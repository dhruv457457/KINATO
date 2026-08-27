import asyncio
import logging
import uuid
import sys

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(name)s | %(levelname)s | %(message)s")
logger = logging.getLogger("AI_BUYER_TEST")

from app.commerce.catalog import merchant_catalog
from app.commerce.mcp_server import ai_commerce_mcp

async def test_happy_path():
    logger.info("=== STARTING AI BUYER TEST: HAPPY PATH ===")
    
    # AI Discovery
    logger.info("AI Action: search_products('decor', max_price=3000)")
    results = ai_commerce_mcp.search_products("decor", max_price=3000.0)
    logger.info(f"AI Discovered: {len(results)} product(s)")
    assert len(results) > 0
    product_id = results[0]["product_id"]
    
    # AI Quote
    logger.info(f"AI Action: quote('{product_id}')")
    quote = ai_commerce_mcp.quote(product_id)
    quote_id = quote["quote_id"]
    logger.info(f"Quote received: INR {quote['quoted_amount']} (ID: {quote_id})")
    
    # AI Intent
    logger.info(f"AI Action: create_purchase_intent('{quote_id}')")
    intent_result = await ai_commerce_mcp.create_purchase_intent(quote_id)
    
    if intent_result.get("status") == "APPROVED":
        logger.info(f"✅ SUCCESS: Purchase Intent Approved. Link: {intent_result['payment_url']}")
    else:
        logger.error(f"❌ FAILED: Happy path was rejected: {intent_result}")


async def test_price_mutation_failure():
    logger.info("\n=== STARTING AI BUYER TEST: PRICE MUTATION REJECTION ===")
    
    product_id = "sku_lamp_01"
    
    # Reset price just in case
    merchant_catalog.mutate_price(product_id, 2499.0)
    
    # 1. AI gets quote
    quote = ai_commerce_mcp.quote(product_id)
    quote_id = quote["quote_id"]
    logger.info(f"Quote received: INR {quote['quoted_amount']} (ID: {quote_id})")
    
    # 2. MALICIOUS / STALE STATE: Price changes on merchant backend
    logger.info("--> Merchant mutates product price to 3499.0 behind the scenes")
    merchant_catalog.mutate_price(product_id, 3499.0)
    
    # 3. AI requests intent
    logger.info(f"AI Action: create_purchase_intent('{quote_id}')")
    intent_result = await ai_commerce_mcp.create_purchase_intent(quote_id)
    
    if intent_result.get("error") == "REJECTED":
        logger.info(f"✅ SUCCESS: Boundary caught mismatch. Reason: {intent_result.get('reason')}")
    else:
        logger.error(f"❌ FAILED: Boundary allowed an invalid price to execute.")


async def test_inventory_failure():
    logger.info("\n=== STARTING AI BUYER TEST: INVENTORY DEPLETION REJECTION ===")
    
    product_id = "sku_rug_02"
    merchant_catalog.mutate_inventory(product_id, 5) # reset
    
    quote = ai_commerce_mcp.quote(product_id, quantity=2)
    quote_id = quote["quote_id"]
    
    logger.info("--> Another buyer buys the last 5 rugs. Inventory drops to 1.")
    merchant_catalog.mutate_inventory(product_id, 1)
    
    intent_result = await ai_commerce_mcp.create_purchase_intent(quote_id)
    if intent_result.get("error") == "REJECTED":
        logger.info(f"✅ SUCCESS: Boundary caught inventory shortage. Reason: {intent_result.get('reason')}")
    else:
        logger.error(f"❌ FAILED: Boundary allowed execution on depleted inventory.")


async def test_expiry_failure():
    logger.info("\n=== STARTING AI BUYER TEST: QUOTE EXPIRY REJECTION ===")
    
    product_id = "sku_rug_02"
    
    quote = ai_commerce_mcp.quote(product_id)
    quote_id = quote["quote_id"]
    
    logger.info("--> Simulating quote timeout (10+ minutes later)")
    ai_commerce_mcp.mock_expire_quote(quote_id)
    
    intent_result = await ai_commerce_mcp.create_purchase_intent(quote_id)
    if intent_result.get("error") == "REJECTED":
        logger.info(f"✅ SUCCESS: Boundary caught expired quote. Reason: {intent_result.get('reason')}")
    else:
        logger.error(f"❌ FAILED: Boundary allowed execution on expired quote.")

async def main():
    await test_happy_path()
    await test_price_mutation_failure()
    await test_inventory_failure()
    await test_expiry_failure()

if __name__ == "__main__":
    asyncio.run(main())
