"""Tests for InventoryRepository reservations."""

import pytest

from shopcart.cart import Cart
from shopcart.errors import OutOfStock
from shopcart.inventory import InventoryRepository
from shopcart.models import Money, Product

WIDGET = Product(sku="WID-1", name="Widget", unit_price=Money(1000))
GADGET = Product(sku="GAD-2", name="Gadget", unit_price=Money(2500))


def test_reserve_reduces_available_units():
    """Reserving units lowers what is left available for other orders."""
    repo = InventoryRepository({"WID-1": 5})
    repo.reserve("WID-1", 2)
    assert repo.available("WID-1") == 3


def test_reserve_raises_out_of_stock_when_short():
    """Asking for more units than the warehouse holds raises OutOfStock."""
    repo = InventoryRepository({"WID-1": 1})
    with pytest.raises(OutOfStock):
        repo.reserve("WID-1", 4)


def test_reserve_cart_rolls_back_on_partial_failure():
    """A cart whose second line is short leaves the first line unreserved."""
    repo = InventoryRepository({"WID-1": 5, "GAD-2": 0})
    cart = Cart()
    cart.add_item(WIDGET, 1)
    cart.add_item(GADGET, 1)
    with pytest.raises(OutOfStock):
        repo.reserve_cart(cart)
    assert repo.available("WID-1") == 5


def test_release_never_goes_negative():
    """Releasing more than was reserved clamps at zero."""
    repo = InventoryRepository({"WID-1": 5})
    repo.release("WID-1", 3)
    assert repo.available("WID-1") == 5
