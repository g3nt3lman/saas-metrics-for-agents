-- Canary: a customer whose subscriptions are all trialing at month end has no MRR.
select
    customer_month_id,
    mrr_eur
from {{ ref('fct_mrr_monthly') }}
where subscription_state = 'trialing' and mrr_eur <> 0
