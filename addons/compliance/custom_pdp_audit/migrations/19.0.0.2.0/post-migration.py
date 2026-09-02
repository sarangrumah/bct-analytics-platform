# -*- coding: utf-8 -*-
"""Heal pdp.audit_log.action constraint on already-provisioned tenants.

Early DBs were created from 02-pdp-schema.sql when the ``action`` column was
VARCHAR(16) with an enumerated CHECK. Feature modules log domain verbs such as
``approval_submit`` / ``approval_advance`` that the enum rejected (and some are
longer than 16 chars), which aborted the surrounding business transaction
(e.g. the tenant Sales Order approval submit).

This migration widens the column and replaces the enum CHECK with the same
snake_case format check shipped in the current schema. The ``action`` column is
referenced by the read-only view ``pdp.audit_log_v``, so the view is captured,
dropped, and faithfully recreated around the ALTER. Idempotent and safe to
re-run; no-op when the pdp schema is absent.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("SELECT 1 FROM information_schema.tables WHERE table_schema = 'pdp' AND table_name = 'audit_log'")
    if not cr.fetchone():
        return

    # Capture every pdp view so we can recreate them verbatim after the ALTER
    # (the column type change is blocked while a view references the column).
    cr.execute(
        """
        SELECT c.relname, pg_get_viewdef(c.oid, true)
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'pdp' AND c.relkind = 'v'
        """
    )
    views = cr.fetchall()
    for name, _viewdef in views:
        cr.execute("DROP VIEW IF EXISTS pdp.%s CASCADE" % name)

    cr.execute("ALTER TABLE pdp.audit_log ALTER COLUMN action TYPE VARCHAR(64)")
    cr.execute("ALTER TABLE pdp.audit_log DROP CONSTRAINT IF EXISTS audit_log_action_check")
    cr.execute(
        "ALTER TABLE pdp.audit_log ADD CONSTRAINT audit_log_action_check CHECK (action ~ '^[a-z][a-z0-9_]{1,63}$')"
    )

    for name, viewdef in views:
        cr.execute("CREATE OR REPLACE VIEW pdp.%s AS %s" % (name, viewdef))

    _logger.info(
        "custom_pdp_audit: migrated audit_log.action to VARCHAR(64) format check (recreated %d view(s))",
        len(views),
    )
