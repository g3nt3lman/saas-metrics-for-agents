-- One row per subscription × month while the subscription exists, including the month it ended.
-- Applies the MRR definitions from docs/metric_definitions.md in local currency:
--   * state is taken at month end (just before the next month starts, UTC);
--   * trialing = 0, ended = 0;
--   * past_due (dunning in progress) and canceled_pending_end (annual cancel at period end) still count;
--   * MRR = list run-rate of the live item version (annual ÷ 12) minus the coupon run-rate;
--   * prorations and one-off lines never enter MRR.

with

subscriptions as (
    select * from {{ ref('stg_ledgerly__subscriptions') }}
),

items as (
    select * from {{ ref('stg_ledgerly__subscription_items') }}
),

plans as (
    select * from {{ ref('stg_ledgerly__plans') }}
),

invoices as (
    select * from {{ ref('stg_ledgerly__invoices') }}
),

invoice_lines as (
    select * from {{ ref('stg_ledgerly__invoice_line_items') }}
),

payments as (
    select * from {{ ref('stg_ledgerly__payments') }}
),

months as (
    select * from {{ ref('int_months') }}
),

subscription_months as (
    select
        subscriptions.subscription_id,
        subscriptions.customer_id,
        subscriptions.started_at,
        subscriptions.trial_end_at,
        subscriptions.canceled_at,
        subscriptions.ended_at,
        months.month_start,
        months.month_end_at
    from subscriptions
    inner join months
        on
            subscriptions.started_at < months.month_end_at
            and (subscriptions.ended_at is null or subscriptions.ended_at >= months.month_start_at)
),

-- the item version live at month end; for a subscription that ended this month, its last version
live_items as (
    select
        subscription_months.subscription_id,
        subscription_months.month_start,
        items.plan_id,
        items.quantity
    from subscription_months
    inner join items
        on
            subscription_months.subscription_id = items.subscription_id
            and subscription_months.month_end_at > items.valid_from
    qualify row_number() over (
        partition by subscription_months.subscription_id, subscription_months.month_start
        order by items.valid_from desc
    ) = 1
),

-- invoices whose first payment attempt failed: when dunning started and when (if ever) it was recovered
dunning_invoices as (
    select
        invoices.subscription_id,
        min(payments.attempted_at) filter (where payments.status = 'failed') as first_failed_at,
        min(payments.attempted_at) filter (where payments.status = 'succeeded') as recovered_at
    from invoices
    inner join payments on invoices.invoice_id = payments.invoice_id
    group by invoices.subscription_id, invoices.invoice_id
    having count(*) filter (where payments.status = 'failed') > 0
),

in_dunning as (
    select distinct
        subscription_months.subscription_id,
        subscription_months.month_start
    from subscription_months
    inner join dunning_invoices
        on
            subscription_months.subscription_id = dunning_invoices.subscription_id
            and subscription_months.month_end_at > dunning_invoices.first_failed_at
            and (
                dunning_invoices.recovered_at is null
                or subscription_months.month_end_at <= dunning_invoices.recovered_at
            )
),

-- coupon run-rate: the discount line of the invoice whose service period covers the month end
discounts as (
    select
        subscription_months.subscription_id,
        subscription_months.month_start,
        max(
            round(-invoice_lines.amount / datediff('month', invoice_lines.period_start, invoice_lines.period_end), 4)
        ) as discount_local
    from subscription_months
    inner join invoices on subscription_months.subscription_id = invoices.subscription_id
    inner join invoice_lines
        on
            invoices.invoice_id = invoice_lines.invoice_id
            and invoice_lines.line_type = 'discount'
            and cast(invoice_lines.period_start as timestamptz) < subscription_months.month_end_at
            and cast(invoice_lines.period_end as timestamptz) >= subscription_months.month_end_at
    group by subscription_months.subscription_id, subscription_months.month_start
),

with_state as (
    select
        subscription_months.subscription_id,
        subscription_months.customer_id,
        subscription_months.month_start,
        live_items.plan_id,
        plans.tier,
        plans.billing_interval,
        plans.currency,
        live_items.quantity,
        case
            when subscription_months.ended_at < subscription_months.month_end_at then 'ended'
            when subscription_months.trial_end_at >= subscription_months.month_end_at then 'trialing'
            when subscription_months.canceled_at < subscription_months.month_end_at then 'canceled_pending_end'
            when in_dunning.subscription_id is not null then 'past_due'
            else 'active'
        end as subscription_state,
        round(plans.unit_amount * live_items.quantity / plans.months_per_interval, 4) as list_mrr_local,
        coalesce(discounts.discount_local, 0) as coupon_mrr_local
    from subscription_months
    inner join live_items
        on
            subscription_months.subscription_id = live_items.subscription_id
            and subscription_months.month_start = live_items.month_start
    inner join plans on live_items.plan_id = plans.plan_id
    left join in_dunning
        on
            subscription_months.subscription_id = in_dunning.subscription_id
            and subscription_months.month_start = in_dunning.month_start
    left join discounts
        on
            subscription_months.subscription_id = discounts.subscription_id
            and subscription_months.month_start = discounts.month_start
),

final as (
    select
        subscription_id,
        customer_id,
        month_start,
        plan_id,
        tier,
        billing_interval,
        currency,
        quantity,
        subscription_state,
        subscription_state in ('active', 'past_due', 'canceled_pending_end') as is_paying,
        case when is_paying then list_mrr_local else 0 end as mrr_gross_local,
        case when is_paying then coupon_mrr_local else 0 end as discount_local,
        mrr_gross_local - discount_local as mrr_local
    from with_state
)

select * from final
