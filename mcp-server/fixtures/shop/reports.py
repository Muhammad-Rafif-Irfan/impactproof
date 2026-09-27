"""Order reporting — imports Checkout (indirect dependency on discount + inventory)."""

from shop.checkout import Checkout
from shop.inventory import Product


def generate_report(items: list[tuple[str, float, int, int]]) -> list[dict]:
    """
    items: list of (name, price, stock, quantity)
    Returns a list of order line dicts.
    """
    checkout = Checkout()
    report = []
    for name, price, stock, qty in items:
        product = Product(name, price, stock)
        try:
            line = checkout.process(product, qty)
        except ValueError as exc:
            line = {"product": name, "error": str(exc)}
        report.append(line)
    return report
