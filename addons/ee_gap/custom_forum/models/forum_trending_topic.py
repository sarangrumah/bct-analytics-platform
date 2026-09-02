# -*- coding: utf-8 -*-
"""Trending forum topics aggregated per tag and period.

A cron rebuilds the top-N trending tags per period by summing post counts
and view counts over the last 7 days.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


_PERIOD_DAYS = {
    "day": 1,
    "week": 7,
    "month": 30,
}
_TOP_N = 10


class ForumTrendingTopic(models.Model):
    _name = "custom.forum.trending.topic"
    _description = "Custom Forum Trending Topic"
    _order = "compute_date desc, score desc"
    _rec_name = "display_name"

    display_name = fields.Char(compute="_compute_display_name", store=True)

    forum_id = fields.Many2one("forum.forum", string="Forum", required=True, ondelete="cascade", index=True)
    tag_id = fields.Many2one("forum.tag", string="Tag", required=True, ondelete="cascade", index=True)
    score = fields.Integer(string="Trend Score", default=0)
    period = fields.Selection(
        selection=[
            ("day", "Last Day"),
            ("week", "Last Week"),
            ("month", "Last Month"),
        ],
        default="week",
        required=True,
        index=True,
    )
    compute_date = fields.Datetime(string="Computed At", default=fields.Datetime.now, index=True)
    post_count = fields.Integer(string="Posts (window)", default=0)
    view_count = fields.Integer(string="Views (window)", default=0)
    rank = fields.Integer(string="Rank", default=0)

    _uniq_forum_tag_period = models.Constraint(
        "unique(forum_id, tag_id, period)",
        "Trending entry must be unique per (forum, tag, period).",
    )

    @api.depends("forum_id", "tag_id", "period", "score")
    def _compute_display_name(self):
        for rec in self:
            forum = rec.forum_id.name or "?"
            tag = rec.tag_id.name or "?"
            rec.display_name = f"[{rec.period}] {forum} / {tag} = {rec.score}"

    # ------------------------------------------------------------------
    # Core compute
    # ------------------------------------------------------------------

    @api.model
    def _compute_trending_for_period(self, period: str = "week") -> int:
        """Recompute the top-N trending tags for the given period.

        Returns the number of (forum, tag) rows written.
        """
        if period not in _PERIOD_DAYS:
            raise ValueError(f"Unknown trending period {period!r}")
        days = _PERIOD_DAYS[period]
        since = fields.Datetime.now() - timedelta(days=days)

        Post = self.env["forum.post"]
        # Active posts only — ignore closed/spam.
        domain = [
            ("create_date", ">=", since),
        ]
        if "state" in Post._fields:
            domain.append(("state", "=", "active"))
        posts = Post.search(domain)

        # Aggregate by (forum_id, tag_id).
        buckets: dict[tuple[int, int], dict[str, int]] = {}
        for post in posts:
            forum_id = post.forum_id.id if post.forum_id else False
            if not forum_id:
                continue
            tags = post.tag_ids if "tag_ids" in post._fields else post.env["forum.tag"]
            views = getattr(post, "views", 0) or 0
            for tag in tags:
                key = (forum_id, tag.id)
                bucket = buckets.setdefault(key, {"post_count": 0, "view_count": 0})
                bucket["post_count"] += 1
                bucket["view_count"] += views

        # Score = post_count * 2 + view_count.  Posts weigh more than passive views.
        scored = [
            (forum_id, tag_id, b["post_count"] * 2 + b["view_count"], b) for (forum_id, tag_id), b in buckets.items()
        ]
        # Per-forum top N.
        by_forum: dict[int, list] = {}
        for forum_id, tag_id, score, b in scored:
            by_forum.setdefault(forum_id, []).append((tag_id, score, b))
        for forum_id in by_forum:
            by_forum[forum_id].sort(key=lambda x: x[1], reverse=True)
            by_forum[forum_id] = by_forum[forum_id][:_TOP_N]

        # Drop stale entries for this period.
        self.search([("period", "=", period)]).unlink()

        now = fields.Datetime.now()
        written = 0
        for forum_id, items in by_forum.items():
            for rank_idx, (tag_id, score, b) in enumerate(items, start=1):
                self.create(
                    {
                        "forum_id": forum_id,
                        "tag_id": tag_id,
                        "period": period,
                        "score": score,
                        "post_count": b["post_count"],
                        "view_count": b["view_count"],
                        "rank": rank_idx,
                        "compute_date": now,
                    }
                )
                written += 1
        _logger.info("custom_forum trending: wrote %s rows for period=%s", written, period)
        return written

    @api.model
    def cron_compute_trending(self):
        """Cron entry-point — recompute the 'week' period by default."""
        total = 0
        for period in ("day", "week", "month"):
            try:
                total += self._compute_trending_for_period(period)
            except Exception as e:  # pragma: no cover - defensive
                _logger.exception("Trending compute failed for %s: %s", period, e)
        return total
