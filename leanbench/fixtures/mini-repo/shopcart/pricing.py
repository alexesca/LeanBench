"""Pricing: turns a cart plus a coupon into the amount the shopper actually owes."""

from datetime import date

from .cart import Cart
from .discounts import Coupon, apply_coupon
from .models import Customer, Money

#: VAT percentage per tax region. The region comes from the customer record.
TAX_RATES = {"EU": 21, "UK": 20, "US": 0}


class TaxCalculator:
    """Applies the value added tax rate that belongs to a customer's region."""

    def __init__(self, rates: dict[str, int] | None = None) -> None:
        self.rates = dict(TAX_RATES if rates is None else rates)

    def rate_for(self, customer: Customer) -> int:
        """VAT percentage for the customer's region, defaulting to zero."""
        return self.rates.get(customer.tax_region, 0)

    def add_tax(self, amount: Money, customer: Customer) -> Money:
        """Return the amount with the customer's VAT added on top."""
        rate = self.rate_for(customer)
        return Money(amount.cents + (amount.cents * rate) // 100, amount.currency)


class PricingEngine:
    """Computes an order total: subtotal, then discount, then tax, in that order.

    The ordering matters: tax is charged on the discounted amount, never on the
    pre-discount subtotal, because charging VAT on money the shopper never paid
    would over-collect tax.
    """

    def __init__(self, tax_calculator: TaxCalculator | None = None) -> None:
        self.tax_calculator = tax_calculator or TaxCalculator()

    def compute_total(
        self,
        cart: Cart,
        customer: Customer,
        coupon: Coupon | None = None,
        today: date | None = None,
    ) -> Money:
        """Final amount owed for a cart, including discount and tax."""
        subtotal = cart.subtotal()
        discounted = self.apply_discount(subtotal, coupon, today or date.today())
        return self.tax_calculator.add_tax(discounted, customer)

    def apply_discount(self, subtotal: Money, coupon: Coupon | None, today: date) -> Money:
        """Reduce a subtotal by a coupon, or return it untouched when there is none."""
        if coupon is None:
            return subtotal
        return apply_coupon(coupon, subtotal, today)

    def price_breakdown(
        self,
        cart: Cart,
        customer: Customer,
        coupon: Coupon | None = None,
        today: date | None = None,
    ) -> dict[str, Money]:
        """Every intermediate amount, for receipts and for debugging a wrong total."""
        subtotal = cart.subtotal()
        discounted = self.apply_discount(subtotal, coupon, today or date.today())
        total = self.tax_calculator.add_tax(discounted, customer)
        return {"subtotal": subtotal, "discounted": discounted, "total": total}
