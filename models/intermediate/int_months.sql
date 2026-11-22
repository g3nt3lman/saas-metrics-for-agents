-- One row per calendar month covered by the export: from the first subscription start
-- to the last FX date (= the as-of date of the export). Month boundaries are UTC.

with

subscriptions as (
    select * from {{ ref('stg_ledgerly__subscriptions') }}
),

fx_rates as (
    select * from {{ ref('stg_ledgerly__fx_rates') }}
),

bounds as (
    select
        (select cast(date_trunc('month', min(started_at)) as timestamp) from subscriptions) as first_month,
        (select cast(date_trunc('month', max(rate_date)) as timestamp) from fx_rates) as last_month
),

months as (
    select cast(unnest(generate_series(first_month, last_month, interval 1 month)) as date) as month_start
    from bounds
),

final as (
    select
        month_start,
        last_day(month_start) as month_end_date,
        cast(month_start as timestamptz) as month_start_at,
        -- exclusive: the instant the next month starts; "state at month end" = state just before this
        cast(month_start + interval 1 month as timestamptz) as month_end_at
    from months
)

select * from final
