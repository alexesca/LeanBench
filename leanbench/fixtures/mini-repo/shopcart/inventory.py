"""Warehouse stock: reserving units for an order and releasing them again."""

from .cart import Cart
from .errors import OutOfStock


class InventoryRepository:
    """In-memory stock ledger keyed by stock keeping unit.

    Reservations are all-or-nothing: if any line cannot be satisfied the whole
    reservation is rolled back, so a failed checkout never leaves units stranded.
    """

    def __init__(self, stock: dict[str, int] | None = None) -> None:
        self.stock: dict[str, int] = dict(stock or {})
        self.reserved: dict[str, int] = {}

    def available(self, sku: str) -> int:
        """Units on hand that are not already reserved for another order."""
        return self.stock.get(sku, 0) - self.reserved.get(sku, 0)

    def reserve(self, sku: str, quantity: int) -> None:
        """Hold `quantity` units of `sku`.

        Raises :class:`OutOfStock` when the warehouse cannot cover the request.
        """
        available = self.available(sku)
        if quantity > available:
            raise OutOfStock(sku, quantity, available)
        self.reserved[sku] = self.reserved.get(sku, 0) + quantity

    def release(self, sku: str, quantity: int) -> None:
        """Give reserved units back, never dropping below zero."""
        self.reserved[sku] = max(0, self.reserved.get(sku, 0) - quantity)

    def reserve_cart(self, cart: Cart) -> list[str]:
        """Reserve every line in a cart atomically.

        On the first :class:`OutOfStock` every already-reserved line is released and
        the error is re-raised, leaving the ledger exactly as it was found.
        """
        taken: list[tuple[str, int]] = []
        try:
            for line in cart.lines:
                self.reserve(line.product.sku, line.quantity)
                taken.append((line.product.sku, line.quantity))
        except OutOfStock:
            for sku, quantity in taken:
                self.release(sku, quantity)
            raise
        return [sku for sku, _quantity in taken]

    def release_cart(self, cart: Cart) -> None:
        """Release every reservation this cart holds, used when an order is cancelled."""
        for line in cart.lines:
            self.release(line.product.sku, line.quantity)
