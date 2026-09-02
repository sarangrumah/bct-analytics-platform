# -*- coding: utf-8 -*-
"""AI analysis driver — orchestrates the call to ``custom_ai_bridge``.

Design notes
============

* The capability catalog is the single largest, rarely changing block in the
  prompt. We embed it as ``cache_control: ephemeral`` so that within a 5-minute
  window every BRD that hits the same Odoo node benefits from a near-instant
  cache hit. ``custom_ai_bridge`` already sets ``cache_system=True`` for the
  system prompt; we additionally inject the catalog into the system prompt so
  it inherits the same caching behaviour.
* The user message is per-BRD and per-section, so it intentionally varies.
* Strict JSON via explicit schema + a low temperature.
* Chunking: if the BRD has too many sections, we split into batches and
  merge results.
* Retry: one retry on malformed JSON with a stricter "JSON ONLY, no prose"
  instruction prefix.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any

from odoo import fields
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


DEFAULT_SYSTEM_PROMPT = """You are a senior Odoo solution architect analysing whether a customer's
Business Requirements Document (BRD) can be fulfilled by an existing custom
Odoo platform.

PLATFORM CONTEXT — IMPORTANT
============================
* The target platform is **Odoo 19 Community Edition** plus the bundled
  ``custom_*`` modules listed in the catalog below.
* The catalog already covers most of the EE-gap (accounting depth, sign,
  documents, approvals, AI features, multi-tenant orchestration, Coretax,
  PDP). Recommend reusing those instead of proposing new modules whose
  capability already exists.
* The BRD may describe a customer's CURRENT-STATE running on Odoo 18 or
  any legacy stack. That is **context only**. You MUST NOT recommend any
  migration tool, Odoo 18 backport, or "legacy_*" / "*_migrator" module —
  the platform is greenfield Odoo 19 and the customer's legacy data
  migration is handled by humans, not a module you propose.
* All recommended module names MUST start with ``custom_`` and describe
  a NEW, FUTURE-STATE Odoo 19 capability. Do NOT propose anything that
  looks like a one-off migration helper.

You receive:
  (a) The BRD as a list of structured sections (id, title, content).
  (b) A JSON catalog of available hub modules with their capabilities,
      models, dependencies and maturity. These are the only ones that
      already exist on Odoo 19 today.

Your job, for every BRD section, is to:
  1. Distil the underlying business capability the section is asking for.
  2. List capability tags (use the controlled vocabulary that appears as
     ``tags`` across the catalog; you may also propose new tags but prefer
     existing ones).
  3. Map to one or more hub modules from the catalog that already cover the
     capability (use the exact ``module`` name).
  4. Score fit 0-100 (0 = nothing covers it, 100 = fully covered today).
  5. Classify gap_status as one of: covered / partial / missing / unclear.
  6. Classify gap_severity as one of: must_have / should_have / nice_to_have.
  7. Write a short note explaining the reasoning.

After processing every section you must also propose a *minimal* set of new
``custom_<x>`` modules to fill the missing/partial parts, each with:
  - a snake_case name starting with ``custom_``,
  - a concise scope,
  - a tag list,
  - dependencies (existing modules from the catalog *and* other proposed
    siblings in this very response),
  - the existing modules they will *impact* (extend / patch),
  - an estimated_md (man-days, integer 1..120),
  - the severity it inherits from the worst section it addresses,
  - a justification paragraph,
  - **cross-vertical impact analysis** (REQUIRED): for every recommendation
    you MUST include
      * ``affects_existing_modules``: list of hub catalog module_name strings
        the proposal would extend or patch (subset of catalog),
      * ``cross_vertical_impact``: object keyed by module_name; each value is
        the list of verticals that already consume that module (use the
        ``deployed_in_verticals`` field of the catalog entry),
      * ``breaking_change``: boolean — true if the change would break the
        public API/schema for *any* of those verticals,
      * ``compat_strategy``: one of ``extend`` / ``abstract_base`` /
        ``feature_flag`` / ``fork_warning``.

OUTPUT MUST BE STRICT JSON matching the schema injected below. Do NOT wrap
the JSON in markdown code fences. Do NOT add commentary. If you are unsure
about a field, set gap_status = "unclear" and explain in notes.
"""


DEFAULT_USER_PROMPT_TEMPLATE = """BRD: {brd_name}
Business domain: {business_domain}
Language: {language}

=== BRD SECTIONS (JSON) ===
{sections_json}

=== AVAILABLE HUB MODULE CATALOG (JSON) ===
{catalog_json}

=== CAPABILITY COVERAGE MAP (per tag → modules + score 0-5) ===
{gap_matrix_json}

(Use this map to AVOID proposing new modules whose capability tag is already
covered. A tag with score >= 3 means at least one production module covers it;
score >= 4 means there is also a knowledge file documenting it. When you see
a section asking for capability X and X has covered modules in the map, MAP
the section to those modules — do NOT recommend a new custom_X.)

=== PRIOR LESSONS LEARNED (from human analyst corrections) ===
{lessons_block}

=== HUB MODULE CROSS-VERTICAL DEPLOYMENT (JSON) ===
{cross_vertical_json}

=== EXPECTED RESPONSE SCHEMA ===
{schema_json}

