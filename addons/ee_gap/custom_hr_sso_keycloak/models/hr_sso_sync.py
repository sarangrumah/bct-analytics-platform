# -*- coding: utf-8 -*-
"""Keycloak SSO -> hr.employee sync.

Ported from the legacy ``authenticate_keycloak`` controller (HR section), but:

- writes ``x_custom_nik`` (this platform's NIK field, 16-digit; from
  ``custom_hr_payroll_id``) instead of the non-existent ``nik`` field, and only
  when that field is installed;
- runs as an AbstractModel so it is unit-testable and can be ``with_delay()``-ed;
- is idempotent (only fills empty fields) and safe when ``hr`` is absent, the
  employee is not found, or the HC API is unreachable — every path degrades to a
  logged no-op so login is never affected.

Config (per tenant DB):
- ``hc.base_url`` — plain ``ir.config_parameter``.
- ``hc.api_key`` — encrypted via ``custom.ir.config`` (Fernet).
- ``custom_hr_sso_keycloak.claim_nik`` / ``claim_dept`` — Keycloak claim names
  (default ``nik`` / ``dept``).
"""

from __future__ import annotations

import logging
import re

import requests

from odoo import api, models

_logger = logging.getLogger(__name__)


def _scrub(exc, nik):
    """Exception text with the NIK masked.

    The HC API endpoint embeds the NIK in its path, so ``requests`` exceptions
    quote it back verbatim. NIK is regulated personal data (UU PDP) and must not
    reach the log in clear text.
    """
    text = str(exc)
    return text.replace(nik, "<nik>") if nik else text


NIK_RE = re.compile(r"\d{16}")
NIK_FIELD = "x_custom_nik"


class HrSsoSync(models.AbstractModel):
    _name = "hr.sso.sync"
    _description = "Keycloak SSO -> HR employee sync"

    # ------------------------------------------------------------------
    # entry point
    # ------------------------------------------------------------------
    @api.model
    def sync_for_login(self, login, validation):
        """Link/enrich the employee behind ``login`` from the SSO claims + HC API."""
        if "hr.employee" not in self.env:
            return
        validation = validation or {}
        user = self.env["res.users"].sudo().search([("login", "=", login)], limit=1)
        if not user:
            return

        nik_claim = (validation.get(self._claim("claim_nik", "nik")) or "").strip()
        dept_name = (validation.get(self._claim("claim_dept", "dept")) or "").strip()

        department = self._resolve_department(dept_name)
        employee = self._link_or_fill_employee(user, nik_claim, department)
        if not employee:
            return

        # Prefer the stored NIK; fall back to a valid claim so enrichment works
        # even when the NIK field isn't installed (payroll module absent).
        effective_nik = self._stored_nik(employee) or (nik_claim if self._valid_nik(nik_claim) else "")
        if effective_nik:
            self._sync_employee_if_needed(employee, effective_nik)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _claim(self, suffix, default):
        key = "custom_hr_sso_keycloak.%s" % suffix
        return self.env["ir.config_parameter"].sudo().get_param(key, default) or default

    @staticmethod
    def _valid_nik(nik):
        return bool(nik) and bool(NIK_RE.fullmatch(nik.strip()))

    def _has_nik_field(self):
        return NIK_FIELD in self.env["hr.employee"]._fields

    def _stored_nik(self, employee):
        return (self._has_nik_field() and employee[NIK_FIELD]) or ""

    def _resolve_department(self, dept_name):
        """Find-or-create an ``hr.department`` by (case-insensitive) name."""
        Dept = self.env["hr.department"].sudo()
        if not dept_name:
            return Dept.browse()
        dept = Dept.search([("name", "ilike", dept_name)], limit=1)
        return dept or Dept.create({"name": dept_name})

    def _link_or_fill_employee(self, user, nik_claim, department):
        """Link the employee to the user and fill empty NIK/department.

        Resolution order: the user's linked employee, else an existing employee
        matched by ``work_email``. If none is found, do nothing (no auto-create).
        """
        Employee = self.env["hr.employee"].sudo()
        employee = user.employee_id
        vals = {}
        if not employee:
            employee = Employee.search([("work_email", "=", user.login)], limit=1)
            if not employee:
                _logger.info("HR SSO: no employee for %s; skipping link", user.login)
                return Employee.browse()
            vals["user_id"] = user.id

        if self._has_nik_field() and not employee[NIK_FIELD] and nik_claim:
            if self._valid_nik(nik_claim):
                vals[NIK_FIELD] = nik_claim.strip()
            else:
                _logger.warning("HR SSO: NIK claim for %s is not 16 digits; skipping", user.login)
        if department and not employee.department_id:
            vals["department_id"] = department.id

        if vals:
            try:
                employee.write(vals)
            except Exception as exc:  # constraint/ACL hiccup must not break login
                _logger.error("HR SSO: failed to update employee %s: %s", employee.id, exc)
        return employee

    def _sync_employee_if_needed(self, employee, nik):
        if all([employee.department_id, employee.job_id, employee.parent_id]):
            return
        self._sync_employee_from_hc_api(employee, nik)

    def _sync_employee_from_hc_api(self, employee, nik):
        """Fill empty department / job / manager from the external HC API."""
        base_url = self.env["ir.config_parameter"].sudo().get_param("hc.base_url")
        api_key = self.env["custom.ir.config"].get_encrypted("hc.api_key")
        if not base_url or not api_key:
            _logger.warning("HR SSO: hc.base_url / hc.api_key not configured; skipping API sync")
            return

        endpoint = "%sapi/v1/open-api/employees/%s" % (base_url, nik)
        headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
        try:
            resp = requests.get(endpoint, headers=headers, timeout=10)
            resp.raise_for_status()
            payload = resp.json()
            if not payload.get("status"):
                # The HC API echoes the request (which carries the NIK and the
                # API key) back in its error message, so the remote text is not
                # safe to log. Identify the row instead; the API's own logs hold
                # the detail.
                _logger.error("HR SSO: HC API reported failure for employee %s", employee.id)
                return

            data = payload.get("data") or {}
            vals = {}

            dept_name = data.get("department")
            if dept_name and not employee.department_id:
                vals["department_id"] = self._resolve_department(dept_name).id

            job_title = data.get("job_title")
            if job_title and not employee.job_id:
                Job = self.env["hr.job"].sudo()
                job = Job.search([("name", "ilike", job_title)], limit=1) or Job.create({"name": job_title})
                vals["job_id"] = job.id

            superior = data.get("superior") or {}
            sup_email = superior.get("email")
            if sup_email and not employee.parent_id:
                sup = self.env["hr.employee"].sudo().search([("work_email", "=", sup_email)], limit=1)
                if sup and sup.id != employee.id:
                    vals["parent_id"] = sup.id
                else:
                    _logger.warning("HR SSO: superior not found (or self) for employee %s", employee.id)

            if vals:
                employee.write(vals)
                _logger.info("HR SSO: synced employee %s from HC API: %s", employee.id, vals)
        except requests.exceptions.RequestException as exc:
            _logger.error(
                "HR SSO: HC API request failed for employee %s: %s",
                employee.id,
                _scrub(exc, nik),
            )
        except Exception as exc:
            _logger.error(
                "HR SSO: unexpected error syncing employee %s: %s",
                employee.id,
                _scrub(exc, nik),
            )
