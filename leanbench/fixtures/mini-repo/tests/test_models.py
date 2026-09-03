"""Tests for the Money value object."""

import pytest

from shopcart.models import Money


def test_amounts_in_different_currencies_cannot_be_added():
    """Adding across currencies raises rather than producing a nonsense total."""
    with pytest.raises(ValueError):
        Money(100, "EUR").add(Money(100, "USD"))


def test_percent_off_rounds_half_away_from_zero():
    """A 10% reduction of 1005 cents rounds the half cent up."""
    assert Money(1005).percent_off(10) == Money(904)


def test_as_decimal_string_pads_the_cents():
    """Rendering keeps two decimal places."""
    assert Money(1205).as_decimal_string() == "12.05 EUR"
