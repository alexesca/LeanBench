"""Tests for PricingEngine and TaxCalculator."""

from datetime import date

from shopcart.cart import Cart
from shopcart.discounts import Coupon
from shopcart.models import Customer, Money, Product
from shopcart.pricing import PricingEngine, TaxCalculator

BOOK = Product(sku="BOOK-1", name="Book", unit_price=Money(2000))
EU_CUSTOMER = Customer(customer_id="c-1", email="a@example.com", tax_region="EU")
US_CUSTOMER = Customer(customer_id="c-2", email="b@example.com", tax_region="US")
TODAY = date(2026, 1, 1)


def test_tax_is_charged_on_the_discounted_amount():
    """VAT applies after the coupon, never to the pre-discount subtotal."""
    cart = Cart()
    cart.add_item(BOOK, 1)
    coupon = Coupon(code="HALF", percent_off=50, expires_on=date(2026, 12, 31))
    total = PricingEngine().compute_total(cart, EU_CUSTOMER, coupon, TODAY)
    assert total == Money(1210)


def test_us_region_has_no_vat():
    """A US customer is charged the discounted subtotal with no tax added."""
    cart = Cart()
    cart.add_item(BOOK, 1)
    assert PricingEngine().compute_total(cart, US_CUSTOMER, None, TODAY) == Money(2000)


def test_rate_for_unknown_region_is_zero():
    """An unrecognised tax region falls back to a zero rate."""
    customer = Customer(customer_id="c-3", email="c@example.com", tax_region="MARS")
    assert TaxCalculator().rate_for(customer) == 0


def test_price_breakdown_exposes_each_stage():
    """The breakdown reports subtotal, discounted amount and final total."""
    cart = Cart()
    cart.add_item(BOOK, 2)
    breakdown = PricingEngine().price_breakdown(cart, EU_CUSTOMER, None, TODAY)
    assert breakdown["subtotal"] == Money(4000)
    assert breakdown["total"].cents > breakdown["discounted"].cents
