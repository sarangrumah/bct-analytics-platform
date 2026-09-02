#!/usr/bin/env python3
"""Fail the build when a scanner suppression has no reason, no expiry, or a stale one.

    python3 security/check_ignore_policy.py            # check every policy file
    python3 security/check_ignore_policy.py --today 2027-06-01   # what will break, and when

Why this exists
---------------
Every scanner in this repository can be silenced by adding a line to a file. That is the
single control that, once loosened, quietly disables all the others - and it loosens by
accretion: one urgent suppression during an incident, never revisited, and two years later
nobody knows which of the forty entries still describes reality.

So the ignore *policy* is enforced by code rather than by review:

    .trivyignore     every active entry needs `reason:`, `expires: YYYY-MM-DD`, `owner:`
    .hadolint.yaml   every id under `ignored:` needs `reason:` and `expires: YYYY-MM-DD`
    .gitleaks.toml   every allowlist description needs `reason:` and `expires:`, where
                     `never` is permitted because a gitleaks allowlist describes a
                     correct-usage carve-out (SOPS ciphertext is *supposed* to be
                     high-entropy) rather than a deferred fix. A date there must still
                     be in the future.

An expiry in the past fails the build. That is the point: the entry does not disappear
silently, and it does not live forever. Somebody re-reads it and decides again.

Metadata is read from the contiguous comment block directly above the entry, so the
justification lives with the suppression and cannot drift away from it.

Zero dependencies: stdlib only, no PyYAML, no TOML parser, no jq. These files are read as
text on purpose - a structured parser discards the comments, and the comments are the part
being audited.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAX_HORIZON_DAYS = 400  # ~13 months: one year plus slack for a review that slips.

DATE_RE = re.compile(r"expires:\s*([0-9]{4}-[0-9]{2}-[0-9]{2}|never)", re.IGNORECASE)
REASON_RE = re.compile(r"reason:\s*(\S.*)", re.IGNORECASE)
OWNER_RE = re.compile(r"owner:\s*(\S.*)", re.IGNORECASE)


class Problem(str):
    pass


def preceding_comment_block(lines: list[str], index: int) -> str:
    """Gather the contiguous run of comment lines immediately above lines[index]."""
    collected = []
    cursor = index - 1
    while cursor >= 0:
        stripped = lines[cursor].strip()
        if stripped.startswith("#"):
            collected.append(stripped.lstrip("#").strip())
            cursor -= 1
        elif stripped == "":
            break
        else:
            break
    return "\n".join(reversed(collected))


def validate_metadata(text: str, *, today: dt.date, need_owner: bool, allow_never: bool) -> list[str]:
    faults = []
    reason = REASON_RE.search(text)
    if not reason:
        faults.append("no `reason:` line")
    elif len(reason.group(1).strip()) < 20:
        faults.append(f"reason is too short to be a reason: {reason.group(1).strip()!r}")
    elif reason.group(1).strip().lower().rstrip(".") in ("false positive", "not applicable", "n/a", "noise"):
        faults.append(f"`{reason.group(1).strip()}` is a verdict, not a reason - say why")

    expiry = DATE_RE.search(text)
    if not expiry:
        faults.append("no `expires:` line (use `expires: YYYY-MM-DD`)")
    else:
        raw = expiry.group(1).lower()
        if raw == "never":
            if not allow_never:
                faults.append("`expires: never` is not permitted here - a suppressed finding gets a date")
        else:
            try:
                when = dt.date.fromisoformat(raw)
            except ValueError:
                faults.append(f"unparseable expiry {raw!r}")
            else:
                if when < today:
                    faults.append(f"EXPIRED on {when.isoformat()} - re-justify it or delete it")
                elif (when - today).days > MAX_HORIZON_DAYS:
                    faults.append(
                        f"expiry {when.isoformat()} is more than {MAX_HORIZON_DAYS} days out; "
                        f"a suppression you cannot revisit within a year is a design problem for the Lead"
                    )

    if need_owner and not OWNER_RE.search(text):
        faults.append("no `owner:` line - name the agent accountable for closing it")
    return faults


def check_trivyignore(path: str, today: dt.date) -> list[Problem]:
    if not os.path.isfile(path):
        return []
    lines = open(path, encoding="utf-8").read().splitlines()
    problems = []
    active = 0
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        active += 1
        block = preceding_comment_block(lines, index)
        for fault in validate_metadata(block, today=today, need_owner=True, allow_never=False):
            problems.append(Problem(f".trivyignore:{index + 1}: entry {stripped!r}: {fault}"))
    print(f"  .trivyignore          {active} active suppression(s)")
    return problems


def check_hadolint(path: str, today: dt.date) -> list[Problem]:
    if not os.path.isfile(path):
        return []
    lines = open(path, encoding="utf-8").read().splitlines()
    problems = []
    in_ignored = False
    active = 0
    for index, line in enumerate(lines):
        stripped = line.strip()
        if re.match(r"^ignored:\s*$", line):
            in_ignored = True
            continue
        if in_ignored:
            if stripped.startswith("#") or stripped == "":
                continue
            if not stripped.startswith("- "):
                in_ignored = False
                continue
            active += 1
            rule_id = stripped[2:].strip()
            block = preceding_comment_block(lines, index)
            for fault in validate_metadata(block, today=today, need_owner=False, allow_never=False):
                problems.append(Problem(f".hadolint.yaml:{index + 1}: ignored rule {rule_id}: {fault}"))
    print(f"  .hadolint.yaml        {active} ignored rule(s)")
    return problems


def check_gitleaks(path: str, today: dt.date) -> list[Problem]:
    if not os.path.isfile(path):
        return []
    text = open(path, encoding="utf-8").read()
    problems = []
    # Each allowlist table, with its description, up to the next table header.
    blocks = re.split(r"^\s*\[\[(?:rules\.)?allowlists\]\]\s*$", text, flags=re.MULTILINE)[1:]
    for number, block in enumerate(blocks, start=1):
        block = re.split(r"^\s*\[", block, flags=re.MULTILINE)[0]
        description = re.search(r'description\s*=\s*(""".*?"""|".*?")', block, flags=re.DOTALL)
        if not description:
            problems.append(Problem(f".gitleaks.toml: allowlist #{number} has no description at all"))
            continue
        for fault in validate_metadata(description.group(1), today=today, need_owner=False, allow_never=True):
            problems.append(Problem(f".gitleaks.toml: allowlist #{number}: {fault}"))
    print(f"  .gitleaks.toml        {len(blocks)} allowlist block(s)")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--today", help="evaluate expiries as of this date (YYYY-MM-DD), for dry runs")
    args = parser.parse_args()

    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
    print(f"Ignore-policy audit as of {today.isoformat()}:")

    problems: list[Problem] = []
    problems += check_trivyignore(os.path.join(REPO_ROOT, ".trivyignore"), today)
    problems += check_hadolint(os.path.join(REPO_ROOT, ".hadolint.yaml"), today)
    problems += check_gitleaks(os.path.join(REPO_ROOT, ".gitleaks.toml"), today)

    if problems:
        print("", file=sys.stderr)
        for problem in problems:
            print(f"ERROR: {problem}", file=sys.stderr)
        print(f"\nIGNORE_POLICY_FAIL ({len(problems)} problem(s))", file=sys.stderr)
        return 1
    print("IGNORE_POLICY_OK - every suppression carries a reason and an unexpired date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
