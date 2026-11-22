with

customers as (
    select * from {{ ref('stg_ledgerly__customers') }}
),

customer_months as (
    select * from {{ ref('int_mrr_by_customer_month') }}
),

billing as (
    select
        customer_id,
        -- cohort = first month the customer was paying at month end, over the whole history;
        -- a customer returning after the 12-month reactivation window keeps this cohort
        min(month_start) filter (where is_paying) as cohort_month,
        min(month_start) filter (where is_paying) is not null as has_ever_paid,
        any_value(currency) as currency
    from customer_months
    group by customer_id
),

final as (
    select
        customers.customer_id,
        customers.company_name,
        customers.country_code,
        billing.currency,
        customers.created_at,
        cast(date_trunc('month', customers.created_at) as date) as signup_month,
        billing.cohort_month,
        coalesce(billing.has_ever_paid, false) as has_ever_paid,
        customers.is_internal,
        customers.is_deleted
    from customers
    left join billing on customers.customer_id = billing.customer_id
)

select * from final
