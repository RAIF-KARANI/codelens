"""Domain models."""

from dataclasses import dataclass

from shop.pricing import with_tax


class Entity:
    def __init__(self, id):
        self.id = id

    def describe(self):
        return f"{type(self).__name__}#{self.id}"


@dataclass
class Product(Entity):
    name: str = ""
    price: float = 0.0

    @property
    def gross(self):
        return with_tax(self.price)

    @gross.setter
    def gross(self, value):
        self.price = value

    def describe(self):
        return super().describe() + f" {self.name}"


class Order(Entity):
    def __init__(self, id, items):
        super().__init__(id)
        self.items = items

    def total(self):
        return sum(item.gross for item in self.items)

    def summary(self):
        lines = [item.describe() for item in self.items]
        return "\n".join(lines) + self.describe()
