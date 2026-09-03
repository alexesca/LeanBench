"""Checkout orchestration: the one place cart, pricing, stock and payment meet."""

from dataclasses import dataclass
from datetime import date

from .cart import Cart
from .discounts import Coupon
from .errors import PaymentDeclined
from .inventory import InventoryRepository
from .models import Customer, Money
from .payments import PaymentGateway
from .pricing import PricingEngine


@dataclass(frozen=True)
class Order:
    """The result of a successful checkout."""

    order_id: str
    customer_id: str
    total: Money
    authorisation: str
    skus: list[str]


class CheckoutService:
    """Turns a cart into an order.

    The sequence is fixed and deliberate: validate the cart, reserve stock, price the
    order, then charge. Stock is reserved before the charge so a shopper is never
    billed for something the warehouse cannot ship, and the reservation is released
    if the payment is declined.
    """

    def __init__(
        self,
        inventory: InventoryRepository,
        pricing: PricingEngine,
        gateway: PaymentGateway,
    ) -> None:
        self.inventory = inventory
        self.pricing = pricing
        self.gateway = gateway
        self.orders_placed = 0

    def place_order(
        self,
        cart: Cart,
        customer: Customer,
        coupon: Coupon | None = None,
        today: date | None = None,
    ) -> Order:
        """Reserve stock, price the cart and capture payment.

        Propagates :class:`OutOfStock` from the reservation and
        :class:`PaymentDeclined` from the gateway; in the latter case the stock
        reservation is rolled back before the error leaves this method.
        """
        cart.require_not_empty()
        self.inventory.reserve_cart(cart)
        total = self.pricing.compute_total(cart, customer, coupon, today)
        try:
            authorisation = self.gateway.charge(customer, total)
        except PaymentDeclined:
            self.inventory.release_cart(cart)
            raise
        self.orders_placed += 1
        return Order(
            order_id=f"order-{self.orders_placed:04d}",
            customer_id=customer.customer_id,
            total=total,
            authorisation=authorisation,
            skus=cart.skus(),
        )

    def cancel_order(self, cart: Cart, order: Order) -> str:
        """Release the reserved stock and refund the captured amount."""
        self.inventory.release_cart(cart)
        return self.gateway.refund(order.authorisation, order.total)
