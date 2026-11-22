with

source as (
    select * from {{ source('ledgerly', 'payments') }}
),

renamed as (
    select
        id as payment_id,
        invoice_id,
        amount,
        currency,
        status,
        -- the raw string mixes 'Z' (UTC) and Europe/Warsaw local time with an offset;
        -- casting to timestamptz honours the offset, so a payment at 00:30+01:00 on the 1st
        -- lands on the previous day (and month) in UTC
        attempted_at as attempted_at_raw,
        cast(attempted_at as timestamptz) as attempted_at
    from source
)

select * from renamed
