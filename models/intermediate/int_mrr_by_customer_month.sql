-- One row per customer × month (all customers, internal included; the mart filters them out).
-- Rolls int_subscription_months up to the customer and converts to EUR at the month-end rate.

with

subscription_months as (
    select * from {{ ref('int_subscription_months') }}
),

customers as (
    select * from {{ ref('stg_ledgerly__customers') }}
),

fx_rates as (
    select * from {{ ref('int_fx_rates_month_end') }}
),

-- a reactivated customer can in principle have two subscriptions in one month; the paying,
-- highest-MRR one describes the customer's plan and state for that month
primary_subscription as (
    select
        customer_id,
        month_start,
        subscription_id,
        plan_id,
        currency,
        subscription_state
    from subscription_months
    qualify row_number() over (
        partition by customer_id, month_start
        order by is_paying desc, mrr_local desc, subscription_id asc
    ) = 1
),

summed as (
    select
        customer_id,
        month_start,
        sum(mrr_local) as mrr_local,
        sum(mrr_gross_local) as mrr_gross_local,
        sum(discount_local) as discount_local,
        sum(case when subscription_state = 'past_due' then mrr_local else 0 end) as mrr_at_risk_local
    from subscription_months
    group by customer_id, month_start
),

final as (
    select
        summed.customer_id,
        summed.month_start,
        primary_subscription.subscription_id,
        primary_subscription.plan_id,
        primary_subscription.currency,
        primary_subscription.subscription_state,
        summed.mrr_local > 0 as is_paying,
        customers.is_internal,
        customers.is_deleted,
        fx_rates.rate_to_eur as fx_rate_to_eur,
        summed.mrr_local,
        summed.mrr_gross_local,
        summed.discount_local,
        summed.mrr_local * fx_rates.rate_to_eur as mrr_eur,
        summed.mrr_gross_local * fx_rates.rate_to_eur as mrr_gross_eur,
        summed.discount_local * fx_rates.rate_to_eur as discount_eur,
        summed.mrr_at_risk_local * fx_rates.rate_to_eur as mrr_at_risk_eur
    from summed
    inner join primary_subscription
        on
            summed.customer_id = primary_subscription.customer_id
            and summed.month_start = primary_subscription.month_start
    inner join customers on summed.customer_id = customers.customer_id
    left join fx_rates
        on
            summed.month_start = fx_rates.month_start
            and primary_subscription.currency = fx_rates.currency
)

select * from final
