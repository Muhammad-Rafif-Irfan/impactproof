"""Discount calculation service."""


class DiscountService:
    def __init__(self, rate: float = 0.1):
        self.rate = rate

    def apply(self, price: float) -> float:
        return round(price * (1 - self.rate), 2)
