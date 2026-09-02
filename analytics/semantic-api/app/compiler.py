"""Compiles a validated request into parameterised SQL. **Never accepts raw SQL.**

Frozen contract 03 rule 2: the API takes ``{metric, dimensions[], filters{}, order_by, limit}`` and
compiles it from the contract. Anything not declared is rejected with 400 *before a query is
planned*.

The property that makes that true is structural rather than a matter of careful escaping: **every
identifier emitted here is looked up in the metric definition first**, and every value is a bound
parameter. A caller-supplied string is never concatenated into SQL. There is no code path that
executes caller-supplied SQL, because there is no code path that turns a caller string into an
identifier — ``psycopg2.sql.Identifier`` is only ever constructed from a name that was already
matched against ``metric.dimensions`` or ``metric.filters``.

That is why a payload like ``{"dimensions": ["1; DROP TABLE marts.x --"]}`` produces a 400 naming an
undeclared dimension, not an escaped-but-executed statement: it fails the allow-list before any SQL
exists.
"""

from __future__ import annotations

import datetime as dt

from psycopg2 import sql

#: Aggregations the compiler can emit, mapped to their SQL. A metric declaring anything else fails
#: the schema, so this map and the schema's enum must agree.
AGGREGATIONS = {
    "sum": "SUM",
    "avg": "AVG",
    "count": "COUNT",
    "count_distinct": "COUNT",  # DISTINCT is added separately
}

#: Aggregations a ratio's numerator or denominator may use. Enumerated, not free-form, so a ratio
#: cannot smuggle an expression into the SELECT list.
#:
#: ``count_true`` exists because ``SUM(boolean)`` is not valid in Postgres, and the obvious
#: workaround -- ``SUM(col::int)`` -- silently truncates when someone later points it at a numeric
#: column. ``COUNT(*) FILTER (WHERE col)`` is exact and fails loudly on a non-boolean.
RATIO_AGGREGATIONS = {"sum", "count", "count_true"}

MAX_LIMIT_CEILING = 10000

#: The explicit UNASSIGNED member of ``dim_operating_unit`` (``is_unassigned = true``, code
#: ``UNASSIGNED``). Verified against the live warehouse: every mart carries a NOT NULL
#: ``operating_unit_id`` and unassigned rows are represented as ``-1``, never as SQL NULL.
#:
#: This matters more than it looks. An earlier version of this compiler mapped an empty
#: ``allowed_ou`` to ``operating_unit_id IS NULL``, reasoning by analogy with Odoo, where an empty
#: entitlement means "only documents that carry no Operating Unit". In the warehouse those
#: documents carry ``-1``, so ``IS NULL`` matched nothing at all: a user with no entitlement would
#: have seen an empty dashboard forever, including the unassigned rows they ARE entitled to. The
#: dimension convention and the source convention are not the same thing, and assuming they were is
#: the same class of mistake that produced the allowed_ou escalation in the first place.
UNASSIGNED_OPERATING_UNIT_ID = -1

#: Safe date derivations a metric may declare. Deliberately a fixed map rather than free-form SQL:
#: these come from the version-controlled metric contract, not from a request, but keeping them to
#: an enumerated set means "the API never accepts raw SQL" stays true without qualification.
DATE_GRAINS = {
    "day": "day",
    "week": "week",
    "month": "month",
    "quarter": "quarter",
    "year": "year",
}


