from collections import defaultdict

from odoo import fields, models


class CustomEsgReport(models.Model):
    _name = "custom.esg.report"
    _description = "ESG Sustainability Report"
    _order = "year desc, id desc"

    name = fields.Char(string="Name", required=True)
    year = fields.Integer(
        string="Year",
        required=True,
        default=lambda self: fields.Date.context_today(self).year,
    )
    framework = fields.Selection(
        [
            ("pojk51", "OJK POJK 51/2017"),
            ("gri", "GRI Standards"),
            ("sasb", "SASB"),
            ("tcfd", "TCFD"),
        ],
        string="Framework",
        default="pojk51",
    )
    measurement_ids = fields.Many2many(
        comodel_name="custom.esg.measurement",
        relation="custom_esg_report_measurement_rel",
        column1="report_id",
        column2="measurement_id",
        string="Measurements",
    )
    generated_html = fields.Html(string="Generated Report", readonly=True)
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("published", "Published"),
        ],
        string="Status",
        default="draft",
    )
    published_date = fields.Date(string="Published Date")

    def action_generate(self):
        """Aggregate measurements by metric.category and build a simple HTML table."""
        for report in self:
            buckets = defaultdict(list)
            for meas in report.measurement_ids:
                buckets[meas.metric_id.category or "other"].append(meas)

            category_labels = {
                "environmental": "Environmental",
                "social": "Social",
                "governance": "Governance",
                "other": "Other",
            }

            rows_html = []
            rows_html.append(
                "<h2>ESG Sustainability Report &mdash; {name} ({year})</h2>".format(
                    name=report.name or "", year=report.year or ""
                )
            )
            rows_html.append(
                "<p><strong>Framework:</strong> {fw}</p>".format(
                    fw=dict(self._fields["framework"].selection).get(report.framework, report.framework or "")
                )
            )

            for cat_key in ("environmental", "social", "governance", "other"):
                meas_list = buckets.get(cat_key)
                if not meas_list:
                    continue
                rows_html.append("<h3>{label}</h3>".format(label=category_labels[cat_key]))
                rows_html.append("<table class='table table-sm' border='1' cellpadding='4' cellspacing='0'>")
                rows_html.append(
                    "<thead><tr>"
                    "<th>Code</th><th>Metric</th><th>Unit</th>"
                    "<th>Period</th><th>Value</th><th>Status</th>"
                    "</tr></thead><tbody>"
                )
                for meas in meas_list:
                    rows_html.append(
                        "<tr>"
                        "<td>{code}</td>"
                        "<td>{name}</td>"
                        "<td>{unit}</td>"
                        "<td>{ps} &rarr; {pe}</td>"
                        "<td>{val}</td>"
                        "<td>{state}</td>"
                        "</tr>".format(
                            code=meas.metric_id.code or "",
                            name=meas.metric_id.name or "",
                            unit=meas.metric_id.unit or "",
                            ps=meas.period_start or "",
                            pe=meas.period_end or "",
                            val=meas.value,
                            state=meas.state,
                        )
                    )
                rows_html.append("</tbody></table>")

            if len(rows_html) <= 2:
                rows_html.append("<p><em>No measurements linked to this report.</em></p>")

            report.write(
                {
                    "generated_html": "".join(rows_html),
                    "state": "published",
                    "published_date": fields.Date.context_today(self),
                }
            )
        return True
