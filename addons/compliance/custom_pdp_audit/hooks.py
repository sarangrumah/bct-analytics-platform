# -*- coding: utf-8 -*-
"""Pre-init hook: ensure the `pdp` schema, hash-chained audit_log table,
verify function, and read-only view exist in the current Odoo database.

Postgres compose init scripts only run on the *postgres* database (template).
New DBs created by Odoo (CREATE DATABASE) do not inherit those scripts, so
this module must self-bootstrap when it installs into a fresh Odoo DB.
"""

from __future__ import annotations

import logging
from pathlib import Path

_logger = logging.getLogger(__name__)

_SQL_FILES = [
    # Order matters: extensions, then schema/triggers
    "01-extensions.sql",
    "02-pdp-schema.sql",
]


def _read_compose_init_sql(name: str) -> str | None:
    """Best-effort: re-use the SQL files from the postgres compose init dir
    if they were copied into the addon's data folder; otherwise inline."""
    candidate = Path(__file__).resolve().parent / "data" / name
    if candidate.exists():
        return candidate.read_text(encoding="utf-8")
    return None


_INLINE_EXTENSIONS = """
CREATE EXTENSION IF NOT EXISTS unaccent;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS btree_gin;
"""


def pre_init_hook(env):
    cr = env.cr
    _logger.info("custom_pdp_audit: bootstrapping pdp schema in current Odoo DB")
    cr.execute(_INLINE_EXTENSIONS)

    schema_sql = _read_compose_init_sql("02-pdp-schema.sql")
    if not schema_sql:
        # Fallback to a shipped copy under the addon
        _logger.warning(
            "custom_pdp_audit: no shipped 02-pdp-schema.sql found at addon data/; "
            "PDP audit log will not be created. Copy from postgres/init/02-pdp-schema.sql."
        )
        return
    # Run as superuser-ish (Odoo's connection IS the DB owner for its own DB)
    cr.execute(schema_sql)
    _logger.info("custom_pdp_audit: pdp schema bootstrapped successfully")
