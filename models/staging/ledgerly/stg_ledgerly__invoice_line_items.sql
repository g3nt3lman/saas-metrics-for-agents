with

source as (
    select * from {{ source('ledgerly', 'invoice_line_items') }}
),

renamed as (
    select
        id as invoice_line_item_id,
        invoice_id,
        type as line_type,
        amount,
        period_start,
        period_end,
        plan_id,
        cast(quantity as integer) as quantity
    from source
)

select * from renamed
