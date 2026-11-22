with

source as (
    select * from {{ source('ledgerly', 'subscriptions') }}
),

renamed as (
    select
        id as subscription_id,
        customer_id,
        status,
        started_at,
        trial_end as trial_end_at,
        canceled_at,
        ended_at,
        cancel_at_period_end
    from source
)

select * from renamed
