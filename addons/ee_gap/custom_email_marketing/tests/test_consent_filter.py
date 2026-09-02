# -*- coding: utf-8 -*-
"""Verify PDP consent filter actually narrows the recipient set at send."""

from odoo.tests.common import TransactionCase


class TestConsentFilter(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Purpose = cls.env["pdp.consent.purpose"]
        cls.purpose = Purpose.create(
            {
                "code": "email_newsletter_test",
                "name": "Newsletter Test Consent",
            }
        )
        Partner = cls.env["res.partner"]
        cls.partner_consenting = Partner.create(
            {
                "name": "Consenting Recipient",
                "email": "yes@example.id",
            }
        )
        cls.partner_no_consent = Partner.create(
            {
                "name": "No Consent Recipient",
                "email": "no@example.id",
            }
        )
        cls.env["pdp.consent"].create(
            {
                "partner_id": cls.partner_consenting.id,
                "purpose_id": cls.purpose.id,
            }
        )

        Mailing = cls.env["mailing.mailing"]
        cls.mailing = Mailing.create(
            {
                "name": "Test Newsletter",
                "subject": "Hi",
                "body_arch": "<p>Hello</p>",
                "body_html": "<p>Hello</p>",
                "mailing_model_id": cls.env.ref("base.model_res_partner").id,
                "mailing_domain": "[]",
                "x_consent_purpose_id": cls.purpose.id,
                "x_uu_pdp_footer": False,
            }
        )

    def test_filter_keeps_only_consenting_partner(self):
        candidate_ids = [self.partner_consenting.id, self.partner_no_consent.id]
        kept, filtered = self.mailing._filter_recipients_by_consent(candidate_ids)
        self.assertIn(self.partner_consenting.id, kept)
        self.assertNotIn(self.partner_no_consent.id, kept)
        self.assertEqual(filtered, 1)

    def test_filter_noop_when_no_purpose_set(self):
        self.mailing.x_consent_purpose_id = False
        candidate_ids = [self.partner_consenting.id, self.partner_no_consent.id]
        kept, filtered = self.mailing._filter_recipients_by_consent(candidate_ids)
        self.assertEqual(set(kept), set(candidate_ids))
        self.assertEqual(filtered, 0)

    def test_withdrawn_consent_is_filtered(self):
        # Withdraw the only consent, then we expect zero kept.
        self.env["pdp.consent"].search(
            [
                ("partner_id", "=", self.partner_consenting.id),
                ("purpose_id", "=", self.purpose.id),
            ]
        ).action_withdraw(reason="test")
        kept, filtered = self.mailing._filter_recipients_by_consent(
            [self.partner_consenting.id, self.partner_no_consent.id]
        )
        self.assertEqual(kept, [])
        self.assertEqual(filtered, 2)
