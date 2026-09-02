# -*- coding: utf-8 -*-
"""Pre-init hook: tag NIK and NPWP fields on hr.employee with PDP classification
'sensitive_pii' via raw SQL on ir_model_fields (ORM blocks writes on base fields)."""

from __future__ import annotations

import logging

_logger = logging.getLogger(__name__)


def pre_init_hook(env):
    cr = env.cr
    # The hr.employee fields are added by this module via _inherit. They may not
    # exist yet at pre-install. After install they exist; the post-tagging here
    # is best-effort and idempotent. The classification record must exist
    # (custom_pdp_taxonomy dependency seeds them).
    cr.execute(
        """
        SELECT id FROM pdp_classification WHERE code = 'sensitive_pii' LIMIT 1
        """
    )
    row = cr.fetchone()
    if not row:
        _logger.warning(
            "custom_hr_payroll_id: pdp.classification 'sensitive_pii' not found; "
            "skipping field tagging. (Will need post-init re-tag.)"
        )
        return
    classif_id = row[0]
    for fname in ("x_custom_nik", "x_custom_npwp", "x_custom_kk_no"):
        cr.execute(
            """
            UPDATE ir_model_fields
               SET x_pdp_classification_id = %s
             WHERE model = 'hr.employee' AND name = %s
               AND (x_pdp_classification_id IS NULL OR x_pdp_classification_id = 0)
            """,
            (classif_id, fname),
        )
    _logger.info("custom_hr_payroll_id: tagged hr.employee NIK/NPWP fields as sensitive_pii")
