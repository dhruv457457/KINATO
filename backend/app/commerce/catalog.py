import logging
from typing import List, Optional
from pydantic import BaseModel

logger = logging.getLogger(__name__)

class NormalizedProduct(BaseModel):
    """
    Clean, external-facing commerce representation.
    Does not expose internal DB IDs or sensitive merchant schema.
    """
    product_id: str
    name: str
    description: str
    price: float
    currency: str = "INR"
    inventory_status: str  # "in_stock", "out_of_stock", "preorder"
    inventory_count: int
    shipping_estimate: str
    category: str


class CatalogAdapter:
    """
    Mock adapter representing the merchant's live inventory.
    In production, this queries the Jiva DB or Shopify APIs.
    """
    def __init__(self):
        self._db = {
            "sku_lamp_01": NormalizedProduct(
                product_id="sku_lamp_01",
                name="Handcrafted Bamboo Lamp",
                description="Sustainable room decor. Warm lighting.",
                price=2499.0,
                inventory_status="in_stock",
                inventory_count=15,
                shipping_estimate="2-3 days",
                category="decor"
            ),
            "sku_rug_02": NormalizedProduct(
                product_id="sku_rug_02",
                name="Jute Area Rug",
                description="Organic jute woven rug.",
                price=3499.0,
                inventory_status="in_stock",
                inventory_count=5,
                shipping_estimate="4-5 days",
                category="decor"
            )
        }

    def search(self, query: str = "", max_price: Optional[float] = None) -> List[NormalizedProduct]:
        results = []
        for product in self._db.values():
            if query.lower() in product.name.lower() or query.lower() in product.description.lower() or not query:
                if max_price is None or product.price <= max_price:
                    results.append(product)
        return results

    def get_product(self, product_id: str) -> Optional[NormalizedProduct]:
        return self._db.get(product_id)
        
    def mutate_price(self, product_id: str, new_price: float):
        """Helper for testing revalidation failures"""
        if product_id in self._db:
            self._db[product_id].price = new_price

    def mutate_inventory(self, product_id: str, new_count: int):
        """Helper for testing revalidation failures"""
        if product_id in self._db:
            self._db[product_id].inventory_count = new_count
            self._db[product_id].inventory_status = "in_stock" if new_count > 0 else "out_of_stock"

merchant_catalog = CatalogAdapter()
