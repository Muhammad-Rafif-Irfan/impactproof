"""Checkout process — imports both DiscountService and Product."""

from shop.discount import DiscountService
from shop.inventory import Product


class Checkout:
    def __init__(self):
        self.discount = DiscountService()

    def process(self, product: Product, quantity: int) -> dict:
        if not product.is_available():
            raise ValueError(f"{product.name} is out of stock")
        unit_price = self.discount.apply(product.price)
        return {
            "product": product.name,
            "quantity": quantity,
            "unit_price": unit_price,
            "total": round(unit_price * quantity, 2),
        }
