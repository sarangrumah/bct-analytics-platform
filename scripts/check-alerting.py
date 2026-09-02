#!/usr/bin/env python3
"""Fail if the alerting path is not actually armed.

`promtool check rules` proves a rule PARSES. Prometheus reporting `health: ok`
proves it EVALUATED without error. Neither proves it can ever fire, and neither
proves a firing alert would reach anyone:

  * a rule referencing a metric nothing currently emits evaluates cleanly
    forever and is silently inert;
  * a scrape target that is down disarms every rule built on it;
  * Alertmanager being absent means a firing alert goes nowhere at all.

All three are the same failure shape this build keeps meeting - a check that
cannot fail is mistaken for one that passes. ADR 0001 accepted
max_slot_wal_keep_size=2GB *conditional on* slot-lag alerting working, so "the
rules are green" is not a sufficient answer.

--------------------------------------------------------------------------
THIS FILE WAS ITSELF AN INSTANCE OF THE PATTERN IT POLICES. Three bugs, fixed
together, each of which made a check unable to fail:

1. The reachability probe was `get("/-/healthy")`, and `get()` JSON-decoded
   every response. `/-/healthy` is `text/plain` BY DESIGN ("Prometheus Server is
   Healthy."), so the decode raised JSONDecodeError on a perfectly healthy
   Prometheus, a bare `except Exception` swallowed it as "not reachable", and
   the function returned. Every check below that line was UNREACHABLE CODE. This
   gate had never executed one of its own checks, on any run, by anyone.
   Fixed: the probe is `/-/ready` and is NOT decoded; only endpoints that are
   documented JSON go through `get_json()`.

2. `except Exception` around that probe was far too wide - it converted a parse
   bug into a confident, false, "the service is down" message. Fixed: only
   connection-level errors are treated as "not running"; anything else is
   reported as itself.

3. `known = set(get("/api/v1/label/__name__/values")["data"])` decided whether a
   rule's metric exists. That endpoint returns every name Prometheus has EVER
   seen within retention, not names with CURRENT samples, so a metric that
   stopped being emitted still read as present - which is exactly the state this
   check exists to catch. QA hit that residue, filed on it, and retracted.
   Fixed: existence is decided by `count by (__name__)`, i.e. by samples. The
   name list is still fetched, but ONLY to word the failure message
   ("never seen" vs "seen earlier, no current samples"). It never decides.

--------------------------------------------------------------------------
EXIT CODES

    0   armed
    1   a hard failure, including "Prometheus is up but not ready"
    77  SKIP: Prometheus is not running at all

77, not 0. The previous version printed "NOT a pass: slot-lag alerting is
unverified" and then returned 0, so every automated consumer - make, CI,
security/CD-CONTRACT.md's post-deploy health gate, QA's cold-start test - read
success while only a human reading stdout read failure. A check that reports
failure in prose and success in its exit code is worse than no check. 77 is
automake's long-standing SKIP convention and is non-zero, so silence can never
be mistaken for a pass.

The overlay genuinely is optional (`make up-obs`), so there is an escape - but
it is explicit and it appears in the command line, rather than being the
default: ALLOW_SKIP=1 downgrades 77 to 0 and says so. "I know the overlay is
down and I accept unverified alerting" is a decision someone should have to
type.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

PROM = os.environ.get("PROMETHEUS_URL", "http://127.0.0.1:39090")
# Alertmanager is probed DIRECTLY, because asking Prometheus is not enough - see
# alertmanager_is_alive(). Default matches ALERTMANAGER_HOST_PORT in .env.example.
ALERTMANAGER = os.environ.get(
    "ALERTMANAGER_URL",
    "http://127.0.0.1:%s" % os.environ.get("ALERTMANAGER_HOST_PORT", "39093"),
)
TIMEOUT = 10
ALLOW_SKIP = os.environ.get("ALLOW_SKIP", "") not in ("", "0", "false", "no")

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_SKIP = 77

# Metric names that are legitimately absent when the thing they describe does
# not exist. A slot series with zero slots is correct, not broken.
#
# The `bct_cdc_slot_lag_bytes` entry below was DEAD: no such metric exists. The
# consumer emits bct_cdc_up, bct_cdc_slot_invalidated,
# bct_cdc_replication_slot_lag_bytes and bct_cdc_end_to_end_lag_seconds, so the
# exemption never matched anything - which nobody could notice, because bug 1
# meant this code never ran. A prefix rule cannot drift the same way when
# Backend adds the next bct_cdc_* metric.
#
# Downgrading the whole bct_cdc_* family to WARN is safe ONLY because check 1
# independently HARD-FAILS when the `analytics-cdc` scrape target is down, which
# is the same condition. If that scrape job is ever removed from
# observability/prometheus/scrape.d, this exemption becomes a blind spot and
# must go with it.
CONDITIONAL = {
    "pg_replication_slots_pg_wal_lsn_diff": "no replication slot exists yet",
    "pg_replication_slots_active": "no replication slot exists yet",
    "pg_replication_slot_wal_status": "no replication slot exists yet",
}
CONDITIONAL_PREFIXES = {
    "bct_cdc_": "the CDC consumer is not running; check 1 fails on its scrape target",
}


def conditional_reason(metric: str) -> str | None:
    """Why this metric's absence is expected, or None if its absence is a defect."""
    if metric in CONDITIONAL:
        return CONDITIONAL[metric]
    for prefix, why in CONDITIONAL_PREFIXES.items():
        if metric.startswith(prefix):
            return why
    return None

