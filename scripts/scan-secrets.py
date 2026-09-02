#!/usr/bin/env python3
"""Fail if a real secret has been committed.

`make scan-secret` runs this. It is intentionally small and dependency-free —
`jq` is not installed on this host and this must work on a clean clone before
anything is set up.

Scope note
----------
The Security agent owns `.gitleaks.toml`, `.pre-commit-config.yaml` and the CI
workflows; Platform-Infra does not create them. So this script *prefers*
`gitleaks` when it is on PATH and only falls back to its own rules otherwise.
It is a fast local guard, not a replacement for the Security agent's scanning.

What it checks
--------------
1. Every tracked file, against a small set of high-signal patterns (private
   keys, cloud credentials, bearer tokens, connection URIs with a password).
2. `.env.example` specifically: every secret-shaped key must be the literal
   string ``changeme``. This is the repository's stated invariant and it is the
   one most likely to be broken by someone "just filling in a placeholder".
3. That `.env` itself is not tracked.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

# Keys whose value in .env.example must be exactly `changeme`.
SECRET_KEY_RE = re.compile(
    r"(PASSWORD|PASSWD|SECRET|TOKEN|API_?KEY|PRIVATE_KEY|SALT|_KID)$",
    re.IGNORECASE,
)
ENV_LINE_RE = re.compile(r"^\s*(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=(?P<value>.*)$")
PLACEHOLDER = "changeme"

# High-signal only. A generic "32 hex chars" rule fires on every git SHA and
# every image digest in this repository, and a scanner that cries wolf is a
# scanner people disable.
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("private key block", re.compile(r"-----BEGIN (RSA|EC|OPENSSH|PGP|DSA)? ?PRIVATE KEY-----")),
    ("AWS access key id", re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("Stripe secret key", re.compile(r"\b[sr]k_live_[0-9A-Za-z]{16,}\b")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    # The negative character class matters as much as the positive one. A
    # documented DSN legitimately reads `postgresql://odoo:<password>@host/db`
    # or `postgresql://odoo:${POSTGRES_PASSWORD}@host/db`. Flagging those trains
    # people to add blanket ignores, which is how a scanner stops finding
    # anything real. So the captured secret may neither be a known placeholder
    # word nor contain a placeholder delimiter.
    ("password in connection URI",
     re.compile(
         r"\b(postgres(?:ql)?|redis|amqp|mongodb|mysql)://[^\s:/@]+:"
         r"(?!changeme\b)(?!password\b)(?!REDACTED\b)(?!secret\b)"
         r"[^\s:/@<>{}$%*]{6,}@"
     )),
    # The negative lookahead carries the exemptions, so each one is visible at the
    # point it applies rather than hidden in a path allowlist. `not-the-password`
    # and friends are values a test uses precisely BECAUSE they are wrong: a login
    # test that asserts the failure redirect has to send a password that fails.
    # Exempting the value keeps the rule pointed at real credentials; exempting the
    # file would stop scanning a file that could later hold one.
    ("hardcoded password assignment",
     re.compile(r"""(?i)\b(password|passwd|secret|api_key|token)\s*[:=]\s*["']"""
                r"""(?!changeme|CHANGEME|\$|\{\{|<)"""
                r"""(?![\w-]*(?:not-the-|wrong-|invalid-|dummy-|placeholder-|example-))"""
                r"""[^"'\s]{12,}["']""")),
]

# Paths where a match is expected and meaningless: this scanner's own rules,
# and documentation that quotes example tokens.
ALLOW_PATHS = (
    "scripts/scan-secrets.py",
)

BINARY_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".gz", ".zip", ".7z",
    ".woff", ".woff2", ".ttf", ".eot", ".dump", ".tar", ".bz2", ".xz",
}


def tracked_files(root: Path) -> list[str]:
    try:
        out = subprocess.check_output(
            ["git", "-C", str(root), "ls-files", "-z"], text=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("scan-secrets: not a git repository (or git missing)", file=sys.stderr)
        return []
    return [p for p in out.split("\0") if p]


def scan_content(root: Path, files: list[str]) -> list[str]:
    findings: list[str] = []
    for rel in files:
        if rel in ALLOW_PATHS:
            continue
        path = root / rel
        if path.suffix.lower() in BINARY_SUFFIXES or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="strict")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for label, pattern in PATTERNS:
                if pattern.search(line):
                    findings.append(f"{rel}:{lineno}: {label}")
    return findings


def scan_env_example(root: Path) -> list[str]:
    findings: list[str] = []
    example = root / ".env.example"
    if not example.exists():
        return ["missing .env.example"]
    for lineno, line in enumerate(example.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = ENV_LINE_RE.match(line)
        if not m:
            continue
        key, value = m.group("key"), m.group("value").strip()
        if SECRET_KEY_RE.search(key) and value != PLACEHOLDER:
            findings.append(
                f".env.example:{lineno}: {key} is {value!r}, must be exactly {PLACEHOLDER!r}"
            )
    return findings


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    files = tracked_files(root)

    problems: list[str] = []

    if ".env" in files:
        problems.append(".env is TRACKED by git. It must be gitignored and removed from the index.")

    problems += scan_env_example(root)

    used_gitleaks = False
    if shutil.which("gitleaks"):
        used_gitleaks = True
        print("scan-secrets: gitleaks found on PATH, running it as well")
        rc = subprocess.call(
            ["gitleaks", "detect", "--no-banner", "--redact", "--source", str(root)]
        )
        if rc != 0:
            problems.append(f"gitleaks reported findings (exit {rc})")

    problems += scan_content(root, files)

    if problems:
        print("\nscan-secrets: FAIL", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        print(
            "\nIf a finding is a false positive, narrow the pattern in "
            "scripts/scan-secrets.py — do not add a blanket ignore.",
            file=sys.stderr,
        )
        return 1

    suffix = " (gitleaks + built-in rules)" if used_gitleaks else " (built-in rules; gitleaks not installed)"
    print(f"scan-secrets: OK - {len(files)} tracked files clean{suffix}")
    print("  .env.example: every secret is the literal string `changeme`")
    return 0


if __name__ == "__main__":
    sys.exit(main())
