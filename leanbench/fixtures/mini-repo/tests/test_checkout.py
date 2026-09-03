"""Tests for CheckoutService orchestration."""

from datetime import date

import pytest

from shopcart.cart import Cart
from shopcart.checkout import CheckoutService
from shopcart.errors import EmptyCart, PaymentDeclined
from shopcart.inventory import InventoryRepository
from shopcart.models import Customer, Money, Product
from shopcart.payments import PaymentGateway
from shopcart.pricing import PricingEngine

LAMP = Product(sku="LAMP-9", name="Lamp", unit_price=Money(3000))
CUSTOMER = Customer(customer_id="c-9", email="d@example.com", tax_region="US")
TODAY = date(2026, 3, 3)


def _cart() -> Cart:
    cart = Cart()
    cart.add_item(LAMP, 1)
    return cart


def test_place_order_returns_an_order_with_an_authorisation():
    """A successful checkout captures payment and returns the order."""
    service = CheckoutService(InventoryRepository({"LAMP-9": 3}), PricingEngine(), PaymentGateway())
    order = service.place_order(_cart(), CUSTOMER, None, TODAY)
    assert order.total == Money(3000)
    assert order.authorisation.startswith("auth-")


def test_declined_payment_releases_the_stock_reservation():
    """When the gateway declines, the reserved units go back to the warehouse."""
    inventory = InventoryRepository({"LAMP-9": 3})
    gateway = PaymentGateway(["fraud"])
    service = CheckoutService(inventory, PricingEngine(), gateway)
    with pytest.raises(PaymentDeclined):
        service.place_order(_cart(), CUSTOMER, None, TODAY)
    assert inventory.available("LAMP-9") == 3


def test_empty_cart_cannot_be_checked_out():
    """Checkout refuses a cart with no lines."""
    service = CheckoutService(InventoryRepository({}), PricingEngine(), PaymentGateway())
    with pytest.raises(EmptyCart):
        service.place_order(Cart(), CUSTOMER, None, TODAY)
