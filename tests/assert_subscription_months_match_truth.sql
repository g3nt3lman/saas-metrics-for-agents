-- Reconciliation: apply docs/metric_definitions.md to the generator's ground truth and compare
-- with int_subscription_months, subscription by month, in local currency (FX is not in the truth).
-- Rules applied to the truth: trial = 0, past_due and canceled_pending_end count, ended = 0, MRR is net of discount.
-- Returns one row per mismatch; the test passes when it returns nothing.

with

truth as (
    select
        subscription_id,
        month as month_start,
        state_at_month_end,
        case
            when state_at_month_end in ('active', 'past_due', 'canceled_pending_end')
                then run_rate_mrr_local_list - discount_mrr_local
            else 0
        end as mrr_local
    from {{ source('ledgerly_truth', 'customer_month_facts') }}
),

modelled as (
    select
        subscription_id,
        month_start,
        subscription_state,
        mrr_local
    from {{ ref('int_subscription_months') }}
),

compared as (
    select
        coalesce(truth.subscription_id, modelled.subscription_id) as subscription_id,
        coalesce(truth.month_start, modelled.month_start) as month_start,
        truth.state_at_month_end as truth_state,
        modelled.subscription_state as modelled_state,
        truth.mrr_local as truth_mrr_local,
        modelled.mrr_local as modelled_mrr_local
    from truth
    full outer join modelled
        on
            truth.subscription_id = modelled.subscription_id
            and truth.month_start = modelled.month_start
)

select *
from compared
where
    truth_state is distinct from modelled_state
    or abs(coalesce(truth_mrr_local, -1) - coalesce(modelled_mrr_local, -2)) > 0.005
