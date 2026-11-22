-- MRR snapshot per customer × month end, in EUR. Internal test accounts are excluded here,
-- so nothing downstream (semantic layer, agent) can include them by accident.
-- MRR is a snapshot: it must never be summed over time (see the semantic model).

with

customer_months as (
    select * from {{ ref('int_mrr_by_customer_month') }}
),

final as (
    select
        customer_id || '_' || strftime(month_start, '%Y-%m') as customer_month_id,
        customer_id,
        plan_id,
        month_start,
        currency,
        subscription_state,
        is_paying,
        fx_rate_to_eur,
        mrr_local,
        mrr_eur,
        mrr_gross_eur,
        discount_eur,
        mrr_at_risk_eur
    from customer_months
    where not is_internal
)

select * from final
