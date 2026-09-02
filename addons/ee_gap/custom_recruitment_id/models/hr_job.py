# -*- coding: utf-8 -*-
import logging
import uuid

from odoo import _, fields, models

_logger = logging.getLogger(__name__)


class HrJob(models.Model):
    _inherit = "hr.job"

    x_publish_jobstreet = fields.Boolean(
        string="Publish to Jobstreet",
        default=False,
        tracking=True,
    )
    x_publish_glints = fields.Boolean(
        string="Publish to Glints",
        default=False,
        tracking=True,
    )
    x_external_post_id_jobstreet = fields.Char(
        string="Jobstreet Post ID",
        readonly=True,
        copy=False,
    )
    x_external_post_id_glints = fields.Char(
        string="Glints Post ID",
        readonly=True,
        copy=False,
    )

    def action_post_to_jobstreet(self):
        """Stub: pretend to publish the job to Jobstreet.

        In production this would call the Jobstreet Partners API; here we
        generate a mock external ID and persist it.
        """
        for job in self:
            if not job.x_publish_jobstreet:
                _logger.info(
                    "custom_recruitment_id: job %s not flagged for Jobstreet — skipped",
                    job.id,
                )
                continue
            ext = "JS-MOCK-%s" % uuid.uuid4().hex[:12].upper()
            job.write({"x_external_post_id_jobstreet": ext})
            job.message_post(
                body=_("Job posted to <b>Jobstreet</b> (stub). External post ID: <code>%s</code>") % ext,
            )
            _logger.info(
                "custom_recruitment_id: job %s posted to Jobstreet stub id=%s",
                job.id,
                ext,
            )
        return True

    def action_post_to_glints(self):
        """Stub: pretend to publish the job to Glints."""
        for job in self:
            if not job.x_publish_glints:
                _logger.info(
                    "custom_recruitment_id: job %s not flagged for Glints — skipped",
                    job.id,
                )
                continue
            ext = "GL-MOCK-%s" % uuid.uuid4().hex[:12].upper()
            job.write({"x_external_post_id_glints": ext})
            job.message_post(
                body=_("Job posted to <b>Glints</b> (stub). External post ID: <code>%s</code>") % ext,
            )
            _logger.info(
                "custom_recruitment_id: job %s posted to Glints stub id=%s",
                job.id,
                ext,
            )
        return True
