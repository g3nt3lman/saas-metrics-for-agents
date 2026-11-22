-- Canary: Ledgerly's own test accounts must never reach the MRR mart.
select fct.customer_month_id
from {{ ref('fct_mrr_monthly') }} as fct
inner join {{ ref('dim_customers') }} as cust
    on fct.customer_id = cust.customer_id
where cust.is_internal