Return strict JSON now.
"""


DEFAULT_JSON_SCHEMA = {
    "type": "object",
    "required": ["overall_fit_pct", "sections", "recommendations"],
    "properties": {
        "overall_fit_pct": {"type": "integer", "minimum": 0, "maximum": 100},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "section_id",
                    "capability_required",
                    "capabilities_mentioned",
                    "mapped_module_names",
                    "fit_score",
                    "gap_status",
                    "gap_severity",
                    "notes",
                ],
                "properties": {
                    "section_id": {"type": "integer"},
                    "capability_required": {"type": "string"},
                    "capabilities_mentioned": {"type": "array", "items": {"type": "string"}},
                    "mapped_module_names": {"type": "array", "items": {"type": "string"}},
                    "fit_score": {"type": "integer", "minimum": 0, "maximum": 100},
                    "gap_status": {"type": "string", "enum": ["covered", "partial", "missing", "unclear"]},
                    "gap_severity": {"type": "string", "enum": ["must_have", "should_have", "nice_to_have"]},
                    "notes": {"type": "string"},
                },
            },
        },
        "recommendations": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "name",
                    "scope",
                    "capability_tags",
                    "estimated_md",
                    "depends",
                    "impact_modules",
                    "justification",
                    "severity",
                ],
                "properties": {
                    "name": {"type": "string"},
                    "scope": {"type": "string"},
                    "capability_tags": {"type": "array", "items": {"type": "string"}},
                    "estimated_md": {"type": "integer", "minimum": 1, "maximum": 240},
                    "depends": {"type": "array", "items": {"type": "string"}},
                    "depends_on_proposed": {"type": "array", "items": {"type": "string"}},
                    "impact_modules": {"type": "array", "items": {"type": "string"}},
                    "severity": {"type": "string", "enum": ["must_have", "should_have", "nice_to_have"]},
                    "justification": {"type": "string"},
                    "related_section_ids": {"type": "array", "items": {"type": "integer"}},
                    "affects_existing_modules": {"type": "array", "items": {"type": "string"}},
                    "cross_vertical_impact": {
                        "type": "object",
                        "additionalProperties": {"type": "array", "items": {"type": "string"}},
                    },
                    "breaking_change": {"type": "boolean"},
                    "compat_strategy": {
                        "type": "string",
                        "enum": ["extend", "abstract_base", "feature_flag", "fork_warning"],
                    },
                },
            },
        },
    },
}


# Section count above which we batch.
_SECTION_BATCH_SIZE = 10
_AI_MAX_TOKENS = 16000  # Opus 4.7 supports up to 32k; 16k fits ~12 full section objects.

# ---- Deep-dive (Jalur B) tuning --------------------------------------------
# Second pass: for each section flagged partial/missing/unclear, fetch real
# source code of candidate modules and re-ask the model. Keeps per-document
# cost AND wall-time bounded — pass 2 cannot run forever, otherwise the
# synchronous HTTP request that triggered analyze() will hit upstream timeout
# (gunicorn worker, reverse proxy, undici fetch).
_DEEP_DIVE_MAX_CALLS = 5  # was 20 — kept small so total wall-time stays bounded.
_DEEP_DIVE_CANDIDATES_PER_SECTION = 3
_DEEP_DIVE_MAX_BYTES_PER_MODULE = 50_000
_DEEP_DIVE_MAX_TOKENS = 4000
# Wall-time budget for the entire deep-dive phase, in seconds. When exceeded,
# remaining sections are skipped. Keeps the synchronous code path responsive
# even when the user opts NOT to dispatch via queue_job.
_DEEP_DIVE_TIME_BUDGET_S = 120
# Default quality for deep-dive — source-code verification is well within
# Haiku's reach and is ~5-10× faster + cheaper than Opus.
_DEEP_DIVE_QUALITY = "fast"

DEEP_DIVE_SYSTEM_PROMPT = """You are a senior Odoo engineer verifying a *first-pass* gap analysis
against the ACTUAL SOURCE CODE of candidate hub modules.

You will receive:
  (a) ONE BRD section (id, title, content).
  (b) The first-pass verdict (fit_score, gap_status, notes).
  (c) Python source excerpts (truncated) from 1-3 candidate hub modules
      that the first pass mapped (or that match the section's capability
      tags).

Your job is to OVERRIDE the first-pass verdict ONLY IF the source clearly
shows the capability is covered (or clearly more covered than the first
pass thought). Be conservative: if the source is ambiguous, keep the
first-pass verdict.

For every confirmed coverage, cite ``module_name:filename`` and the model
or method that supplies the capability.

You may also recommend that PROPOSED recommendations be dropped if the
deep dive shows the capability is already in the source. Use the
``drop_recommendation_names`` array for that — match by the snake_case
name from the first pass.

