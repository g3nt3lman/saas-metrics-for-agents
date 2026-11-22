-- FX rule (docs/metric_definitions.md): the rate of the last business day on or before the month end.
-- fx_rates has no weekend rows, so an exact-date join would silently drop months ending on a weekend.

with

months as (
    select * from {{ ref('int_months') }}
),

fx_rates as (
    select * from {{ ref('stg_ledgerly__fx_rates') }}
),

last_rate_in_month as (
    select
        months.month_start,
        fx_rates.currency,
        fx_rates.rate_date,
        fx_rates.rate_to_eur
    from months
    inner join fx_rates
        on
            months.month_end_date >= fx_rates.rate_date
            and months.month_end_date - interval 10 day < fx_rates.rate_date
    qualify row_number() over (
        partition by months.month_start, fx_rates.currency
        order by fx_rates.rate_date desc
    ) = 1
)

select * from last_rate_in_month
