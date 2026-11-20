# Metric definitions

Business definitions for Ledgerly metrics. Decide these **before** writing the intermediate layer
(see `PROJECT_BRIEF.md` §5). Model and metric descriptions reference this file.

## When does churn happen: at cancellation, at `ended_at`, or after dunning fails?

**Decision:** Churn happens in the month of `ended_at`, with a separate `is_voluntary` attribute.
Subscriptions in `past_due` (dunning in progress) count towards MRR.

## Which FX rate: the month-start rate, the invoice-date rate, or the monthly average?

**Decision:** The month-end rate, i.e. the rate from the last working day of the month, for all financial metrics.
Differences in MRR that come from currency movements go to the `fx_adjustment` movement.

## Are discounts in MRR (net) or excluded (gross)?

**Decision:** Net. Additional metrics: `mrr_gross_eur` and `discount_eur`.
A coupon ending is a separate movement: `discount_expiry`.

## Reactivation window: after how many months does a returning customer count as "new" again?

**Decision:** 12 months from `ended_at`. The cohort is the first paid month in the customer's whole history.

## How do proration lines affect MRR and revenue?

**Decision:**
- MRR comes from the `subscription_items` versions at month end, excluding prorations and one-off charges.
- `revenue_recognized` is spread monthly over the service period.
- One-off charges are recognized on the invoice date.
- Refunds are recognized in the month of the refund. This matches cash and is simpler.

## Other

- **Trials:** MRR = 0. A trial with no conversion is not churn.
- **Internal customers:** flagged in the dimension table, but **not** included in metrics.
- **Soft-deleted customers:** kept in history.
- **Uncollectible invoices:** an invoice not paid after dunning is not revenue.
