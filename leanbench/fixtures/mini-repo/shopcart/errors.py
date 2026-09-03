"""Exception hierarchy for the shopping cart domain.

Every failure a caller is expected to handle derives from :class:`ShopCartError`.
"""


class ShopCartError(Exception):
    """Base class for every error raised by the shopcart package."""


class OutOfStock(ShopCartError):
    """Raised when a reservation asks for more units than the warehouse holds."""

    def __init__(self, sku: str, requested: int, available: int) -> None:
        super().__init__(f"{sku}: requested {requested}, only {available} available")
        self.sku = sku
        self.requested = requested
        self.available = available


class CouponExpired(ShopCartError):
    """Raised when a coupon is applied after its expiry date."""

    def __init__(self, code: str, expired_on: str) -> None:
        super().__init__(f"coupon {code} expired on {expired_on}")
        self.code = code
        self.expired_on = expired_on


class CouponNotApplicable(ShopCartError):
    """Raised when a coupon's minimum-subtotal condition is not met."""


class PaymentDeclined(ShopCartError):
    """Raised when the payment gateway refuses the charge after all retries."""

    def __init__(self, reason: str, attempts: int) -> None:
        super().__init__(f"payment declined after {attempts} attempts: {reason}")
        self.reason = reason
        self.attempts = attempts


class EmptyCart(ShopCartError):
    """Raised when checkout is attempted with no lines in the cart."""
