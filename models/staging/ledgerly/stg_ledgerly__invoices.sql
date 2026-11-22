with

source as (
    select * from {{ source('ledgerly', 'invoices') }}
),

renamed as (
    select
        id as invoice_id,
        customer_id,
        subscription_id,
        period_start,
        period_end,
        currency,
        total as total_amount,
        status,
        created_at
    from source
)

select * from renamed
