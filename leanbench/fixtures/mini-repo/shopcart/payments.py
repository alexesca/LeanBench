"""Payment capture, including the retry policy for transient gateway failures."""

from .errors import PaymentDeclined
from .models import Customer, Money

#: How many times a transient gateway failure is retried before giving up.
MAX_PAYMENT_ATTEMPTS = 3
#: Gateway responses that are worth retrying; anything else is final.
RETRYABLE_REASONS = frozenset({"timeout", "issuer_unavailable", "network"})


class PaymentGateway:
    """Thin wrapper over a card processor with a bounded retry loop.

    Retries exist because issuers time out; they are bounded because charging a card
    an unbounded number of times is how a shopper ends up paying twice.
    """

    def __init__(self, responses: list[str] | None = None) -> None:
        #: Scripted responses, oldest first. "ok" authorises, anything else declines.
        self.responses = list(responses or ["ok"])
        self.attempts = 0

    def _next_response(self) -> str:
        if not self.responses:
            return "ok"
        return self.responses.pop(0)

    def charge(self, customer: Customer, amount: Money) -> str:
        """Charge `amount` to the customer, retrying only transient failures.

        Raises :class:`PaymentDeclined` once the attempt budget is exhausted or the
        issuer returns a reason that is not retryable.
        """
        self.attempts = 0
        last_reason = "unknown"
        while self.attempts < MAX_PAYMENT_ATTEMPTS:
            self.attempts += 1
            response = self._next_response()
            if response == "ok":
                return f"auth-{customer.customer_id}-{amount.cents}"
            last_reason = response
            if response not in RETRYABLE_REASONS:
                break
        raise PaymentDeclined(last_reason, self.attempts)

    def refund(self, authorisation: str, amount: Money) -> str:
        """Reverse a previously authorised charge."""
        return f"refund-{authorisation}-{amount.cents}"
