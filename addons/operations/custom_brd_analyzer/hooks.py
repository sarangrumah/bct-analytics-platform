# -*- coding: utf-8 -*-
"""Post-install hook: kick off a first capability catalog scan."""

from __future__ import annotations

import logging

_logger = logging.getLogger(__name__)


def _brd_post_init(env):
    """Run after the module is freshly installed.

    Triggers an initial scan so the catalog is populated for analysts before
    the monthly cron fires. Swallows errors — install must not fail because
    of a flaky filesystem walk.
    """
    try:
        env["custom.module.capability.entry"].sudo()._scan_all_modules()
    except Exception as exc:  # pragma: no cover - defensive
        _logger.warning("custom_brd_analyzer: initial capability scan failed: %s", exc)