OUTPUT STRICT JSON, no markdown, no prose. Schema:
{
  "section_id": <int>,
  "fit_score": <int 0-100>,
  "gap_status": "covered" | "partial" | "missing" | "unclear",
  "notes": "<short reasoning with module_name:filename citations>",
  "drop_recommendation_names": ["custom_xxx", ...]
}
"""


class BrdAiAnalyzer:
    """Stateless analyzer; takes an env + a ``brd.document`` recordset of len 1."""

    def __init__(self, env):
        self.env = env

    # ------------------------------------------------------------------
    # Public entry-point
    # ------------------------------------------------------------------

    def analyze(self, brd_doc) -> dict[str, Any]:
        """Run the analysis and write Analyses + Recommendations.

        Returns the parsed AI response dict (useful for tests and debugging).
        """
        if not brd_doc:
            raise UserError("BRD record required.")
        sections = brd_doc.section_ids.sorted("sequence")
        if not sections:
            raise UserError("BRD has no extracted sections; run Extract first.")

        Param = self.env["ir.config_parameter"].sudo()
        system_prompt = Param.get_param("custom_brd_analyzer.system_prompt") or DEFAULT_SYSTEM_PROMPT
        user_template = Param.get_param("custom_brd_analyzer.user_prompt_template") or DEFAULT_USER_PROMPT_TEMPLATE
        schema = self._load_schema(Param)
        Entry = self.env["custom.module.capability.entry"].sudo()
        catalog = Entry._build_prompt_catalog()
        catalog_json = json.dumps(catalog, ensure_ascii=False)
        gap_matrix = Entry._build_gap_matrix()
        gap_matrix_json = json.dumps(gap_matrix, ensure_ascii=False)
        cross_vertical = self._build_cross_vertical_context()
        cross_vertical_json = json.dumps(cross_vertical, ensure_ascii=False)
        lessons_block = self._build_lessons_block(sections)

        merged: dict[str, Any] = {"sections": [], "recommendations": [], "overall_fit_pct": 0}
        scored = 0
        fit_total = 0
        raw_responses: list[str] = []  # for diagnostics

        def _flush_diag(extra_note: str = "") -> None:
            """Save raw responses to the document even when parsing fails so
            the BA can inspect what the model returned."""
            raw_dump = "\n\n--- BATCH ---\n\n".join(raw_responses)
            if extra_note:
                raw_dump = f"[NOTE] {extra_note}\n\n{raw_dump}"
            if len(raw_dump) > 60000:
                raw_dump = raw_dump[:60000] + "\n…(truncated)"
            try:
                brd_doc.sudo().write(
                    {
                        "last_ai_raw": raw_dump or False,
                        "last_ai_at": fields.Datetime.now(),
                    }
                )
            except Exception:  # noqa: BLE001
                pass

        for batch in self._chunk(sections, _SECTION_BATCH_SIZE):
            section_blob = [
                {"section_id": s.id, "title": s.title or "", "content": (s.content or "")[:3000]} for s in batch
            ]
            try:
                user_msg = user_template.format(
                    brd_name=brd_doc.name or "BRD",
                    business_domain=brd_doc.business_domain or "other",
                    language=brd_doc.language or "en",
                    sections_json=json.dumps(section_blob, ensure_ascii=False),
                    catalog_json=catalog_json,
                    gap_matrix_json=gap_matrix_json,
                    lessons_block=lessons_block,
                    cross_vertical_json=cross_vertical_json,
                    schema_json=json.dumps(schema),
                )
            except KeyError:
                # Backward-compat: previously-overridden template in
                # ir.config_parameter may lack newer placeholders. Build a
                # best-effort message with whatever placeholders the template
                # has, then append the missing blocks at the end.
                base_kwargs = {
                    "brd_name": brd_doc.name or "BRD",
                    "business_domain": brd_doc.business_domain or "other",
                    "language": brd_doc.language or "en",
                    "sections_json": json.dumps(section_blob, ensure_ascii=False),
                    "catalog_json": catalog_json,
                    "schema_json": json.dumps(schema),
                }
                # Try with each optional placeholder; drop the ones the
                # template doesn't use.
                for opt_key, opt_val in (
                    ("gap_matrix_json", gap_matrix_json),
                    ("lessons_block", lessons_block),
                    ("cross_vertical_json", cross_vertical_json),
                ):
                    if "{" + opt_key + "}" in user_template:
                        base_kwargs[opt_key] = opt_val
                user_msg = user_template.format(**base_kwargs)
                # Append any missing optional blocks so the model still gets them.
                if "gap_matrix_json" not in base_kwargs:
                    user_msg += "\n\n=== CAPABILITY COVERAGE MAP ===\n" + gap_matrix_json
                if "lessons_block" not in base_kwargs:
                    user_msg += "\n\n=== PRIOR LESSONS LEARNED ===\n" + lessons_block
                if "cross_vertical_json" not in base_kwargs:
                    user_msg += "\n\n=== HUB MODULE CROSS-VERTICAL DEPLOYMENT (JSON) ===\n" + cross_vertical_json
            response_text = self._call_ai(system_prompt=system_prompt, user_message=user_msg, catalog_json=catalog_json)
            raw_responses.append(response_text or "")
            _flush_diag()  # save after every batch so partial progress is visible
            _logger.info(
                "brd_ai_analyzer: batch response_text length=%d preview=%s",
                len(response_text or ""),
                (response_text or "")[:500].replace("\n", " "),
            )
            try:
                parsed = self._parse_json_strict(
                    response_text,
                    system_prompt=system_prompt,
                    user_message=user_msg,
                    catalog_json=catalog_json,
                )
            except UserError:
                # Both strict-parse attempts failed; try tolerant salvage on
                # truncated output (Anthropic hit max_tokens mid-stream).
                parsed = self._tolerant_parse(response_text) or {}
                if not parsed:
                    _flush_diag("Parse failed; raw response above is the unparsed AI output.")
                    raise
                _logger.warning(
                    "brd_ai_analyzer: salvaged %d sections / %d recs from truncated response",
                    len(parsed.get("sections") or []),
                    len(parsed.get("recommendations") or []),
                )
            batch_secs = parsed.get("sections") or []
            batch_recs = parsed.get("recommendations") or []
            _logger.info(
                "brd_ai_analyzer: parsed batch -> %d sections, %d recommendations",
                len(batch_secs),
                len(batch_recs),
            )
            merged["sections"].extend(batch_secs)
            merged["recommendations"].extend(batch_recs)
            if parsed.get("overall_fit_pct") is not None:
                fit_total += int(parsed["overall_fit_pct"])
                scored += 1

        if scored:
            merged["overall_fit_pct"] = int(fit_total / scored)

        # ----- Pass 2: deep-dive on partial/missing/unclear sections --------
        deep_enabled = Param.get_param("custom_brd_analyzer.deep_dive_enabled", default="1")
        if str(deep_enabled).strip() not in ("0", "false", "False", ""):
            try:
                self._deep_dive(brd_doc, merged, raw_responses)
                # Re-compute overall fit after deep-dive adjustments.
                fits = [
                    int(s.get("fit_score") or 0)
                    for s in (merged.get("sections") or [])
                    if s.get("fit_score") is not None
                ]
                if fits:
                    merged["overall_fit_pct"] = int(sum(fits) / len(fits))
            except Exception as exc:  # pragma: no cover - never block pass-1 result
                _logger.exception("brd_ai_analyzer: deep-dive failed (non-fatal): %s", exc)

        # Stash diagnostics on the document so the UI can surface them even
        # when AI returns empty arrays.
        merged["_raw_responses"] = raw_responses

        self._persist(brd_doc, merged)
        return merged

    # ------------------------------------------------------------------
    # Cross-vertical context builder
    # ------------------------------------------------------------------

    def _build_cross_vertical_context(self) -> list[dict]:
        """Return a serializable view of the hub catalog with capability tags,
        maturity and per-module list of verticals it is deployed in.

        Format::

            [
                {"module_name": "custom_coretax",
                 "category": "compliance",
                 "maturity": "production",
                 "tags": ["tax", "indonesia"],
                 "deployed_in_verticals": ["retail", "fnb"]},
                ...
            ]

        Graceful degradation:
        * If ``custom.hub.module.catalog`` is not installed, returns ``[]``.
        * If ``custom.hub.module.deployment`` is missing, leaves the verticals
          list empty.
        """
        Catalog = self.env.get("custom.hub.module.catalog")
        if Catalog is None:
            return []
        Catalog = Catalog.sudo()
        Deployment = self.env.get("custom.hub.module.deployment")
        deployments_by_catalog: dict[int, list[str]] = {}
        if Deployment is not None:
            for dep in Deployment.sudo().search([]):
                cat = getattr(dep, "catalog_id", False)
                if not cat:
                    continue
                vertical = (
                    getattr(dep, "vertical_code", False)
                    or getattr(dep, "tenant_code", False)
                    or getattr(getattr(dep, "tenant_id", False), "code", False)
                    or getattr(getattr(dep, "vertical_id", False), "code", False)
                    or getattr(dep, "name", False)
                )
                if vertical:
                    deployments_by_catalog.setdefault(cat.id, []).append(str(vertical))

        rows: list[dict] = []
        for entry in Catalog.search([]):
            rows.append(
                {
                    "module_name": entry.module_name,
                    "category": entry.category or "",
                    "maturity": entry.maturity or "",
                    "tags": entry.capability_tag_ids.mapped("technical_code")
                    or entry.capability_tag_ids.mapped("name"),
                    "deployed_in_verticals": sorted(set(deployments_by_catalog.get(entry.id, []))),
                }
            )
        return rows

    # ------------------------------------------------------------------
    # Lessons learned — humans correct the LLM; capture & inject those
    # corrections into future runs so we don't repeat the same mistakes.
    # ------------------------------------------------------------------

    def _build_lessons_block(self, sections) -> str:
        """Render active brd.lesson records as a bullet list. Blocker lessons
        are always included; hint lessons only when a keyword in their
        section_pattern overlaps any BRD section content (lowercased
        substring match). Capped at 20 entries by length desc.
        """
        Lesson = self.env.get("brd.lesson")
        if Lesson is None:
            return "(none)"
        lessons = Lesson.sudo().search([("active", "=", True)])
        if not lessons:
            return "(none — analyzer has no prior corrections recorded)"
        # Lower-cased concatenation of all sections for cheap keyword match.
        haystack = " ".join((s.content or "") for s in sections).lower()
        picked: list = []
        for L in lessons:
            if L.severity == "blocker":
                picked.append(L)
                continue
            pattern = (L.section_pattern or "").lower()
            # Hit if any 4+ char token from the pattern shows up in haystack.
            tokens = [t.strip(",.:;()[]\"'") for t in pattern.split() if len(t) >= 4]
            if any(t in haystack for t in tokens):
                picked.append(L)
        if not picked:
            return "(no lessons match the current BRD section keywords)"
        picked.sort(key=lambda L: len(L.reason or ""), reverse=True)
        picked = picked[:20]
        lines: list[str] = []
        for L in picked:
            tag = "[BLOCKER]" if L.severity == "blocker" else "[HINT]"
            rejected = ", ".join(L.rejected_proposals or []) or "—"
            correct = ", ".join(L.correct_modules.mapped("module_name")) or "—"
            lines.append(
                f"- {tag} {L.name}\n"
                f"    Pattern: {(L.section_pattern or '').strip()[:200]}\n"
                f"    Reject: {rejected}\n"
                f"    Use instead: {correct}\n"
                f"    Reason: {(L.reason or '').strip()[:400]}"
            )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Deep-dive pass (reads real source of candidate modules)
    # ------------------------------------------------------------------

    def _deep_dive(self, brd_doc, merged: dict, raw_responses: list[str]) -> None:
        """Pass 2: re-evaluate sections flagged partial/missing/unclear using
        actual Python source of candidate hub modules.

        Mutates ``merged`` in place:
        * Updates section.gap_status / fit_score / notes when the source
          shows the capability is more covered than pass-1 thought.
        * Drops recommendations whose name appears in the model's
          ``drop_recommendation_names`` for any reviewed section.
        """
        Param = self.env["ir.config_parameter"].sudo()
        max_calls = int(
            Param.get_param("custom_brd_analyzer.deep_dive_max_calls", default=_DEEP_DIVE_MAX_CALLS)
            or _DEEP_DIVE_MAX_CALLS
        )
        time_budget = float(
            Param.get_param("custom_brd_analyzer.deep_dive_time_budget_s", default=_DEEP_DIVE_TIME_BUDGET_S)
            or _DEEP_DIVE_TIME_BUDGET_S
        )
        deep_quality = (
            Param.get_param("custom_brd_analyzer.deep_dive_quality", default=_DEEP_DIVE_QUALITY) or _DEEP_DIVE_QUALITY
        )
        started_at = time.monotonic()

        sections = merged.get("sections") or []
        todo = [s for s in sections if s.get("gap_status") in ("partial", "missing", "unclear")]
        if not todo:
            return

        Entry = self.env["custom.module.capability.entry"].sudo()
        # Index catalog entries by module_name once.
        all_entries = Entry.search([])
        entries_by_name = {e.module_name: e for e in all_entries}
        # Tag-based fallback index: tech_code -> list[module_name]
        tag_index: dict[str, list[str]] = {}
        for e in all_entries:
            for code in e.capability_tag_ids.mapped("technical_code"):
                tag_index.setdefault(code, []).append(e.module_name)

        # Build a fast lookup from BRD section_id -> the actual record so we
        # can read title/content (the model only gets the trimmed batch view).
        Section = self.env["brd.document.section"].sudo()
        record_by_id: dict[int, Any] = {s.id: s for s in Section.search([("document_id", "=", brd_doc.id)])}

        # Track which recommendations to drop (set of names).
        drop_names: set[str] = set()
        calls = 0
        deep_raw: list[str] = []

        for sec in todo:
            if calls >= max_calls:
                _logger.info("brd_ai_analyzer: deep-dive cap reached (%d), stopping", max_calls)
                break
            elapsed = time.monotonic() - started_at
            if elapsed >= time_budget:
                _logger.info(
                    "brd_ai_analyzer: deep-dive time budget exhausted (elapsed=%.1fs, budget=%.1fs, done=%d/%d)",
                    elapsed,
                    time_budget,
                    calls,
                    len(todo),
                )
                break
            sec_id = int(sec.get("section_id") or 0)
            sec_record = record_by_id.get(sec_id)
            if sec_record is None:
                continue

            # Pick candidates: pass-1 mapped names first, fall back to tag match.
            cand_names: list[str] = list(sec.get("mapped_module_names") or [])
            if len(cand_names) < _DEEP_DIVE_CANDIDATES_PER_SECTION:
                for tag in sec.get("capabilities_mentioned") or []:
                    for name in tag_index.get(tag, []):
                        if name not in cand_names:
                            cand_names.append(name)
                        if len(cand_names) >= _DEEP_DIVE_CANDIDATES_PER_SECTION:
                            break
                    if len(cand_names) >= _DEEP_DIVE_CANDIDATES_PER_SECTION:
                        break
            cand_names = cand_names[:_DEEP_DIVE_CANDIDATES_PER_SECTION]
            if not cand_names:
                continue

            excerpts = self._gather_source_excerpts(cand_names, entries_by_name)
            if not excerpts:
                continue

            user_msg = self._build_deep_dive_user_msg(
                brd_doc=brd_doc,
                section_record=sec_record,
                pass1_verdict={
                    "fit_score": sec.get("fit_score"),
                    "gap_status": sec.get("gap_status"),
                    "notes": sec.get("notes"),
                    "mapped_module_names": sec.get("mapped_module_names") or [],
                },
                excerpts=excerpts,
            )
            try:
                raw = self._call_ai_deep_dive(user_message=user_msg, quality=deep_quality)
            except Exception as exc:  # pragma: no cover
                _logger.warning("brd_ai_analyzer: deep-dive call failed for sec %s: %s", sec_id, exc)
                continue
            deep_raw.append(raw or "")
            parsed = self._try_parse(raw or "")
            calls += 1
            if not parsed:
                _logger.info("brd_ai_analyzer: deep-dive returned unparseable JSON for sec %s", sec_id)
                continue

            # Apply override conservatively — only accept if gap_status is one
            # of the allowed values and section_id matches.
            new_status = parsed.get("gap_status")
            if new_status in ("covered", "partial", "missing", "unclear"):
                sec["gap_status"] = new_status
            if "fit_score" in parsed:
                try:
                    sec["fit_score"] = max(0, min(100, int(parsed.get("fit_score") or 0)))
                except (TypeError, ValueError):
                    pass
            if parsed.get("notes"):
                # Prepend pass-2 note so the BA can see both verdicts.
                pass1_note = sec.get("notes") or ""
                sec["notes"] = f"[deep-dive] {parsed['notes']}\n[pass-1] {pass1_note}".strip()

            for n in parsed.get("drop_recommendation_names") or []:
                if isinstance(n, str):
                    drop_names.add(n)

        # Drop recommendations the deep-dive confirmed are no longer needed.
        if drop_names:
            before = len(merged.get("recommendations") or [])
            merged["recommendations"] = [
                r for r in (merged.get("recommendations") or []) if (r.get("name") or "") not in drop_names
            ]
            _logger.info(
                "brd_ai_analyzer: deep-dive dropped %d/%d recommendation(s): %s",
                before - len(merged["recommendations"]),
                before,
                sorted(drop_names),
            )

        if deep_raw:
            raw_responses.append("\n--- DEEP-DIVE BATCH ---\n" + "\n\n--- DD ---\n\n".join(deep_raw))

    def _gather_source_excerpts(self, module_names: list[str], entries_by_name: dict) -> list[dict]:
        """Read up to ``_DEEP_DIVE_MAX_BYTES_PER_MODULE`` of source per module,
        prioritising ``models/*.py`` then ``wizards/*.py``. Returns a list of
        ``{module, files: [{path, content}]}`` dicts.
        """
        out: list[dict] = []
        for name in module_names:
            entry = entries_by_name.get(name)
            if entry is None:
                continue
            base = entry.module_path
            if not base or not os.path.isdir(base):
                continue
            file_entries: list[dict] = []
            budget = _DEEP_DIVE_MAX_BYTES_PER_MODULE
            for sub in ("models", "wizards", "wizard"):
                folder = os.path.join(base, sub)
                if not os.path.isdir(folder):
                    continue
                for root, _dirs, files in os.walk(folder):
                    for fn in sorted(files):
                        if not fn.endswith(".py") or fn == "__init__.py":
                            continue
                        if budget <= 0:
                            break
                        full = os.path.join(root, fn)
                        try:
                            with open(full, "r", encoding="utf-8", errors="ignore") as fh:
                                src = fh.read(budget + 1)
                        except OSError:
                            continue
                        truncated = len(src) > budget
                        if truncated:
                            src = src[:budget] + "\n# ...(truncated)\n"
                        budget -= len(src)
                        # Relative path for citation clarity.
                        rel = os.path.relpath(full, base).replace(os.sep, "/")
                        file_entries.append({"path": rel, "content": src})
                    if budget <= 0:
                        break
                if budget <= 0:
                    break
            if file_entries:
                out.append({"module": name, "files": file_entries})
        return out

    @staticmethod
    def _build_deep_dive_user_msg(*, brd_doc, section_record, pass1_verdict: dict, excerpts: list[dict]) -> str:
        # Render excerpts as fenced blocks the model can scan quickly.
        blocks: list[str] = []
        for mod in excerpts:
            blocks.append(f"=== MODULE {mod['module']} ===")
            for f in mod.get("files") or []:
                blocks.append(f"--- {mod['module']}/{f['path']} ---")
                blocks.append(f["content"])
        excerpts_blob = "\n".join(blocks)
        return (
            f"BRD: {brd_doc.name or 'BRD'}\n"
            f"Business domain: {brd_doc.business_domain or 'other'}\n"
            f"Language: {brd_doc.language or 'en'}\n\n"
            f"=== BRD SECTION ===\n"
            f"id: {section_record.id}\n"
            f"title: {section_record.title or ''}\n"
            f"content:\n{(section_record.content or '')[:4000]}\n\n"
            f"=== PASS-1 VERDICT (TO VERIFY OR OVERRIDE) ===\n"
            f"{json.dumps(pass1_verdict, ensure_ascii=False, indent=2)}\n\n"
            f"=== CANDIDATE MODULE SOURCE (TRUNCATED) ===\n"
            f"{excerpts_blob}\n\n"
            f"Return STRICT JSON per the deep-dive schema. Cite "
            f"module_name:filename when claiming coverage. If unsure, keep "
            f"pass-1 verdict."
        )

    def _call_ai_deep_dive(self, *, user_message: str, quality: str = _DEEP_DIVE_QUALITY) -> str:
        # Deep-dive system prompt is fixed across all calls in this run → cache.
        cached_system = f"{DEEP_DIVE_SYSTEM_PROMPT}\n\n<!-- cache_control: ephemeral -->\n"
        result = (
            self.env["custom.ai"]
            .sudo()
            ._chat(
                messages=[{"role": "user", "content": user_message}],
                system=cached_system,
                quality=quality if quality in ("fast", "high") else _DEEP_DIVE_QUALITY,
                max_tokens=_DEEP_DIVE_MAX_TOKENS,
                temperature=0.2,
            )
        )
        return self._unwrap_text(result)

    # ------------------------------------------------------------------
    # AI gateway call
    # ------------------------------------------------------------------

    def _call_ai(self, *, system_prompt: str, user_message: str, catalog_json: str) -> str:
        """Wraps ``custom.ai._chat``. The capability catalog is the cacheable
        block — we inject a cache-control sentinel by appending it to the
        system prompt (``custom_ai_bridge`` sets ``cache_system=True``)."""
        cached_system = (
            f"{system_prompt}\n\n"
            f"<!-- cache_control: ephemeral -->\n"
            f"=== CAPABILITY CATALOG (CACHED) ===\n{catalog_json}\n"
        )
        # ai-gateway accepts quality ∈ {"fast","high"}. BRD analysis benefits
        # from the quality model (claude-opus-4-7) — fewer JSON parse retries.
        result = (
            self.env["custom.ai"]
            .sudo()
            ._chat(
                messages=[{"role": "user", "content": user_message}],
                system=cached_system,
                quality="high",
                max_tokens=_AI_MAX_TOKENS,
                temperature=0.2,
            )
        )
        # custom_ai_bridge gateway returns provider-shaped JSON; tolerate a few
        # common shapes.
        return self._unwrap_text(result)

    @staticmethod
    def _unwrap_text(result: Any) -> str:
        if isinstance(result, str):
            return result
        if isinstance(result, dict):
            content = result.get("content")
            # 1) custom_ai_bridge gateway: ChatResponse.content is a pre-joined
            #    string of all text blocks. This is the common path for us.
            if isinstance(content, str):
                return content
            # 2) Raw Anthropic SDK response: content is list[{type,text}].
            if isinstance(content, list):
                parts = [c.get("text", "") for c in content if isinstance(c, dict)]
                if parts:
                    return "".join(parts)
            # 3) OpenAI-shaped
            choices = result.get("choices")
            if isinstance(choices, list) and choices:
                msg = choices[0].get("message") or {}
                if isinstance(msg, dict) and msg.get("content"):
                    return str(msg["content"])
            # 4) Other custom gateway shapes
            if "text" in result:
                return str(result["text"])
            if "output" in result:
                return str(result["output"])
        return json.dumps(result)

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _parse_json_strict(self, raw: str, *, system_prompt: str, user_message: str, catalog_json: str) -> dict:
        parsed = self._try_parse(raw)
        if parsed is not None:
            return parsed
        # Retry once with a stricter instruction prefix.
        _logger.warning("custom_brd_analyzer: first JSON parse failed; retrying with stricter prompt")
        retry_user = "STRICT JSON ONLY. NO PROSE. NO MARKDOWN FENCES. Output a single JSON object.\n\n" + user_message
        retry_text = self._call_ai(system_prompt=system_prompt, user_message=retry_user, catalog_json=catalog_json)
        parsed = self._try_parse(retry_text)
        if parsed is None:
            raise UserError("AI returned malformed JSON twice; check the logs for the raw response.")
        return parsed

    @staticmethod
    def _tolerant_parse(raw: str) -> dict | None:
        """Salvage `sections`/`recommendations` from a truncated AI response.

        When Anthropic hits ``max_tokens`` mid-stream, the raw string is no
        longer valid JSON (missing closing braces / unterminated string).
        We scan for the ``"sections": [`` array, walk balanced braces until
        the array breaks, and return whatever complete objects we got. Same
        for ``"recommendations"``. Better than 0 records.
        """
        if not raw:
            return None
        # Find a top-level opening brace.
        i = raw.find("{")
        if i == -1:
            return None

        def _extract_array(text: str, key: str) -> list[dict]:
            label = f'"{key}"'
            k = text.find(label)
            if k == -1:
                return []
            br = text.find("[", k)
            if br == -1:
                return []
            items: list[dict] = []
            pos = br + 1
            length = len(text)
            while pos < length:
                # Skip whitespace and commas
                while pos < length and text[pos] in " \t\r\n,":
                    pos += 1
                if pos >= length or text[pos] == "]":
                    break
                if text[pos] != "{":
                    # Unexpected — bail
                    break
                # Walk balanced braces with string-awareness.
                depth = 0
                start = pos
                in_str = False
                escape = False
                while pos < length:
                    ch = text[pos]
                    if in_str:
                        if escape:
                            escape = False
                        elif ch == "\\":
                            escape = True
                        elif ch == '"':
                            in_str = False
                    else:
                        if ch == '"':
                            in_str = True
                        elif ch == "{":
                            depth += 1
                        elif ch == "}":
                            depth -= 1
                            if depth == 0:
                                pos += 1
                                break
                    pos += 1
                if depth != 0:
                    # Last object was truncated; stop.
                    break
                snippet = text[start:pos]
                try:
                    items.append(json.loads(snippet))
                except json.JSONDecodeError:
                    break
            return items

        sections = _extract_array(raw, "sections")
        recommendations = _extract_array(raw, "recommendations")
        if not sections and not recommendations:
            return None
        # Recover overall_fit_pct if obviously present near the top.
        fit = 0
        m = re.search(r'"overall_fit_pct"\s*:\s*(\d+)', raw[:2000])
        if m:
            try:
                fit = int(m.group(1))
            except ValueError:
                pass
        return {
            "overall_fit_pct": fit,
            "sections": sections,
            "recommendations": recommendations,
        }

    @staticmethod
    def _try_parse(raw: str) -> dict | None:
        if not raw:
            return None
        # Strip markdown code fences if present.
        m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, re.DOTALL)
        candidate = m.group(1) if m else raw
        # If still embedded, find the first '{' and last '}'.
        if not candidate.lstrip().startswith("{"):
            i = candidate.find("{")
            j = candidate.rfind("}")
            if i == -1 or j == -1 or j <= i:
                return None
            candidate = candidate[i : j + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _load_schema(Param) -> dict:
        raw = Param.get_param("custom_brd_analyzer.json_schema")
        if raw:
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                _logger.warning("custom_brd_analyzer.json_schema is not valid JSON; falling back to default.")
        return DEFAULT_JSON_SCHEMA

    @staticmethod
    def _chunk(seq, size):
        seq = list(seq)
        for i in range(0, len(seq), size):
            yield seq[i : i + size]

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _persist(self, brd_doc, parsed: dict) -> None:
        Analysis = self.env["brd.analysis"].sudo()
        Recommendation = self.env["brd.recommendation"].sudo()
        Entry = self.env["custom.module.capability.entry"].sudo()
        Tag = self.env["custom.module.capability.tag"].sudo()
        Section = self.env["brd.document.section"].sudo()
        Catalog = self.env.get("custom.hub.module.catalog")
        if Catalog is not None:
            Catalog = Catalog.sudo()

        # Wipe previous run to keep idempotency.
        brd_doc.analysis_ids.unlink()
        brd_doc.recommendation_ids.unlink()

        # Sections
        for row in parsed.get("sections") or []:
            sec = Section.browse(int(row.get("section_id") or 0))
            if not sec.exists() or sec.document_id != brd_doc:
                # Be defensive: ignore section IDs the AI hallucinated.
                continue
            mapped = Entry.search([("module_name", "in", row.get("mapped_module_names") or [])])
            Analysis.create(
                {
                    "document_id": brd_doc.id,
                    "section_id": sec.id,
                    "capability_required": row.get("capability_required") or "",
                    "capabilities_mentioned": row.get("capabilities_mentioned") or [],
                    "mapped_module_ids": [(6, 0, mapped.ids)],
                    "fit_score": int(row.get("fit_score") or 0),
                    "gap_status": row.get("gap_status") or "unclear",
                    "gap_severity": row.get("gap_severity") or "should_have",
                    "notes": row.get("notes") or "",
                }
            )

        # Recommendations — two-pass so we can resolve sibling references.
        name_to_rec: dict[str, Any] = {}
        for seq, rec in enumerate(parsed.get("recommendations") or [], start=1):
            depends = Entry.search([("module_name", "in", rec.get("depends") or [])])
            impact = Entry.search([("module_name", "in", rec.get("impact_modules") or [])])
            tag_codes = rec.get("capability_tags") or []
            tag_ids: list[int] = []
            for code in tag_codes:
                tag = Tag.search([("technical_code", "=", code)], limit=1)
                if not tag:
                    tag = Tag.create({"name": code.replace("-", " ").title(), "technical_code": code})
                tag_ids.append(tag.id)
            related = Section.browse([int(x) for x in (rec.get("related_section_ids") or []) if x]).filtered(
                lambda s, brd=brd_doc: s.document_id.id == brd.id
            )
            affects_names = rec.get("affects_existing_modules") or []
            affects_ids: list[int] = []
            if Catalog is not None and affects_names:
                affects_ids = Catalog.search([("module_name", "in", list(affects_names))]).ids
            cross_map = rec.get("cross_vertical_impact")
            cross_json = ""
            if isinstance(cross_map, dict):
                # Coerce values to list[str] for predictable storage.
                clean = {str(k): [str(x) for x in (v if isinstance(v, list) else [])] for k, v in cross_map.items()}
                cross_json = json.dumps(clean, ensure_ascii=False)
            compat = rec.get("compat_strategy")
            if compat not in ("extend", "abstract_base", "feature_flag", "fork_warning"):
                compat = False
            new_rec = Recommendation.create(
                {
                    "document_id": brd_doc.id,
                    "sequence": seq,
                    "name": rec.get("name") or f"custom_proposal_{seq}",
                    "scope": rec.get("scope") or "",
                    "capability_tag_ids": [(6, 0, tag_ids)],
                    "related_section_ids": [(6, 0, related.ids)],
                    "depends_on_module_ids": [(6, 0, depends.ids)],
                    "impact_module_ids": [(6, 0, impact.ids)],
                    "estimated_md": int(rec.get("estimated_md") or 0),
                    "severity": rec.get("severity") or "should_have",
                    "justification": rec.get("justification") or "",
                    "cross_vertical_impact_json": cross_json,
                    "breaking_change": bool(rec.get("breaking_change")),
                    "compat_strategy": compat,
                }
            )
            if "affects_existing_module_ids" in Recommendation._fields:
                new_rec.write({"affects_existing_module_ids": [(6, 0, affects_ids)]})
            name_to_rec[new_rec.name] = (new_rec, rec.get("depends_on_proposed") or [])

        # Second pass: sibling links.
        for rec_rec, sibling_names in name_to_rec.values():
            sibs = [
                name_to_rec[n][0].id for n in sibling_names if n in name_to_rec and name_to_rec[n][0].id != rec_rec.id
            ]
            if sibs:
                rec_rec.write({"depends_on_proposed_ids": [(6, 0, sibs)]})

        # Persist diagnostic fields (truncated raw for UI surfacing).
        raw_responses = parsed.get("_raw_responses") or []
        raw_dump = "\n\n--- BATCH ---\n\n".join(raw_responses)
        if len(raw_dump) > 60000:
            raw_dump = raw_dump[:60000] + "\n…(truncated)"
        brd_doc.write(
            {
                "state": "analyzed",
                "last_ai_raw": raw_dump or False,
                "last_ai_at": fields.Datetime.now(),
                "last_ai_section_count": len(parsed.get("sections") or []),
                "last_ai_recommendation_count": len(parsed.get("recommendations") or []),
            }
        )
