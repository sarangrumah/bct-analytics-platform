{# =========================================================================
   Project macros. Deliberately few, and each one exists because the rule it
   encodes must be identical everywhere it is used.
   ========================================================================= #}


{#- --------------------------------------------------------------------------
    generate_schema_name — use the custom schema VERBATIM.

    dbt's default concatenates target schema and custom schema, so a model
    configured `+schema: staging` against target schema `marts` would land in
    `marts_staging`. Contract 05 freezes the four schema names; a mart in
    `marts_marts` is not the contract.
--------------------------------------------------------------------------- #}
{% macro generate_schema_name(custom_schema_name, node) -%}
{%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
{{ custom_schema_name | trim }}
{%- endif -%}
{%- endmacro %}


{#- --------------------------------------------------------------------------
    surrogate_key — the deterministic hash surrogate.

    THE STRATEGY, stated here because the master prompt asks for it by name and
    because every fact in this project depends on it:

    A dimension key is md5 of the natural key, and the natural key ALWAYS
    starts with tenant_id. Two consequences that are the whole reason for the
    choice:

      1. A fact can compute its own foreign key from the column it already
         holds, without looking the dimension up. fct_sale_order_line computes
         partner_key from (tenant_id, partner_id) directly. So a fact never
         waits for a dimension to be built, there is no late-arriving-dimension
         deadlock, and the two can be rebuilt in either order.
      2. The key is stable across a full refresh. A sequence-backed surrogate
         is not: drop and rebuild the dimension and every historical key
         changes, silently repointing every fact that stored one.

    Two NULL hazards, both handled:
      * a NULL component becomes the sentinel '_dbt_null_' rather than
        collapsing the whole concatenation to NULL;
      * components are joined with '|', which cannot appear in an integer id
        or a tenant slug (the slug regex is ^[a-z][a-z0-9_]{1,30}$), so
        ('bct', 12) and ('bct1', 2) cannot collide.
--------------------------------------------------------------------------- #}
{% macro surrogate_key(field_list) -%}
md5(concat_ws('|'
{%- for field in field_list %}
        , coalesce(cast({{ field }} as text), '_dbt_null_')
{%- endfor %}
    ))
{%- endmacro %}


{#- --------------------------------------------------------------------------
    raw_latest — read a landing table the way contract 05 says it must be read.

    Three rules in one place, because getting any of them wrong produces a mart
    that is quietly wrong rather than one that fails:

      1. LATEST VERSION PER KEY. The landing zone is append-only, so an UPDATE
         is a second row. Ordering is (_lsn, _ingested_at, _row_id) descending.
      2. THE LOWEST _lsn SORTS FIRST. A snapshot/backfill row has no WAL
         position and is written with '0/0', the lowest possible pg_lsn, so
         every streamed CDC row supersedes every snapshot row for the same key
         - the correct precedence, and what makes a re-snapshot safe to run
         over live data. The coalesce() is kept as a belt-and-braces guard for
         any producer that still writes NULL: it gives the same answer, and
         costs nothing.
      3. TOMBSTONES. If the surviving version is _op='D', the row is gone —
         not filtered before ranking, which would resurrect the previous
         version of a deleted record.
--------------------------------------------------------------------------- #}
{% macro raw_latest(table, pk='id') -%}
    select v.*
    from (
        select r.*,
               row_number() over (
                   partition by r._tenant_id, r.{{ pk }}
                   order by coalesce(r._lsn, '0/0'::pg_lsn) desc,
                            r._ingested_at desc,
                            r._row_id desc
               ) as _version_rank
        from {{ source('raw', table) }} r
    ) v
    where v._version_rank = 1
      and v._op <> 'D'
{%- endmacro %}


{#- --------------------------------------------------------------------------
    apply_rls — the storage-layer tenant boundary, as a post-hook.

    Delegates to warehouse.apply_tenant_rls(), which is defined once in
    analytics/warehouse/init/sql/60-functions.sql so the policy text exists in
    exactly one place. The function raises if the relation has no tenant_id
    column, which is how "every fact and dimension carries tenant_id" is
    enforced rather than reviewed.
--------------------------------------------------------------------------- #}
{% macro apply_rls() -%}
select warehouse.apply_tenant_rls('{{ this.schema }}', '{{ this.identifier }}')
{%- endmacro %}


{#- --------------------------------------------------------------------------
    active_tenants — the tenant list, read from warehouse.tenant_registry.

    Read from the database rather than declared as a dbt var, so onboarding a
    tenant is one INSERT into the registry (which is what
    scripts/tenant-provision.sh will do) and not an edit to dbt_project.yml
    that somebody forgets.
--------------------------------------------------------------------------- #}
{% macro active_tenants() -%}
{%- if execute -%}
        {%- set results = run_query(
            "select tenant_id from warehouse.tenant_registry where active order by tenant_id"
        ) -%}
        {{ return(results.columns[0].values()) }}
    {%- else -%}
{{ return([]) }}
{%- endif -%}
{%- endmacro %}


{#- --------------------------------------------------------------------------
    log_dbt_results — persist the run into warehouse.dbt_run_result.

    This is what turns "a reconciliation test failed" from a red line in a
    terminal into a Prometheus series and an alert. The exporter reads only the
    most recent invocation, so a fixed failure stops firing.

    `failures` is the row count a test returned; dbt's convention is that a
    test passes when it returns none, so the healthy value is 0.
--------------------------------------------------------------------------- #}
{% macro log_dbt_results(results) -%}
{%- if execute and results | length > 0 -%}
{%- set rows = [] -%}
{%- for r in results -%}
            {%- set _ = rows.append(
                "(" ~ dbt.string_literal(invocation_id)
                ~ "," ~ dbt.string_literal(run_started_at.strftime('%Y-%m-%d %H:%M:%S%z')) ~ "::timestamptz"
                ~ "," ~ dbt.string_literal(r.node.unique_id)
                ~ "," ~ dbt.string_literal(r.node.resource_type)
                ~ "," ~ dbt.string_literal(r.node.name)
                ~ "," ~ dbt.string_literal(r.status | string)
                ~ "," ~ dbt.string_literal((r.node.config.severity | default('none')) | string | lower)
                ~ "," ~ (r.adapter_response.get('rows_affected', 'null') | default('null', true))
                ~ "," ~ (r.failures if r.failures is not none else 'null')
                ~ "," ~ (r.execution_time | round(4))
                ~ "," ~ dbt.string_literal((r.message | default('')) | string | truncate(500, true, ''))
                ~ ")"
            ) -%}
        {%- endfor %}
        insert into warehouse.dbt_run_result
            (invocation_id, run_started_at, node_id, resource_type, node_name,
             status, severity, rows_affected, failures, execution_time, message)
        values {{ rows | join(',\n               ') }}
        on conflict (invocation_id, node_id) do update set
            status = excluded.status,
            failures = excluded.failures,
            execution_time = excluded.execution_time,
            message = excluded.message
    {%- else -%}
select 1
{%- endif -%}
{%- endmacro %}