class QueryRejected(Exception):
    """The request named something the contract does not declare. Always a 400."""

    def __init__(self, detail: str, field: str = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.field = field


def _check_date(value, field):
    if isinstance(value, dt.date):
        return value.isoformat()
    if not isinstance(value, str):
        raise QueryRejected("Filter %r expects ISO dates." % field, field)
    try:
        dt.date.fromisoformat(value)
    except ValueError:
        raise QueryRejected(
            "Filter %r expects an ISO date (YYYY-MM-DD), got %r." % (field, value), field
        ) from None
    return value


def _ratio_side(side):
    """Render one half of a ratio. ``side`` comes from the metric file, never from a request."""
    column = sql.Identifier(side["measure"])
    agg = side["agg"]
    if agg == "count_true":
        return sql.SQL("COUNT(*) FILTER (WHERE {})").format(column)
    if agg == "count":
        return sql.SQL("COUNT({})").format(column)
    return sql.SQL("SUM({})").format(column)


def _dimension_expr(metric, name):
    """The SQL for a dimension, derived or plain. Used by ORDER BY inside a window."""
    derived = metric.derived_dimensions.get(name)
    if derived:
        return sql.SQL("date_trunc({}, {})::date").format(
            sql.Literal(DATE_GRAINS[derived["grain"]]), sql.Identifier(derived["from"])
        )
    return sql.Identifier(name)


def compile_query(metric, dimensions, filters, order_by, limit, tenant_id, allowed_ou, all_ou,
                  max_limit=5000):
    """Return ``(psycopg2.sql.Composed, params)``.

    ``tenant_id`` is bound as a parameter here *in addition to* being set as the RLS session
    variable by :mod:`app.db`. Contract 02 requires both: RLS is the enforcement, and the bound
    predicate means a mistake in the RLS policy shows up as an empty result rather than a leak.
    """
    dimensions = list(dimensions or [])
    filters = dict(filters or {})

    # -- allow-list every dimension ---------------------------------------------------
    for dimension in dimensions:
        if not isinstance(dimension, str) or dimension not in metric.dimensions:
            raise QueryRejected(
                "Dimension %r is not declared for metric %r. Declared: %s."
                % (dimension, metric.name, ", ".join(metric.dimensions)),
                "dimensions",
            )
    if len(set(dimensions)) != len(dimensions):
        raise QueryRejected("Duplicate dimension in request.", "dimensions")

    # -- allow-list every filter ------------------------------------------------------
    for key in filters:
        if key not in metric.filters:
            raise QueryRejected(
                "Filter %r is not declared for metric %r. Declared: %s."
                % (key, metric.name, ", ".join(sorted(metric.filters)) or "(none)"),
                "filters",
            )
    for key, spec in metric.filters.items():
        if spec.get("required") and key not in filters:
            raise QueryRejected(
                "Filter %r is required for metric %r." % (key, metric.name), "filters"
            )

    # -- limit ------------------------------------------------------------------------
    if limit is None:
        limit = 1000
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise QueryRejected("limit must be a positive integer.", "limit")
    # The browser never receives more rows than it renders (contract 02). The ceiling is applied
    # rather than rejected so a generous client gets data, not an error.
    limit = min(limit, max_limit, MAX_LIMIT_CEILING)

    # A growth metric is meaningless without the dimension it grows over: without it every row is
    # its own group and lag() has nothing to look back at, so every value would be NULL. Rejected
    # rather than served as an empty column.
    if metric.aggregation == "period_growth" and metric.growth_over not in dimensions:
        raise QueryRejected(
            "Metric %r reports growth over %r, so that dimension must be requested. Without it "
            "there is no prior period to compare against and every value would be null."
            % (metric.name, metric.growth_over),
            "dimensions",
        )

    params = []
    select_parts = []
    group_parts = []

    for dimension in dimensions:
        derived = metric.derived_dimensions.get(dimension)
        if derived:
            # date_trunc over a declared base column at a declared grain. Both halves come from the
            # metric contract and are validated against DATE_GRAINS, so no caller string reaches SQL.
            grain = DATE_GRAINS[derived["grain"]]
            expression = sql.SQL("date_trunc({}, {})::date").format(
                sql.Literal(grain), sql.Identifier(derived["from"])
            )
            select_parts.append(sql.SQL("{} AS {}").format(expression, sql.Identifier(dimension)))
            group_parts.append(expression)
        else:
            select_parts.append(sql.Identifier(dimension))
            group_parts.append(sql.Identifier(dimension))

    if metric.aggregation == "ratio":
        # numerator / denominator, both aggregated over the SAME model and the same GROUP BY.
        # NULLIF guards division by zero: a group with an empty denominator yields NULL, which is
        # honest -- "no rate" is not "a rate of zero", and a chart that plots 0 for an empty
        # denominator is asserting something the data does not say.
        value_expr = sql.SQL("({})::numeric / NULLIF({}, 0)").format(
            _ratio_side(metric.numerator), _ratio_side(metric.denominator)
        )
    elif metric.aggregation == "period_growth":
        # (this period - prior period) / prior period, over a declared time dimension.
        # Frontend refuses to compute ratios in React and is right to: the browser would have to
        # re-sort, re-window and re-divide server-side aggregates, which is business logic living
        # in a component. The window runs over the grouped rows, so the growth dimension must be
        # requested -- enforced below, not assumed.
        base = sql.SQL("SUM({})").format(sql.Identifier(metric.measure))
        order = _dimension_expr(metric, metric.growth_over)
        prior = sql.SQL("lag({}) OVER (ORDER BY {})").format(base, order)
        value_expr = sql.SQL("({} - {})::numeric / NULLIF({}, 0)").format(base, prior, prior)
    elif metric.aggregation == "count_distinct":
        value_expr = sql.SQL("COUNT(DISTINCT {})").format(sql.Identifier(metric.measure))
    elif metric.aggregation == "count":
        value_expr = sql.SQL("COUNT({})").format(sql.Identifier(metric.measure))
    else:
        value_expr = sql.SQL("{}({})").format(
            sql.SQL(AGGREGATIONS[metric.aggregation]), sql.Identifier(metric.measure)
        )
    select_parts.append(sql.SQL("{} AS {}").format(value_expr, sql.Identifier("value")))

    # -- WHERE ------------------------------------------------------------------------
    where = [sql.SQL("{} = %s").format(sql.Identifier("tenant_id"))]
    params.append(tenant_id)

    # Operating Unit scoping, per contract 02 as amended by ruling a0fbb88:
    #   all_ou = true  -> no OU predicate at all (the explicit bypass group)
    #   all_ou = false -> restrict to allowed_ou, and an EMPTY list means NO Operating Units,
    #                     which mirrors custom_operating_unit's record rules: they fail closed.
    # Reading [] as "everything" here would give a user more in the dashboard than in Odoo.
    if not all_ou:
        if allowed_ou:
            where.append(sql.SQL("{} = ANY(%s)").format(sql.Identifier("operating_unit_id")))
            params.append(list(allowed_ou))
        else:
            # No entitlement: the user sees only the UNASSIGNED member, mirroring Odoo's record
            # rules, which fail closed on an empty entitlement. See UNASSIGNED_OPERATING_UNIT_ID
            # for why this is -1 and emphatically not IS NULL.
            where.append(sql.SQL("{} = %s").format(sql.Identifier("operating_unit_id")))
            params.append(UNASSIGNED_OPERATING_UNIT_ID)

    for key, value in filters.items():
        spec = metric.filters[key]
        kind = spec.get("type")
        if kind == "daterange":
            if not isinstance(value, (list, tuple)) or len(value) != 2:
                raise QueryRejected(
                    "Filter %r expects [start, end] ISO dates." % key, "filters"
                )
            start = _check_date(value[0], key)
            end = _check_date(value[1], key)
            if start > end:
                raise QueryRejected(
                    "Filter %r has start after end (%s > %s)." % (key, start, end), "filters"
                )
            column = sql.Identifier(spec.get("column", "date_day"))
            where.append(sql.SQL("{} >= %s AND {} <= %s").format(column, column))
            params.extend([start, end])
        elif kind in ("int[]", "string[]"):
            if not isinstance(value, (list, tuple)) or not value:
                raise QueryRejected("Filter %r expects a non-empty array." % key, "filters")
            if kind == "int[]":
                try:
                    coerced = [int(v) for v in value]
                except (TypeError, ValueError):
                    raise QueryRejected("Filter %r expects integers." % key, "filters") from None
            else:
                if any(not isinstance(v, str) for v in value):
                    raise QueryRejected("Filter %r expects strings." % key, "filters")
                coerced = list(value)
            where.append(sql.SQL("{} = ANY(%s)").format(sql.Identifier(key)))
            params.append(coerced)
        elif kind == "int":
            if isinstance(value, bool) or not isinstance(value, int):
                raise QueryRejected("Filter %r expects an integer." % key, "filters")
            where.append(sql.SQL("{} = %s").format(sql.Identifier(key)))
            params.append(value)
        elif kind == "string":
            if not isinstance(value, str):
                raise QueryRejected("Filter %r expects a string." % key, "filters")
            where.append(sql.SQL("{} = %s").format(sql.Identifier(key)))
            params.append(value)
        else:  # pragma: no cover - the schema constrains the enum
            raise QueryRejected("Filter %r has unsupported type %r." % (key, kind), "filters")

    # -- ORDER BY ---------------------------------------------------------------------
    order_sql = None
    if order_by:
        if not isinstance(order_by, str):
            raise QueryRejected("order_by must be a string.", "order_by")
        field = order_by
        direction = sql.SQL("ASC")
        if field.startswith("-"):
            field = field[1:]
            direction = sql.SQL("DESC")
        # 'value' is the computed measure; anything else must be a requested dimension. Ordering by
        # a column that is not selected would need it in GROUP BY, and allowing an arbitrary name
        # here is exactly the hole the allow-list exists to close.
        if field == "value":
            order_sql = sql.SQL("{} {}").format(sql.Identifier("value"), direction)
        elif field in dimensions:
            order_sql = sql.SQL("{} {}").format(sql.Identifier(field), direction)
        else:
            raise QueryRejected(
                "order_by %r must be 'value' or one of the requested dimensions (%s)."
                % (order_by, ", ".join(dimensions) or "none"),
                "order_by",
            )

    statement = sql.SQL("SELECT {select} FROM {model} WHERE {where}").format(
        select=sql.SQL(", ").join(select_parts),
        model=sql.Identifier("marts", metric.source_model),
        where=sql.SQL(" AND ").join(where),
    )
    if group_parts:
        statement = statement + sql.SQL(" GROUP BY ") + sql.SQL(", ").join(group_parts)
    if order_sql is not None:
        statement = statement + sql.SQL(" ORDER BY ") + order_sql
    statement = statement + sql.SQL(" LIMIT %s")
    params.append(limit)

    return statement, params
