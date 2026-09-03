"""Value objects: money amounts, catalogue products and cart lines."""

from dataclasses import dataclass

CENTS_PER_UNIT = 100


@dataclass(frozen=True)
class Money:
    """An exact monetary amount held in integer minor units (cents).

    Floating point is deliberately avoided so that totals never drift by a cent.
    """

    cents: int
    currency: str = "EUR"

    def add(self, other: "Money") -> "Money":
        """Return the sum of two amounts in the same currency."""
        if self.currency != other.currency:
            raise ValueError(f"cannot add {self.currency} to {other.currency}")
        return Money(self.cents + other.cents, self.currency)

    def scale(self, factor: int) -> "Money":
        """Multiply the amount by an integer quantity."""
        return Money(self.cents * factor, self.currency)

    def percent_off(self, percent: int) -> "Money":
        """Return the amount reduced by `percent`, rounding half away from zero."""
        reduction = (self.cents * percent + CENTS_PER_UNIT // 2) // CENTS_PER_UNIT
        return Money(self.cents - reduction, self.currency)

    def as_decimal_string(self) -> str:
        """Human readable rendering such as `12.34 EUR`."""
        return f"{self.cents // CENTS_PER_UNIT}.{self.cents % CENTS_PER_UNIT:02d} {self.currency}"


@dataclass(frozen=True)
class Product:
    """A catalogue entry identified by its stock keeping unit."""

    sku: str
    name: str
    unit_price: Money
    taxable: bool = True


@dataclass
class CartLine:
    """One product and the quantity a shopper wants of it."""

    product: Product
    quantity: int

    def line_total(self) -> Money:
        """Price of this line before any discount or tax."""
        return self.product.unit_price.scale(self.quantity)


@dataclass(frozen=True)
class Customer:
    """The buyer, including the tax region that decides the VAT rate."""

    customer_id: str
    email: str
    tax_region: str = "EU"
