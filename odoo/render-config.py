#!/usr/bin/env python3
"""Render /opt/odoo/odoo.conf.template into $ODOO_RC using the environment.

Why this exists
---------------
`admin_passwd` is declared as a FileOnlyOption in odoo/tools/config.py: Odoo
offers no CLI flag and no environment variable for the master password, so the
only way to supply it without committing it is to render the config file at
container start.

Rules
-----
* Every ``${NAME}`` in the template must resolve, either from the environment or
  from DEFAULTS below. An unresolved placeholder is a hard error — emitting an
  empty value would silently produce, for example, ``admin_passwd =``.
* The rendered file is written 0600. It carries db_password and admin_passwd.
* Rendering is idempotent and runs on every container start, so a changed .env
  takes effect on ``docker compose up -d`` without a rebuild.

No third-party imports: this runs before Odoo does, in a minimal image.
"""
from __future__ import annotations

import os
import re
import stat
import sys

TEMPLATE = os.environ.get("ODOO_CONF_TEMPLATE", "/opt/odoo/odoo.conf.template")
TARGET = os.environ.get("ODOO_RC", "/opt/odoo/conf/odoo.conf")

# Defaults are the single source of truth for "what happens if .env is silent".
# They are mirrored in .env.example; keep the two in step.
DEFAULTS: dict[str, str] = {
    # database
    "ODOO_DB_HOST": "postgres",
    "ODOO_DB_PORT": "5432",
    "ODOO_DB_USER": "odoo",
    "ODOO_DB_PASSWORD": "",
    "ODOO_DB_MAXCONN": "32",
    "ODOO_DB_NAME": "bct",
    "ODOO_DBFILTER": "^bct$",
    # database manager
    "ODOO_LIST_DB": "False",
    "ODOO_ADMIN_PASSWD": "",
    # http
    "ODOO_HTTP_PORT": "8069",
    "ODOO_LONGPOLLING_PORT": "8072",
    "ODOO_PROXY_MODE": "True",
    # workers / limits
    "ODOO_WORKERS": "2",
    "ODOO_MAX_CRON_THREADS": "1",
    "ODOO_LIMIT_MEMORY_SOFT": "1073741824",   # 1024 MiB
    "ODOO_LIMIT_MEMORY_HARD": "1342177280",   # 1280 MiB
    "ODOO_LIMIT_REQUEST": "8192",
    "ODOO_LIMIT_TIME_CPU": "120",
    "ODOO_LIMIT_TIME_REAL": "240",
    "ODOO_LIMIT_TIME_REAL_CRON": "300",
    # logging
    "ODOO_LOG_LEVEL": "info",
    # behaviour
    "ODOO_WITHOUT_DEMO": "True",
    # email
    "ODOO_EMAIL_FROM": "noreply@localhost",
    "ODOO_SMTP_SERVER": "localhost",
    "ODOO_SMTP_PORT": "25",
}

# Values that must never be left at a placeholder in a running container.
# `changeme` is the literal placeholder used throughout .env.example; letting it
# reach a live master password would be worse than failing to boot.
MUST_NOT_BE_PLACEHOLDER = ("ODOO_ADMIN_PASSWD", "ODOO_DB_PASSWORD")

PLACEHOLDER_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def resolve(name: str, missing: list[str]) -> str:
    value = os.environ.get(name)
    if value is None or value == "":
        value = DEFAULTS.get(name)
    if value is None:
        missing.append(name)
        return ""
    return value


def main() -> int:
    try:
        with open(TEMPLATE, "r", encoding="utf-8") as fh:
            template = fh.read()
    except OSError as exc:
        print(f"render-config: cannot read template {TEMPLATE}: {exc}", file=sys.stderr)
        return 1

    missing: list[str] = []
    rendered = PLACEHOLDER_RE.sub(lambda m: resolve(m.group(1), missing), template)

    if missing:
        print(
            "render-config: no value and no default for: " + ", ".join(sorted(set(missing))),
            file=sys.stderr,
        )
        return 1

    weak = [k for k in MUST_NOT_BE_PLACEHOLDER if os.environ.get(k) == "changeme"]
    if weak:
        print(
            "render-config: refusing to start with placeholder secrets: "
            + ", ".join(weak)
            + "\n  Run `make dev-bootstrap` (scripts/gen-env-secrets.py) to generate a real .env.",
            file=sys.stderr,
        )
        return 1

    empty = [k for k in MUST_NOT_BE_PLACEHOLDER if not (os.environ.get(k) or DEFAULTS.get(k))]
    if empty:
        print(
            "render-config: these must be set and are empty: " + ", ".join(empty),
            file=sys.stderr,
        )
        return 1

    parent = os.path.dirname(TARGET) or "."
    os.makedirs(parent, exist_ok=True)

    # Write via a temp file in the same directory then rename, so a crash
    # mid-write can never leave Odoo reading half a config.
    tmp = TARGET + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(rendered)
    os.replace(tmp, TARGET)

    print(f"render-config: wrote {TARGET} (mode 0600) from {TEMPLATE}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
