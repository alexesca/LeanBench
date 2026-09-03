"""shopcart — a deliberately small but genuinely structured example package.

Layering, top to bottom:

    checkout  -> pricing, inventory, payments, cart
    pricing   -> cart, discounts, models
    inventory -> cart, errors
    cart      -> models, errors
    models    -> (nothing)
"""

from .cart import Cart
from .checkout import CheckoutService, Order
from .discounts import Coupon, apply_coupon, best_coupon, validate_coupon
from .errors import (
    CouponExpired,
    CouponNotApplicable,
    EmptyCart,
    OutOfStock,
    PaymentDeclined,
    ShopCartError,
)
from .inventory import InventoryRepository
from .models import CartLine, Customer, Money, Product
from .payments import PaymentGateway
from .pricing import PricingEngine, TaxCalculator

__all__ = [
    "Cart",
    "CartLine",
    "CheckoutService",
    "Coupon",
    "CouponExpired",
    "CouponNotApplicable",
    "Customer",
    "EmptyCart",
    "InventoryRepository",
    "Money",
    "Order",
    "OutOfStock",
    "PaymentDeclined",
    "PaymentGateway",
    "PricingEngine",
    "Product",
    "ShopCartError",
    "TaxCalculator",
]
