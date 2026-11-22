with

source as (
    select * from {{ source('ledgerly', 'events') }}
),

renamed as (
    select
        id as event_id,
        type as event_type,
        object_id,
        cast(payload_json as json) as payload,
        created_at,
        ingested_at
    from source
),

deduplicated as (
    -- webhooks are re-delivered with the same id; keep the first delivery
    select *
    from renamed
    qualify row_number() over (partition by event_id order by ingested_at) = 1
)

select * from deduplicated