# --------------------------------------------------------------------------
# Metric-name extraction.
#
# The previous single regex - identifier followed by `{`, an operator, `)`, or
# whitespace - also matched LABEL names, because a label matcher is an
# identifier followed by an operator. Nobody had ever seen that, because bug 1
# meant this code never ran; the first execution reported `slot_name`,
# `wal_status`, `on_breach` and `source_table` as metrics that can never fire.
# All four are labels:
#
#   pg_replication_slot_wal_status{wal_status="lost"} == 1
#   max by (tenant, source_table) (bct_cdc_end_to_end_lag_seconds) > 300
#
# So the expression is stripped of everything that is syntactically NOT a metric
# position before any identifier is read: string literals, label-matcher braces,
# range/offset brackets, and by/without/on/ignoring/group_* label lists. What
# survives is then filtered by a rule no metric can violate - a metric name is
# never immediately followed by `(`, which catches any function this file has
# not been taught about.
#
# This is deliberately conservative: it can still MISS a metric (a name with no
# underscore is dropped, see below), it must not INVENT one. A false "can never
# fire" is how a working gate gets switched off.
# --------------------------------------------------------------------------
STRING_RE = re.compile(r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|`[^`]*`')
LABELSET_RE = re.compile(r"\{[^{}]*\}")
BRACKET_RE = re.compile(r"\[[^\[\]]*\]")
GROUPING_RE = re.compile(
    r"\b(?:by|without|on|ignoring|group_left|group_right)\s*\([^()]*\)", re.IGNORECASE
)
IDENT_RE = re.compile(r"([a-zA-Z_:][a-zA-Z0-9_:]*)\s*(\()?")
PROMQL_KEYWORDS = {
    "by", "without", "on", "ignoring", "group_left", "group_right", "offset",
    "bool", "and", "or", "unless", "sum", "min", "max", "avg", "count", "rate",
    "irate", "increase", "delta", "abs", "ceil", "floor", "round", "clamp_max",
    "clamp_min", "histogram_quantile", "topk", "bottomk", "quantile", "stddev",
    "stdvar", "count_values", "absent", "absent_over_time", "changes", "time",
    "vector", "scalar", "predict_linear", "deriv", "humanize", "humanize1024",
    "last_over_time", "avg_over_time", "max_over_time", "min_over_time",
    "sum_over_time", "count_over_time", "label_replace", "group",
}

# Connection-level failures - the ONLY thing that means "Prometheus is not
# running". Deliberately not `Exception`: bug 2 above is what a wide catch does.
# HTTPError is a subclass of URLError and is handled separately, before these.
CONNECTION_ERRORS = (urllib.error.URLError, TimeoutError, ConnectionError, OSError)


def _open(path: str):
    """urlopen a Prometheus path, validating the scheme first.

    PROMETHEUS_URL comes from the environment, so the scheme is validated rather
    than trusted: urlopen happily accepts `file:///etc/passwd`, which would turn
    a monitoring check into a file read. Flagged by ruff S310, and the flag was
    right - the fix is the check, not a suppression.
    """
    url = PROM + path
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            f"refusing to open {parsed.scheme!r} URL; PROMETHEUS_URL must be http or https"
        )
    return urllib.request.urlopen(url, timeout=TIMEOUT)  # noqa: S310 - scheme checked above


def get_json(path: str):
    """GET a path that is DOCUMENTED to return JSON, and decode it.

    Only /api/v1/* qualifies. The operational endpoints (/-/healthy, /-/ready)
    are text/plain and must never come through here - that was bug 1.
    """
    if not path.startswith("/api/"):
        raise ValueError(f"get_json is for /api/* only; {path!r} is not documented as JSON")
    with _open(path) as response:
        return json.load(response)


def query(expr: str):
    """Run an instant query and return its result list, or raise on a non-success body."""
    body = get_json("/api/v1/query?query=" + urllib.parse.quote(expr))
    if body.get("status") != "success":
        raise RuntimeError(f"query {expr!r} returned status={body.get('status')!r}: "
                           f"{body.get('error', '')[:200]}")
    return body["data"]["result"]


def metrics_with_current_samples(names: set[str]) -> set[str]:
    """Which of `names` actually have samples right now.

    One query, not one per metric. `count by (__name__)` returns a series only
    for names with a sample inside the lookback window, which is the question -
    unlike /api/v1/label/__name__/values, which answers "ever seen" and is how
    a dead series reads as healthy.
    """
    if not names:
        return set()
    # Metric names are [a-zA-Z_:][a-zA-Z0-9_:]* so they carry no regex
    # metacharacters and need no escaping. Prometheus anchors label regexes.
    selector = "|".join(sorted(names))
    result = query('count by (__name__) ({__name__=~"%s"})' % selector)
    return {series["metric"]["__name__"] for series in result if "__name__" in series["metric"]}


def metric_names_in(expr: str) -> set[str]:
    """Metric names referenced by a PromQL expression, conservatively.

    Strips every construct in which an identifier is NOT a metric name, then
    keeps identifiers that are not PromQL keywords and are not function calls.

    Known, deliberate limitation: a name without an underscore is dropped, so
    bare `up` is never checked. Widening that would sweep in every stray
    identifier the stripping missed and produce false "can never fire" reports -
    and a gate that cries wolf gets switched off, which is a worse outcome than
    one metric going unchecked. Kept from the original for the same reason.
    """
    cleaned = STRING_RE.sub(" ", expr)
    cleaned = LABELSET_RE.sub(" ", cleaned)
    cleaned = BRACKET_RE.sub(" ", cleaned)
    previous = None
    while previous != cleaned:                 # `sum by (a) (max by (b) (x))`
        previous = cleaned
        cleaned = GROUPING_RE.sub(" ", cleaned)

    names = set()
    for match in IDENT_RE.finditer(cleaned):
        name, call = match.group(1), match.group(2)
        if call:                               # a metric is never followed by `(`
            continue
        if name in PROMQL_KEYWORDS or name.isdigit() or "_" not in name:
            continue
        names.add(name)
    return names


def alertmanager_is_alive(url: str) -> tuple[bool, str]:
    """Is Alertmanager actually answering? Returns (alive, detail).

    WHY THIS EXISTS, and it is the whole point.

    "Prometheus has an active Alertmanager" was checked with
    /api/v1/alertmanagers alone. Under `static_configs` - which is what
    observability/prometheus/prometheus.yml uses - that endpoint reports the
    CONFIGURED target, not a reachable one. Measured: with
    odoo19-bct-alertmanager stopped, Prometheus reported active=1, dropped=0
    continuously for 90 seconds, and the gate printed
    "Alertmanager reachable" while nothing was listening. It could only ever
    have caught an Alertmanager that was never configured.

    That was found by doing what PLAN.md's standing rule requires - trying to
    make a green check go red - and not by reading it. Alertmanager is also not
    a scrape target here, so check 1 does not cover it either; a firing alert
    would have gone nowhere with every gate green.

    A direct probe is the only honest answer. If Alertmanager is not published
    on ALERTMANAGER_URL in some deployment, this reports a failure naming the
    variable rather than passing quietly - unreachable must never be silent.
    """
    ready = url.rstrip("/") + "/-/ready"
    parsed = urllib.parse.urlparse(ready)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return False, f"ALERTMANAGER_URL is not a usable http(s) URL: {url!r}"
    try:
        with urllib.request.urlopen(ready, timeout=TIMEOUT) as response:  # noqa: S310 - checked
            response.read(256)
        return True, ready
    except urllib.error.HTTPError as exc:
        return False, f"{ready} returned HTTP {exc.code} {exc.reason}"
    except CONNECTION_ERRORS as exc:
        detail = f"{exc.__class__.__name__}: {getattr(exc, 'reason', exc)}"
        return False, f"{ready} unreachable ({detail})"


def probe() -> str | None:
    """None if Prometheus answers; a reason string if it is NOT RUNNING.

    Raises for anything that is neither - a running-but-broken Prometheus must
    not be reported as an absent one.
    """
    try:
        with _open("/-/ready") as response:
            response.read(256)          # read, do NOT decode. It is text/plain.
        return None
    except urllib.error.HTTPError as exc:
        if exc.code == 503:
            raise RuntimeError(
                "Prometheus is running but reports NOT READY (503 from /-/ready). "
                "It is still replaying its WAL or starting up; no rule is being "
                "evaluated yet. Re-run in a few seconds."
            ) from exc
        raise RuntimeError(f"/-/ready returned HTTP {exc.code} {exc.reason}") from exc
    except CONNECTION_ERRORS as exc:
        reason = getattr(exc, "reason", exc)
        return f"{exc.__class__.__name__}: {reason}"


def main() -> int:
    # A misconfigured PROMETHEUS_URL must not masquerade as "Prometheus is down".
    # Reporting a config error as a SKIP is the same failure shape as a check
    # that cannot fail: it reads as "nothing to see here".
    parsed = urllib.parse.urlparse(PROM)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        print(f"check-alerting: FAIL - PROMETHEUS_URL is not a usable http(s) URL: {PROM!r}",
              file=sys.stderr)
        return EXIT_FAIL

    try:
        unreachable = probe()
    except (RuntimeError, ValueError) as exc:
        print(f"check-alerting: FAIL - {exc}", file=sys.stderr)
        return EXIT_FAIL

    if unreachable:
        print(f"check-alerting: SKIP - Prometheus is not running at {PROM} ({unreachable}).")
        print("  The observability overlay is optional. Start it with `make up-obs`.")
        print("  NOT a pass: slot-lag alerting is unverified while it is down.")
        if ALLOW_SKIP:
            print(f"  ALLOW_SKIP is set, so this exits 0 instead of {EXIT_SKIP}. "
                  "You have accepted unverified alerting.")
            return EXIT_OK
        print(f"  Exiting {EXIT_SKIP} (skip), not 0. Set ALLOW_SKIP=1 to accept this deliberately.")
        return EXIT_SKIP

    # Everything below talks to the Prometheus API. If PROMETHEUS_URL points at
    # something that answers HTTP but is not Prometheus - Grafana, a proxy, the
    # wrong port - those calls raise, and an unhandled traceback is a poor way
    # to say "you pointed me at the wrong thing". It is still a FAIL, never a
    # SKIP: the check did not run, and this file's whole sin was calling
    # "did not run" a success.
    try:
        failures, warnings, summary = run_checks()
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError,
            RuntimeError, json.JSONDecodeError) as exc:
        print(f"check-alerting: FAIL - {PROM} answered /-/ready but its API did not behave: "
              f"{exc.__class__.__name__}: {exc}", file=sys.stderr)
        print("  Is PROMETHEUS_URL really pointing at Prometheus?", file=sys.stderr)
        return EXIT_FAIL

    for line in summary:
        print(line)
    for warning in warnings:
        print(f"  WARN  {warning}")

    if failures:
        print("\ncheck-alerting: FAIL", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return EXIT_FAIL

    print("check-alerting: OK - targets up, Alertmanager reachable, "
          "every rule's metrics have current samples")
    return EXIT_OK


def run_checks() -> tuple[list[str], list[str], list[str]]:
    """The three checks. Returns (failures, warnings, summary lines).

    Output is collected rather than printed as it goes, so a mid-way API error
    cannot leave half a report on the terminal above a traceback.
    """
    failures: list[str] = []
    warnings: list[str] = []
    summary: list[str] = []

    # 1. every scrape target up
    targets = get_json("/api/v1/targets?state=active")["data"]["activeTargets"]
    for target in targets:
        job = target["labels"].get("job", "?")
        if target["health"] != "up":
            failures.append(
                f"scrape target '{job}' is {target['health']}: {target.get('lastError', '')[:120]}"
            )
    summary.append(
        f"  scrape targets: {sum(1 for t in targets if t['health'] == 'up')}/{len(targets)} up")

    # 2. Prometheus must actually know an Alertmanager
    alertmanagers = get_json("/api/v1/alertmanagers")["data"]
    active = [a["url"] for a in alertmanagers.get("activeAlertmanagers", [])]
    if not active:
        failures.append(
            "Prometheus has NO active Alertmanager - every alert would fire into nothing. "
            f"dropped={[a['url'] for a in alertmanagers.get('droppedAlertmanagers', [])]}")
        summary.append("  alertmanagers: 0 active")
    else:
        # Configured is not the same as alive. See alertmanager_is_alive().
        alive, detail = alertmanager_is_alive(ALERTMANAGER)
        if alive:
            summary.append(f"  alertmanagers: {len(active)} configured, {detail} answering")
        else:
            failures.append(
                f"Prometheus lists {len(active)} active Alertmanager ({active[0]}), but it is NOT "
                f"answering: {detail}. Under static_configs that listing reports the CONFIGURED "
                "target forever, alive or not, so this is the only check that notices. Every "
                "alert would fire into nothing. If Alertmanager is deliberately not published "
                "there, set ALERTMANAGER_URL.")
            summary.append(f"  alertmanagers: {len(active)} configured, NONE answering")

    # 3. every metric an alert rule references must resolve to CURRENT SAMPLES
    groups = get_json("/api/v1/rules")["data"]["groups"]
    n_rules = 0
    referenced: set[str] = set()
    per_rule: list[tuple[str, set[str]]] = []
    for group in groups:
        for rule in group["rules"]:
            if rule.get("type") != "alerting":
                continue
            n_rules += 1
            refs = metric_names_in(rule["query"])
            per_rule.append((rule["name"], refs))
            referenced |= refs

    live = metrics_with_current_samples(referenced)

    # "Ever seen" is fetched for WORDING ONLY. It must never decide - deciding
    # on it is bug 3, and a future reader tempted to reuse it here should see
    # this sentence first.
    try:
        ever_seen = set(get_json("/api/v1/label/__name__/values")["data"] or [])
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        ever_seen = set()

    for rule_name, refs in per_rule:
        for metric in sorted(refs - live):
            why = conditional_reason(metric)
            residue = " - seen earlier but NO current samples" if metric in ever_seen else \
                      " - never seen"
            line = f"{rule_name}: no current samples for '{metric}'{residue}"
            if why:
                warnings.append(f"{line} ({why})")
            else:
                failures.append(f"{line}; this rule can never fire, however green it looks")

    summary.append(f"  alerting rules: {n_rules} evaluated, "
                   f"{len(live)}/{len(referenced)} referenced metrics have current samples")

    return failures, warnings, summary


if __name__ == "__main__":
    sys.exit(main())
