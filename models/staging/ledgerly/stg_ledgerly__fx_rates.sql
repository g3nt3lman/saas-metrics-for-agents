with

source as (
    select * from {{ source('ledgerly', 'fx_rates') }}
),

renamed as (
    select
        date as rate_date,
        currency,
        rate_to_eur
    from source
)

select * from renamed
