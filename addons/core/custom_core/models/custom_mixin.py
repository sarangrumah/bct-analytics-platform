# -*- coding: utf-8 -*-
"""Mixins shared across Custom platform modules."""

import logging

from odoo import api, models

_logger = logging.getLogger(__name__)


class CustomPlatformMixin(models.AbstractModel):
    """Marker mixin signaling that a model carries `x_custom_*` cross-module fields.

    Inheriting this mixin in a core-Odoo model (e.g. via _inherit) is a
    convention used by downstream modules to discover safe extension points.
    """

    _name = "custom.mixin.platform"
    _description = "Custom cross-module marker"

    @api.model
    def _custom_validate_field_prefix(self, field_name: str) -> bool:
        """Return True if the field follows the `x_custom_` convention."""
        return field_name.startswith("x_custom_")
