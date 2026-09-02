{#- --------------------------------------------------------------------------
    unique_combination — a composite-key uniqueness test.

    dbt ships `unique` for a single column and nothing for a composite key. In
    a multi-tenant warehouse almost every natural key IS composite, because it
    starts with tenant_id: sale_order_line_id 42 exists in every tenant and a
    single-column `unique` on it would fail on correct data.

    dbt_utils.unique_combination_of_columns does this, and this project
    deliberately installs no packages (see dbt_project.yml). Twelve lines of
    local SQL against a run-time fetch from hub.getdbt.com is the right trade.

    Usage:
        data_tests:
          - unique_combination:
              combination_of_columns: [tenant_id, sale_order_line_id]
--------------------------------------------------------------------------- #}
{% test unique_combination(model, combination_of_columns) %}

{%- set column_list = combination_of_columns | join(', ') -%}

select
    {{ column_list }},
    count(*) as duplicate_row_count
from {{ model }}
group by {{ column_list }}
having count(*) > 1

{% endtest %}
