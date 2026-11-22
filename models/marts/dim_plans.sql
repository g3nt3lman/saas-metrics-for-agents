with

plans as (
    select * from {{ ref('stg_ledgerly__plans') }}
),

final as (
    select
        plan_id,
        product_id,
        tier,
        billing_interval,
        months_per_interval,
        currency,
        unit_amount,
        price_version,
        valid_from,
        is_active
    from plans
)

select * from final
