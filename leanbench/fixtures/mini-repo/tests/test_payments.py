"""Tests for the payment retry policy."""

import pytest

from shopcart.errors import PaymentDeclined
from shopcart.models import Customer, Money
from shopcart.payments import MAX_PAYMENT_ATTEMPTS, PaymentGateway

CUSTOMER = Customer(customer_id="c-7", email="e@example.com")


def test_transient_failures_are_retried_until_success():
    """A timeout is retried and a later success still authorises the charge."""
    gateway = PaymentGateway(["timeout", "ok"])
    assert gateway.charge(CUSTOMER, Money(500)).startswith("auth-")
    assert gateway.attempts == 2


def test_retries_are_bounded():
    """Repeated transient failures stop after the attempt budget."""
    gateway = PaymentGateway(["timeout"] * 10)
    with pytest.raises(PaymentDeclined):
        gateway.charge(CUSTOMER, Money(500))
    assert gateway.attempts == MAX_PAYMENT_ATTEMPTS


def test_non_retryable_reason_fails_immediately():
    """A hard decline is not retried at all."""
    gateway = PaymentGateway(["fraud", "ok"])
    with pytest.raises(PaymentDeclined):
        gateway.charge(CUSTOMER, Money(500))
    assert gateway.attempts == 1
