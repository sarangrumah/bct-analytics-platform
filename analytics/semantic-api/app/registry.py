"""Metric definitions: loaded from YAML, validated against the schema, and the only source of truth.

Frozen contract 03: one place defines each metric, and the front-end never hand-writes business
logic in SQL or TypeScript. This module is that place. A metric that fails the schema fails the
build — :func:`load_registry` raises, the service does not start, and CI goes red.
"""

from __future__ import annotations

import json
import logging
import os

import jsonschema
import yaml

_logger = logging.getLogger(__name__)


class MetricDefinitionError(RuntimeError):
    """A metric file is invalid. Fatal at startup: a half-valid registry is not servable."""


class Metric:
    def __init__(self, raw: dict) -> None:
        self.raw = raw
        self.name = raw["name"]
        self.label = raw["label"]
        self.description = raw.get("description", "")
        self.grain = list(raw["grain"])
        self.dimensions = list(raw["dimensions"])
        self.filters = dict(raw["filters"])
        self.type = raw["type"]
        self.unit = raw.get("unit")
        self.aggregation = raw["aggregation"]
        self.source_model = raw["source_model"]
        self.measure = raw.get("measure")
        self.numerator = raw.get("numerator")
        self.denominator = raw.get("denominator")
        self.growth_over = raw.get("growth_over")
        self.refresh_sla_seconds = int(raw["refresh_sla_seconds"])
        self.pdp_class = raw["pdp_class"]
        self.derived_dimensions = dict(raw.get("derived_dimensions") or {})
        self.channel_note = raw.get("channel_note")

    def __repr__(self) -> str:
        return "<Metric %s -> %s>" % (self.name, self.source_model)


class Registry:
    def __init__(self, metrics: dict) -> None:
        self._metrics = metrics

    def __contains__(self, name) -> bool:
        return name in self._metrics

    def __len__(self) -> int:
        return len(self._metrics)

    def get(self, name):
        return self._metrics.get(name)

    def names(self) -> list:
        return sorted(self._metrics)

    def all(self) -> list:
        return [self._metrics[n] for n in self.names()]


def load_registry(directory: str) -> Registry:
    schema_path = os.path.join(directory, "metric.schema.json")
    with open(schema_path, encoding="utf-8") as handle:
        schema = json.load(handle)

    metrics = {}
    problems = []
    for filename in sorted(os.listdir(directory)):
        if not filename.endswith((".yml", ".yaml")):
            continue
        path = os.path.join(directory, filename)
        with open(path, encoding="utf-8") as handle:
            documents = yaml.safe_load(handle) or []
        if not isinstance(documents, list):
            problems.append("%s: top level must be a list of metrics" % filename)
            continue
        for entry in documents:
            try:
                jsonschema.validate(entry, schema)
            except jsonschema.ValidationError as exc:
                problems.append(
                    "%s: metric %r failed the schema at %s: %s"
                    % (filename, entry.get("name", "<unnamed>"),
                       "/".join(str(p) for p in exc.absolute_path) or "<root>", exc.message)
                )
                continue

            metric = Metric(entry)

            # Contract 03 rule 1: tenant_id is always in grain. A metric that can be computed
            # across tenants does not exist -- so this is checked, not assumed. The schema cannot
            # express it, which is exactly why it is here.
            if "tenant_id" not in metric.grain:
                problems.append(
                    "%s: metric %r omits tenant_id from grain. Contract 03 rule 1: a metric that "
                    "can be computed across tenants does not exist." % (filename, metric.name)
                )

            # Contract 03 rule: a metric may never expose a `secret` class.
            if metric.pdp_class == "secret":
                problems.append(
                    "%s: metric %r is pdp_class=secret. A secret-class column is dropped at "
                    "extraction and does not exist in the warehouse, so a metric over one cannot "
                    "be computed at all." % (filename, metric.name)
                )

            # Every grain key must also be a legal group-by, or the metric cannot be queried at
            # the grain it declares.
            ungroupable = [g for g in metric.grain if g not in metric.dimensions]
            if ungroupable:
                problems.append(
                    "%s: metric %r declares grain %s that are not in dimensions, so it cannot be "
                    "requested at its own grain." % (filename, metric.name, ungroupable)
                )

            # Shape rules the JSON schema cannot express, because they are conditional on
            # `aggregation`. Checked here so a malformed metric fails the BUILD rather than
            # returning a plausible number at query time.
            if metric.aggregation == "ratio":
                if not (metric.numerator and metric.denominator):
                    problems.append(
                        "%s: metric %r is a ratio and must declare both numerator and denominator."
                        % (filename, metric.name)
                    )
            elif metric.aggregation == "period_growth":
                if not metric.measure:
                    problems.append(
                        "%s: metric %r is period_growth and must declare a measure."
                        % (filename, metric.name)
                    )
                if not metric.growth_over:
                    problems.append(
                        "%s: metric %r is period_growth and must declare growth_over."
                        % (filename, metric.name)
                    )
                elif metric.growth_over not in metric.dimensions:
                    problems.append(
                        "%s: metric %r grows over %r, which is not one of its dimensions, so it "
                        "could never be requested." % (filename, metric.name, metric.growth_over)
                    )
            elif not metric.measure:
                problems.append(
                    "%s: metric %r must declare a measure." % (filename, metric.name)
                )

            # TRAP 1, and it is not a hypothetical: mart_ppob_transaction carries both
            # `pass_through_amount` (money owed to the biller -- not ours, not revenue) and
            # `commission_revenue` (what we actually earn). Measured on live data, binding the
            # wrong one overstates revenue by 481x. That is not an error a reviewer eyeballs in a
            # YAML diff, so it is refused here.
            if (
                (metric.measure or "").startswith("pass_through")
                and metric.aggregation == "sum"
                and (metric.unit or "").upper() == "IDR"
            ):
                problems.append(
                    "%s: metric %r sums %r as an IDR amount. A pass-through is money owed to the "
                    "biller: it is not revenue, and binding it here overstates revenue by orders "
                    "of magnitude (measured at 481x on live data). Use commission_revenue."
                    % (filename, metric.name, metric.measure)
                )

            # TRAP 2: mart_revenue_daily is three channels UNIONed, not summed. Summing across
            # revenue_channel must be a declared decision, not an accident of a missing GROUP BY.
            if metric.source_model == "mart_revenue_daily" and not metric.channel_note:
                problems.append(
                    "%s: metric %r reads mart_revenue_daily, which UNIONs revenue_channel in "
                    "(invoice, pos, ppob_commission). Declare channel_note to confirm that summing "
                    "across channels is intended; credit notes are already netted off inside "
                    "'invoice' and must not be netted again." % (filename, metric.name)
                )

            # A derived dimension must be declared as a dimension too, or it can never be requested.
            for name in metric.derived_dimensions:
                if name not in metric.dimensions:
                    problems.append(
                        "%s: metric %r derives %r but does not list it in dimensions."
                        % (filename, metric.name, name)
                    )

            if metric.name in metrics:
                problems.append("%s: duplicate metric name %r" % (filename, metric.name))
            metrics[metric.name] = metric

    if problems:
        raise MetricDefinitionError(
            "%d metric definition problem(s):\n  - %s" % (len(problems), "\n  - ".join(problems))
        )
    _logger.info("loaded %d metric definitions from %s", len(metrics), directory)
    return Registry(metrics)
