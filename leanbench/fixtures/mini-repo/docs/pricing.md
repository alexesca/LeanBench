# Pricing rules

## Order of operations

A total is computed as subtotal, then discount, then tax. Tax is charged on the
discounted amount, never on the original subtotal.

## Tax regions

The VAT percentage comes from the customer's `tax_region`. `EU` is 21%, `UK` is 20%
and `US` is 0%. An unknown region is treated as 0%.

## Coupons

A coupon carries an expiry date and an optional minimum spend. Both conditions are
checked before the discount is applied; a coupon that fails either check is refused
rather than silently ignored.
