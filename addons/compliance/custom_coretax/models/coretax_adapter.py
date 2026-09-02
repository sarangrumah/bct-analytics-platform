# -*- coding: utf-8 -*-
"""Adapter abstractions for Coretax submission backends.

The DJP Coretax B2B REST API is not officially documented as of May 2026.
The default flow is manual XML upload via the portal. This module ships
an abstract base + manual no-op implementation so that future host-to-
host (ASPP) modules can plug in cleanly via inheritance without changing
the wizards.

Concrete adapters are dispatched by `custom.coretax.config.adapter_type`:
    - "manual"   -> custom.coretax.adapter.manual
    - "h2h_aspp" -> implemented by downstream module
"""

from __future__ import annotations

from odoo import _, api, models
from odoo.exceptions import UserError


class CoretaxAdapterBase(models.AbstractModel):
    _name = "custom.coretax.adapter.base"
    _description = "Coretax Submission Adapter (abstract)"

    @api.model
    def submit_xml(self, xml_bytes: bytes) -> dict:
        """Submit a Coretax XML payload.

        Returns a dict::

            {
                "submission_uuid": str | None,
                "status": "queued" | "submitted" | "manual_required",
                "message": str,
            }
        """
        raise NotImplementedError(_("submit_xml() must be implemented by the concrete adapter"))

    @api.model
    def query_nsfp(self, submission_uuid: str) -> str | None:
        """Return the assigned NSFP (17-char) for an approved submission, or None."""
        raise NotImplementedError(_("query_nsfp() must be implemented by the concrete adapter"))

    @api.model
    def download_response(self, submission_uuid: str) -> bytes:
        """Return the DJP response payload (XML/PDF bytes)."""
        raise NotImplementedError(_("download_response() must be implemented by the concrete adapter"))

    # ----- Dispatcher -----
    @api.model
    def _get_for_config(self, config):
        """Return the concrete adapter model bound to `config.adapter_type`."""
        mapping = {
            "manual": "custom.coretax.adapter.manual",
            "h2h_aspp": "custom.coretax.adapter.h2h_aspp",
        }
        model_name = mapping.get(config.adapter_type)
        if not model_name or model_name not in self.env:
            raise UserError(
                _(
                    "Coretax adapter '%s' is not installed. Install the corresponding "
                    "Custom adapter module or switch to manual."
                )
                % config.adapter_type
            )
        return self.env[model_name]


class CoretaxAdapterManual(models.AbstractModel):
    _name = "custom.coretax.adapter.manual"
    _inherit = "custom.coretax.adapter.base"
    _description = "Coretax Manual Adapter (portal upload)"

    @api.model
    def submit_xml(self, xml_bytes: bytes) -> dict:
        # Manual flow: the wizard produces the XML; the operator uploads it
        # via the official Coretax portal and pastes back the submission ref.
        return {
            "submission_uuid": None,
            "status": "manual_required",
            "message": _(
                "XML generated. Upload via Coretax portal and record the "
                "submission reference on the invoice once issued."
            ),
        }

    @api.model
    def query_nsfp(self, submission_uuid: str) -> str | None:
        # Manual flow: NSFP is entered by the operator after portal approval.
        return None

    @api.model
    def download_response(self, submission_uuid: str) -> bytes:
        raise UserError(
            _(
                "Manual adapter cannot fetch responses automatically. Download the "
                "approval PDF/XML from the Coretax portal and attach it to the invoice."
            )
        )
