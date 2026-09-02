"""The fixture set must match the registry. Frontend's test cannot check this, and here is why.

`insight-portal/tests/contract-shape.test.ts` parses *every file present* in
`analytics/semantic-api/metrics/fixtures/` through the same type guards the app uses on live
responses. That is the right design and it catches a malformed fixture immediately. What it
structurally cannot catch is a fixture that is **not there**: it iterates `readdirSync`, so a
metric declared in `core.yml` and never generated produces no file, no iteration, and no failure.
Seven correct fixtures for a ten-metric registry is a green run.

That is the empty-result tell one directory over: the check asks "is anything present malformed?",
gets "no", and has no way to distinguish that from "the thing you wanted was never written". The
missing half belongs on the producing side -- here -- because only the registry knows what the set
is supposed to be.

Made to go red: deleting `fixtures/account_balance.json` fails
`test_every_registry_metric_has_a_fixture` naming it, while the whole of Frontend's suite stays
green. Restored, both pass.
"""

from __future__ import annotations

import json
import os

import pytest

from app.registry import load_registry

METRICS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "metrics")
FIXTURES_DIR = os.path.join(METRICS_DIR, "fixtures")


@pytest.fixture(scope="module")
def registry():
    return load_registry(METRICS_DIR)


def _fixture_names():
    return {
        name[: -len(".json")]
        for name in os.listdir(FIXTURES_DIR)
        if name.endswith(".json") and not name.startswith("_")
    }


def test_the_registry_is_not_empty(registry):
    """The non-empty subject assertion every check below depends on.

    Without it, an empty registry would satisfy "every metric has a fixture" and "no fixture is
    orphaned" simultaneously and perfectly.
    """
    assert len(registry.names()) >= 10, (
        "registry holds %d metrics; the set-equality tests below are vacuous on an empty or "
        "truncated registry" % len(registry.names())
    )


def test_every_registry_metric_has_a_fixture(registry):
    missing = sorted(set(registry.names()) - _fixture_names())
    assert not missing, (
        "declared in metrics/*.yml but absent from metrics/fixtures/: %s. Frontend is building "
        "against this directory and its own test cannot see an absent file. Run "
        "`make metric-fixtures ARGS=--offline` (or with a token for a live transcript)."
        % ", ".join(missing)
    )


def test_no_fixture_is_orphaned(registry):
    """A fixture for a metric that no longer exists is worse than a missing one.

    Frontend's guards would accept it -- the envelope is well formed -- so it renders as a real
    panel for a metric the API answers 400 for.
    """
    orphans = sorted(_fixture_names() - set(registry.names()))
    assert not orphans, (
        "fixtures with no metric behind them: %s. The API would 400 on each." % ", ".join(orphans)
    )


def test_the_catalogue_lists_exactly_the_registry(registry):
    with open(os.path.join(FIXTURES_DIR, "_catalogue.json"), encoding="utf-8") as handle:
        catalogue = json.load(handle)
    listed = [m["name"] for m in catalogue["metrics"]]
    assert listed, "_catalogue.json lists no metrics at all"
    assert sorted(listed) == sorted(registry.names()), (
        "_catalogue.json is stale: lists %s, registry holds %s"
        % (sorted(listed), sorted(registry.names()))
    )


def test_each_catalogue_entry_carries_the_dimensions_the_registry_declares(registry):
    """Catches the half-regenerated case: the right file names, the wrong contents.

    Adding a dimension is a backwards-compatible contract change (contract 03 rule 5), so it does
    NOT add or remove a fixture file -- the set-equality tests above stay green while Frontend
    reads a catalogue that has never heard of the new dimension.
    """
    with open(os.path.join(FIXTURES_DIR, "_catalogue.json"), encoding="utf-8") as handle:
        catalogue = json.load(handle)
    by_name = {m["name"]: m for m in catalogue["metrics"]}
    drift = []
    for metric in registry.all():
        entry = by_name.get(metric.name)
        if entry is None:
            continue  # already reported by the set-equality test; do not double-count
        if list(entry.get("dimensions") or []) != list(metric.dimensions):
            drift.append(
                "%s: catalogue %s vs registry %s"
                % (metric.name, entry.get("dimensions"), metric.dimensions)
            )
        if sorted((entry.get("filters") or {}).keys()) != sorted(metric.filters.keys()):
            drift.append(
                "%s: catalogue filters %s vs registry %s"
                % (metric.name, sorted((entry.get("filters") or {}).keys()),
                   sorted(metric.filters.keys()))
            )
    assert not drift, "regenerate the fixtures; the catalogue has drifted:\n  " + "\n  ".join(drift)
