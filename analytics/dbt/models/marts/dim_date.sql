-- The date dimension: Indonesian fiscal calendar and national holidays.
--
-- It carries tenant_id like every other dimension, which is why it is a cross
-- join with the tenant registry rather than a single shared calendar. That is a
-- deliberate cost: three years x N tenants is a few thousand rows, and the
-- alternative - one dimension without tenant_id - would be the single row in
-- the star that RLS could not protect and that a cross-tenant join could use as
-- a bridge.
--
-- HOLIDAYS come from the seed, which is honest about which dates are decreed
-- and which are computed. See seeds/id_public_holiday.csv - Islamic and lunar
-- holidays move with sighting and with the annual SKB, and the seed marks those
-- rows is_estimated.

with tenants as (
    select tenant_id from {{ source('warehouse', 'tenant_registry') }}
    where active
),

spine as (
    select generate_series(
        date '{{ var("date_spine_start") }}',
        date '{{ var("date_spine_end") }}',
        interval '1 day'
    )::date as date_day
),

holidays as (
    select
        holiday_date,
        holiday_name,
        holiday_type,
        is_estimated
    from {{ ref('id_public_holiday') }}
),

base as (
    select
        t.tenant_id,
        s.date_day,
        h.holiday_name,
        h.holiday_type,
        extract(year from s.date_day)::int as calendar_year,
        extract(month from s.date_day)::int as calendar_month,
        extract(day from s.date_day)::int as day_of_month,
        extract(quarter from s.date_day)::int as calendar_quarter,
        extract(week from s.date_day)::int as iso_week,
        extract(isodow from s.date_day)::int as iso_day_of_week,
        extract(doy from s.date_day)::int as day_of_year,
        coalesce(h.is_estimated, false) as holiday_is_estimated
    from spine as s
    cross join tenants as t
    left join holidays as h on s.date_day = h.holiday_date
)

select
    {{ surrogate_key(['b.tenant_id', 'b.date_day']) }} as date_key,
    b.tenant_id,
    b.date_day,
    b.calendar_year,
    b.calendar_quarter,
    b.calendar_month,
    b.day_of_month,
    b.day_of_year,
    b.iso_week,
    b.iso_day_of_week,
    date_trunc('month', b.date_day)::date as month_start_date,
    (date_trunc('month', b.date_day) + interval '1 month - 1 day')::date as month_end_date,
    date_trunc('quarter', b.date_day)::date as quarter_start_date,
    date_trunc('year', b.date_day)::date as year_start_date,
    to_char(b.date_day, 'YYYY-MM') as month_key,

    -- Indonesian labels. The dashboard is Indonesian-language (metric contract
    -- labels are), and formatting a month name in the presentation layer is how
    -- two screens end up disagreeing about what "Mei" is.
    (array['Senin', 'Selasa', 'Rabu', 'Kamis', 'Jumat', 'Sabtu', 'Minggu'])[b.iso_day_of_week] as day_name_id,
    (array[
        'Januari', 'Februari', 'Maret', 'April', 'Mei', 'Juni',
        'Juli', 'Agustus', 'September', 'Oktober', 'November', 'Desember'
    ])[b.calendar_month] as month_name_id,

    -- Fiscal calendar. fiscal_year_start_month is a var, not a literal: a
    -- tenant on a non-calendar fiscal year is configuration, not a rewrite.
    -- At the Indonesian default of 1 the fiscal and calendar years coincide,
    -- and the arithmetic below still holds for any other start month.
    case
        when b.calendar_month >= {{ var('fiscal_year_start_month') }}
            then b.calendar_year
        else b.calendar_year - 1
    end as fiscal_year,
    (((b.calendar_month - {{ var('fiscal_year_start_month') }} + 12) % 12) + 1) as fiscal_month_index,
    ((((b.calendar_month - {{ var('fiscal_year_start_month') }} + 12) % 12) / 3) + 1) as fiscal_quarter,

    (b.iso_day_of_week >= 6) as is_weekend,
    (b.holiday_name is not null) as is_national_holiday,
    b.holiday_name,
    b.holiday_type,
    b.holiday_is_estimated,
    -- A working day in Indonesia: Monday-Friday and not a national holiday.
    (b.iso_day_of_week < 6 and b.holiday_name is null) as is_business_day
from base as b
