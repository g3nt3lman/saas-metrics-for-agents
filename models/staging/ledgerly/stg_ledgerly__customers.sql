with

source as (
    select * from {{ source('ledgerly', 'customers') }}
),

renamed as (
    select
        id as customer_id,
        lower(email) as email,
        split_part(lower(email), '@', 2) as email_domain,
        company_name,
        country as country_code,
        created_at,
        is_deleted,
        split_part(lower(email), '@', 2) = 'ledgerly.io' as is_internal
    from source
)

select * from renamed
