# -*- coding: utf-8 -*-
"""Bring existing databases in line with the 19.0.0.9.0 data files.

``data/feeds.xml`` and ``data/cron.xml`` are ``noupdate="1"``, so editing them only
affects fresh installs. Everything an existing tenant needs is applied here instead.

Deliberately NOT touched:

* ``retail.import.mailbox.active`` / ``purge_enabled`` / ``dry_run`` — mail fetching and
  message deletion stay off until an admin turns them on in exactly one database (the
  cron runs per-database, and two databases polling the same INBOX would race to delete
  each other's messages).
* ``cron_poll_retail_feeds.active`` — on a tenant with ``retail_import.x24_post_enabled``,
  enabling it starts posting POS orders and journal entries automatically. That is an
  operator decision, not something a schema upgrade should make.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

# Poll order once posting is enabled: X24 books the POS entries that X70D's tender
# reconciliation settles against; X31's discount reclass follows both.
_SEQUENCES = {
    "feed_retail_x24": 10,
    "feed_retail_x70d": 20,
    "feed_retail_x31": 30,
    "feed_retail_x20": 90,
    "feed_retail_x101": 90,
    "feed_retail_coa": 90,
    "feed_retail_x32p": 90,
    "feed_retail_x70": 90,
}

_MODULE = "custom_retail_import"


def _ref(env, xmlid):
    return env.ref("%s.%s" % (_MODULE, xmlid), raise_if_not_found=False)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})

    for xmlid, sequence in _SEQUENCES.items():
        feed = _ref(env, xmlid)
        if feed:
            feed.sequence = sequence

    # X31 discount reclass now runs daily alongside X24/X70D.
    x31 = _ref(env, "feed_retail_x31")
    if x31 and not x31.active:
        x31.active = True
        _logger.info("custom_retail_import: activated feed_retail_x31")

    # X20 must never fire on a daily drop: _load_x20 is a one-shot opening-balance
    # loader, and the emailed X20 is xlsx with a layout this CSV profile cannot read.
    x20 = _ref(env, "feed_retail_x20")
    if x20 and x20.active:
        x20.active = False
        _logger.info("custom_retail_import: deactivated feed_retail_x20 (one-shot loader)")

    # X70T's report has a title block: row 10 is the header, data starts at row 11.
    x70 = _ref(env, "profile_retail_x70")
    if x70 and x70.data_start_row == 10:
        x70.data_start_row = 11
        _logger.info("custom_retail_import: profile_retail_x70 data_start_row 10 -> 11")

    # Retime the feed cron to match the hourly mailbox fetch, but DO NOT enable it.
    # On a tenant with retail_import.x24_post_enabled set, switching this on starts
    # posting POS orders and journal entries automatically — an operator decision that
    # must follow the X70D-vs-pos.payment Rp0 reconciliation, not a schema upgrade.
    cron = _ref(env, "cron_poll_retail_feeds")
    if cron:
        cron.write({"interval_number": 1, "interval_type": "hours"})
        _logger.info(
            "custom_retail_import: cron_poll_retail_feeds retimed to hourly (active=%s, unchanged)",
            cron.active,
        )
