"""Inventory / product catalogue."""


class Product:
    def __init__(self, name: str, price: float, stock: int):
        self.name = name
        self.price = price
        self.stock = stock

    def is_available(self) -> bool:
        return self.stock > 0
