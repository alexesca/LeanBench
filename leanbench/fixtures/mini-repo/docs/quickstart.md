# Quickstart

Build a cart, price it and place the order.

```python
from datetime import date
from shopcart import Cart, CheckoutService, InventoryRepository, PaymentGateway
from shopcart import Customer, Money, PricingEngine, Product

cart = Cart()
cart.add_item(Product("WID-1", "Widget", Money(1000)), 2)

service = CheckoutService(
    InventoryRepository({"WID-1": 10}),
    PricingEngine(),
    PaymentGateway(),
)
order = service.place_order(cart, Customer("c-1", "shopper@example.com"), None, date.today())
print(order.total.as_decimal_string())
```

## Money is integer cents

Amounts never use floating point. `Money` holds integer minor units so repeated
additions cannot drift by a cent.

## Errors you should handle

`OutOfStock` when the warehouse is short, `CouponExpired` when a voucher has lapsed,
and `PaymentDeclined` when the card processor refuses the charge.
