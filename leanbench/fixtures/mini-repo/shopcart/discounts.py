"""Coupons and the rules that decide whether one may be applied."""

from dataclasses import dataclass
from datetime import date

from .errors import CouponExpired, CouponNotApplicable
from .models import Money


@dataclass(frozen=True)
class Coupon:
    """A discount voucher with an expiry date and a minimum-spend condition."""

    code: str
    percent_off: int
    expires_on: date
    minimum_subtotal_cents: int = 0

    def is_expired(self, today: date) -> bool:
        """True when the voucher's expiry date is in the past."""
        return today > self.expires_on


def validate_coupon(coupon: Coupon, subtotal: Money, today: date) -> None:
    """Check a coupon against the cart before it is allowed to reduce the price.

    Raises :class:`CouponExpired` when the voucher's date has passed and
    :class:`CouponNotApplicable` when the cart subtotal is below the minimum spend.
    """
    if coupon.is_expired(today):
        raise CouponExpired(coupon.code, coupon.expires_on.isoformat())
    if subtotal.cents < coupon.minimum_subtotal_cents:
        raise CouponNotApplicable(
            f"coupon {coupon.code} needs a subtotal of at least "
            f"{coupon.minimum_subtotal_cents} cents"
        )


def apply_coupon(coupon: Coupon, subtotal: Money, today: date) -> Money:
    """Validate the voucher and return the reduced subtotal."""
    validate_coupon(coupon, subtotal, today)
    return subtotal.percent_off(coupon.percent_off)


def best_coupon(coupons: list[Coupon], subtotal: Money, today: date) -> Coupon | None:
    """Pick the voucher that saves the shopper the most, ignoring invalid ones.

    Ties are broken by coupon code so the choice is stable between runs.
    """
    usable: list[Coupon] = []
    for coupon in sorted(coupons, key=lambda c: c.code):
        try:
            validate_coupon(coupon, subtotal, today)
        except (CouponExpired, CouponNotApplicable):
            continue
        usable.append(coupon)
    if not usable:
        return None
    return max(usable, key=lambda c: (c.percent_off, c.code))
