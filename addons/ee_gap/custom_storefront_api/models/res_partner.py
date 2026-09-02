# -*- coding: utf-8 -*-
"""Storefront customer helpers on res.partner.

Registration creates a *portal* ``res.users`` (so the customer can log into
Odoo's portal too, and so ``auth_jwt``'s email→partner strategy resolves a
unique partner). Authentication is delegated to Odoo's own credential check
so password hashing/policy stays in one place.
"""

from __future__ import annotations

from odoo import _, api, fields, models
from odoo.exceptions import AccessDenied, ValidationError


def _verify_password(user, password) -> bool:
    """Validate ``password`` for ``user`` across Odoo credential signatures."""
    u = user.with_user(user.id)
    credential = {"type": "password", "password": password}
    try:
        u._check_credentials(credential, {"interactive": False})
        return True
    except AccessDenied:
        return False
    except TypeError:
        # Fallback for older positional signature: _check_credentials(password, env)
        try:
            u._check_credentials(password, {"interactive": False})
            return True
        except AccessDenied:
            return False


class ResPartner(models.Model):
    _inherit = "res.partner"

    # UU PDP consent captured at storefront registration (spec F6).
    custom_consent_marketing = fields.Boolean(string="Marketing Consent")
    custom_consent_data = fields.Boolean(string="Data Processing Consent")
    custom_consent_date = fields.Datetime(string="Consent Given On")
    custom_is_guest = fields.Boolean(string="Guest Checkout")

    # ---- PII at rest (Fernet) ----------------------------------------------
    # The plaintext columns are replaced by non-stored computed fields backed by
    # encrypted `custom_*_enc` ciphertext columns. `email`/`name` stay plaintext
    # (load-bearing for login/JWT resolution). Trade-off: these fields are no
    # longer searchable/groupable in the back-office. Migrate existing rows once.
    custom_phone_enc = fields.Char(copy=False)
    custom_street_enc = fields.Char(copy=False)
    custom_street2_enc = fields.Char(copy=False)
    custom_zip_enc = fields.Char(copy=False)

    phone = fields.Char(compute="_compute_pii_enc", inverse="_inverse_pii_enc", store=False)
    street = fields.Char(compute="_compute_pii_enc", inverse="_inverse_pii_enc", store=False)
    street2 = fields.Char(compute="_compute_pii_enc", inverse="_inverse_pii_enc", store=False)
    zip = fields.Char(compute="_compute_pii_enc", inverse="_inverse_pii_enc", store=False)

    @api.depends("custom_phone_enc", "custom_street_enc", "custom_street2_enc", "custom_zip_enc")
    def _compute_pii_enc(self):
        crypto = self.env["custom.ir.config"]
        for p in self:
            p.phone = crypto.decrypt_value(p.custom_phone_enc)
            p.street = crypto.decrypt_value(p.custom_street_enc)
            p.street2 = crypto.decrypt_value(p.custom_street2_enc)
            p.zip = crypto.decrypt_value(p.custom_zip_enc)

    def _inverse_pii_enc(self):
        crypto = self.env["custom.ir.config"]
        for p in self:
            p.custom_phone_enc = crypto.encrypt_value(p.phone)
            p.custom_street_enc = crypto.encrypt_value(p.street)
            p.custom_street2_enc = crypto.encrypt_value(p.street2)
            p.custom_zip_enc = crypto.encrypt_value(p.zip)

    @api.model
    def _storefront_register(
        self,
        email,
        password,
        name,
        phone=None,
        consent_data=False,
        consent_marketing=False,
    ):
        """Create a portal user + partner. Returns the partner."""
        email = (email or "").strip().lower()
        if not email or not password or not name:
            raise ValidationError(_("Name, email and password are required."))
        if len(password) < 8:
            raise ValidationError(_("Password must be at least 8 characters."))
        # UU PDP: data-processing consent is mandatory; marketing is opt-in.
        if not consent_data:
            raise ValidationError(_("You must accept the data processing terms to register."))

        Users = self.env["res.users"].sudo()
        if Users.search_count([("login", "=", email)]):
            raise ValidationError(_("An account with this email already exists."))

        portal_group = self.env.ref("base.group_portal")
        user = Users.with_context(no_reset_password=True).create(
            {
                "name": name,
                "login": email,
                "email": email,
                "phone": phone or False,
                "password": password,
                # Odoo 19 renamed res.users.groups_id → group_ids.
                "group_ids": [(6, 0, [portal_group.id])],
            }
        )
        user.partner_id.write(
            {
                # customer_rank > 0 → the account shows up under Odoo Sales ▸ Customers.
                "customer_rank": 1,
                "custom_consent_data": bool(consent_data),
                "custom_consent_marketing": bool(consent_marketing),
                "custom_consent_date": fields.Datetime.now(),
            }
        )
        return user.partner_id

    @api.model
    def _storefront_authenticate(self, email, password):
        """Return the partner for valid credentials, else raise AccessDenied."""
        email = (email or "").strip().lower()
        user = self.env["res.users"].sudo().search([("login", "=", email)], limit=1)
        if not user or not _verify_password(user, password):
            raise AccessDenied(_("Invalid email or password."))
        return user.partner_id

    @api.model
    def _storefront_guest(self, email, name=None, consent_data=False):
        """Resolve (or create) a password-less guest partner for guest checkout.

        The JWT validator resolves the partner from the email claim and requires
        exactly one match, so the guest email must be unique and must NOT belong
        to a registered account (otherwise a guest could hijack that customer).
        """
        email = (email or "").strip().lower()
        if not email or "@" not in email:
            raise ValidationError(_("A valid email is required."))
        if not consent_data:
            raise ValidationError(_("You must accept the data processing terms to continue."))
        if self.env["res.users"].sudo().search_count([("login", "=", email)]):
            raise ValidationError(_("An account with this email already exists. Please sign in."))
        partners = self.sudo().search([("email", "=", email)])
        if len(partners) > 1:
            raise ValidationError(_("This email can't be used for guest checkout. Please sign in."))
        vals = {
            "custom_is_guest": True,
            "custom_consent_data": True,
            "custom_consent_date": fields.Datetime.now(),
        }
        if partners:
            partner = partners
            if name and not partner.name:
                vals["name"] = name
            partner.write(vals)
            return partner
        return self.sudo().create(
            {
                "name": name or email.split("@")[0],
                "email": email,
                "customer_rank": 1,
                **vals,
            }
        )
