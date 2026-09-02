# -*- coding: utf-8 -*-
"""Give the sessions created before the sequence existed a real name.

``data/ir_sequence_data.xml`` used to be an empty placeholder, so
``next_by_code`` returned ``None`` and ``create()`` fell back to the literal
"CC/NEW" — every session on those databases shares that one name. Renumber
them in creation order off the now-real sequence so the stock-take barcode
and the report grouping become meaningful.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("SELECT id FROM custom_cycle_count_session WHERE name = 'CC/NEW' ORDER BY id")
    ids = [row[0] for row in cr.fetchall()]
    if not ids:
        return
    from odoo import SUPERUSER_ID, api

    env = api.Environment(cr, SUPERUSER_ID, {})
    sequence = env["ir.sequence"]
    for session_id in ids:
        name = sequence.next_by_code("custom.cycle.count.session")
        if not name:
            _logger.warning("cycle count: sequence still missing, leaving session %s as CC/NEW", session_id)
            return
        cr.execute("UPDATE custom_cycle_count_session SET name = %s WHERE id = %s", (name, session_id))
    _logger.info("cycle count: renamed %s placeholder session(s)", len(ids))
