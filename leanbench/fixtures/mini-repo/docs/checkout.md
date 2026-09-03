# Checkout

## Sequence

1. The cart is checked for lines.
2. Stock is reserved for every line, atomically.
3. The order is priced.
4. The card is charged.

Stock is reserved before the charge so that a shopper is never billed for goods the
warehouse cannot ship. If the charge is declined the reservation is released again.

## Retries

The payment gateway retries transient failures such as issuer timeouts up to a fixed
attempt budget. Hard declines are not retried.
