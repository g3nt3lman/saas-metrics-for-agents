with

source as (
    select * from {{ source('ledgerly', 'plans') }}
),

renamed as (
    select
        plan_id,
        product as product_id,
        tier,
        interval as billing_interval,
        case interval when 'year' then 12 else 1 end as months_per_interval,
        currency,
        unit_amount,
        cast(regexp_extract(plan_id, '_v([0-9]+)$', 1) as integer) as price_version,
        valid_from,
        active as is_active,
        updated_at
    from source
)

select * from renamed
