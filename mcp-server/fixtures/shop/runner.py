"""
Runner that uses the bare `import shop.checkout` style (module.attr call pattern).
This exercises the module_map / Attribute call path in the analyzer.
"""

import shop.checkout as shop_checkout
import shop.inventory as shop_inventory


def run_order(name: str, price: float, stock: int, qty: int) -> dict:
    product = shop_inventory.Product(name, price, stock)
    checkout_obj = shop_checkout.Checkout()
    return checkout_obj.process(product, qty)
