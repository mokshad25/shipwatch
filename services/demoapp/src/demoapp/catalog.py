"""In-memory product catalog and order store. A restart (deploy) starts from a clean slate."""

import itertools
from dataclasses import dataclass, field
from decimal import Decimal


@dataclass
class Product:
    id: int
    name: str
    price: Decimal
    stock: int


def _products() -> dict[int, Product]:
    items = [
        ("Espresso beans 1kg", "24.90"),
        ("Pour-over kettle", "39.00"),
        ("Ceramic dripper", "18.50"),
        ("Paper filters x100", "4.20"),
        ("Burr grinder", "129.00"),
        ("Milk jug", "12.75"),
        ("Travel mug", "16.00"),
        ("Scale", "29.99"),
    ]
    # Plenty of stock: only an absurd quantity (as the load script sometimes asks for) runs out.
    return {
        i: Product(id=i, name=name, price=Decimal(price), stock=1_000_000)
        for i, (name, price) in enumerate(items, start=1)
    }


@dataclass
class Store:
    products: dict[int, Product] = field(default_factory=_products)
    orders: dict[int, dict] = field(default_factory=dict)
    _ids: itertools.count = field(default_factory=lambda: itertools.count(1))

    def next_order_id(self) -> int:
        return next(self._ids)
