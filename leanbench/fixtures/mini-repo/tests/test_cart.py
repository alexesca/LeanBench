"""Tests for Cart line management and the untaxed subtotal."""

import pytest

from shopcart.cart import Cart
from shopcart.models import Money, Product

PEN = Product(sku="PEN-3", name="Pen", unit_price=Money(150))
PAD = Product(sku="PAD-4", name="Notepad", unit_price=Money(450))


def test_adding_the_same_sku_merges_lines():
    """Adding a product twice increases the quantity of the existing line."""
    cart = Cart()
    cart.add_item(PEN, 1)
    cart.add_item(PEN, 2)
    assert len(cart.lines) == 1
    assert cart.item_count() == 3


def test_non_positive_quantity_is_rejected():
    """A quantity of zero is an error rather than a silent no-op."""
    cart = Cart()
    with pytest.raises(ValueError):
        cart.add_item(PEN, 0)


def test_subtotal_sums_line_totals_before_tax():
    """The subtotal is the sum of the line totals, with no tax or discount applied."""
    cart = Cart()
    cart.add_item(PEN, 2)
    cart.add_item(PAD, 1)
    assert cart.subtotal() == Money(750)


def test_remove_item_reports_whether_anything_changed():
    """Removing a sku that is not present returns False."""
    cart = Cart()
    cart.add_item(PEN, 1)
    assert cart.remove_item("PEN-3") is True
    assert cart.remove_item("PEN-3") is False
