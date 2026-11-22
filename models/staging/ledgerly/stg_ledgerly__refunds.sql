with

source as (
    select * from {{ source('ledgerly', 'refunds') }}
),

renamed as (
    select
        id as refund_id,
        payment_id,
        amount,
        created_at,
        reason
    from source
)

select * from renamed
