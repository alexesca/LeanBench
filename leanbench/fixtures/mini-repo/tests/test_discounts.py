"""Tests for coupon validation and selection."""

from datetime import date

import pytest

from shopcart.discounts import Coupon, apply_coupon, best_coupon, validate_coupon
from shopcart.errors import CouponExpired, CouponNotApplicable
from shopcart.models import Money

TODAY = date(2026, 6, 1)
LIVE = Coupon(code="TEN", percent_off=10, expires_on=date(2026, 12, 31))
STALE = Coupon(code="OLD", percent_off=90, expires_on=date(2025, 1, 1))
BIG_SPEND = Coupon(
    code="BULK", percent_off=25, expires_on=date(2026, 12, 31), minimum_subtotal_cents=10_000
)


def test_expired_coupon_raises_coupon_expired():
    """A voucher past its expiry date is refused."""
    with pytest.raises(CouponExpired):
        validate_coupon(STALE, Money(5000), TODAY)


def test_minimum_subtotal_is_enforced():
    """A voucher with a minimum spend is refused on a small cart."""
    with pytest.raises(CouponNotApplicable):
        validate_coupon(BIG_SPEND, Money(500), TODAY)


def test_apply_coupon_reduces_the_subtotal():
    """A valid voucher reduces the amount by its percentage."""
    assert apply_coupon(LIVE, Money(1000), TODAY) == Money(900)


def test_best_coupon_ignores_invalid_vouchers():
    """Selection skips expired and inapplicable vouchers and keeps the best valid one."""
    assert best_coupon([STALE, LIVE, BIG_SPEND], Money(5000), TODAY) == LIVE
