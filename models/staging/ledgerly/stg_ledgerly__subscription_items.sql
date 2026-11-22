with

source as (
    select * from {{ source('ledgerly', 'subscription_items') }}
),

renamed as (
    select
        id as subscription_item_id,
        subscription_id,
        plan_id,
        cast(quantity as integer) as quantity,
        valid_from,
        valid_to
    from source
)

select * from renamed
