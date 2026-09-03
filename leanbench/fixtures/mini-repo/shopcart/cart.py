"""The shopping cart itself: holds lines and computes an untaxed subtotal."""

from .errors import EmptyCart
from .models import CartLine, Money, Product


class Cart:
    """A mutable collection of cart lines belonging to one shopper.

    The cart knows nothing about tax, discounts or stock; it only tracks what the
    shopper picked and what those picks cost before any adjustment.
    """

    def __init__(self, currency: str = "EUR") -> None:
        self.currency = currency
        self.lines: list[CartLine] = []

    def add_item(self, product: Product, quantity: int = 1) -> CartLine:
        """Add a product to the cart, merging with an existing line for the same sku.

        Adding a quantity of zero or less is rejected rather than silently ignored.
        """
        if quantity <= 0:
            raise ValueError(f"quantity must be positive, got {quantity}")
        for line in self.lines:
            if line.product.sku == product.sku:
                line.quantity += quantity
                return line
        line = CartLine(product=product, quantity=quantity)
        self.lines.append(line)
        return line

    def remove_item(self, sku: str) -> bool:
        """Drop every line for `sku`. Returns True when something was removed."""
        before = len(self.lines)
        self.lines = [line for line in self.lines if line.product.sku != sku]
        return len(self.lines) != before

    def subtotal(self) -> Money:
        """Sum of all line totals before discounts and before tax."""
        total = Money(0, self.currency)
        for line in self.lines:
            total = total.add(line.line_total())
        return total

    def item_count(self) -> int:
        """Total number of physical units in the cart."""
        return sum(line.quantity for line in self.lines)

    def require_not_empty(self) -> None:
        """Guard used by checkout: an empty cart cannot become an order."""
        if not self.lines:
            raise EmptyCart("cart has no lines")

    def skus(self) -> list[str]:
        """Sorted stock keeping units currently in the cart."""
        return sorted(line.product.sku for line in self.lines)
