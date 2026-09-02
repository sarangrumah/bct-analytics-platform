# -*- coding: utf-8 -*-
from odoo import api, fields, models


PROVIDER_TRADEOFF_HTML = {
    "anthropic": """
        <div class="alert alert-info" role="alert">
            <strong>Anthropic Claude</strong> — kualitas reasoning tinggi,
            latensi 1–4 detik, biaya per-token.
            <ul class="mb-0">
                <li>Data prompt dikirim ke API Anthropic (US/EU region).</li>
                <li>Quota per-tenant aktif di gateway; pantau Grafana board "AI Spend".</li>
                <li>Rekomendasi untuk produksi multi-tenant &amp; fitur reasoning berat
                    (NLQ Chat, anomaly explain, doc classify).</li>
            </ul>
        </div>
    """,
    "openai": """
        <div class="alert alert-info" role="alert">
            <strong>OpenAI</strong> — fallback provider. Karakteristik mirip
            Anthropic: managed, per-token billing, data keluar ke API OpenAI.
            Gunakan hanya jika Anthropic tidak tersedia di region tenant.
        </div>
    """,
    "ollama": """
        <div class="alert alert-warning" role="alert">
            <strong>Local Ollama</strong> — model self-hosted (Llama / Mistral 3B–8B).
            <ul class="mb-0">
                <li><b>Kualitas</b> lebih rendah dari Claude — jawaban bisa pendek
                    atau kurang akurat untuk prompt panjang / multi-step.</li>
                <li><b>Latensi</b> 3–15 detik di VPS CPU-only (3–4× lebih lambat).
                    Fitur UI realtime (Ask AI) akan terasa lebih lambat.</li>
                <li><b>Biaya</b> flat — hanya RAM/CPU VPS, no per-token charge.</li>
                <li><b>Data residency</b> 100% on-prem, cocok untuk tenant dengan
                    klausul kerahasiaan ketat / air-gapped.</li>
                <li>Pastikan service <code>ollama</code> jalan &amp; model sudah di-pull.
                    Lihat <code>docs/ollama-local-deploy.md</code>.</li>
            </ul>
        </div>
    """,
    "": """
        <div class="alert alert-secondary" role="alert">
            Tenant ini mengikuti provider default dari gateway
            (env <code>AI_PROVIDER</code>). Override hanya jika tenant punya
            kebutuhan khusus (data residency, biaya, atau kualitas).
        </div>
    """,
}


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    custom_ai_enabled = fields.Boolean(
        string="Enable AI Intelligence",
        config_parameter="custom_ai.enabled",
        default=True,
    )
    custom_ai_default_quality = fields.Selection(
        [("fast", "Fast (default)"), ("high", "High quality (slower)")],
        string="Default Quality Tier",
        config_parameter="custom_ai.quality",
        default="fast",
    )
    custom_ai_provider_override = fields.Selection(
        [
            ("", "Use gateway default"),
            ("anthropic", "Anthropic"),
            ("openai", "OpenAI"),
            ("ollama", "Local Ollama"),
        ],
        string="Provider Override",
        config_parameter="custom_ai.provider_override",
    )
    custom_ai_ollama_model = fields.Char(
        string="Ollama Model",
        config_parameter="custom_ai.ollama_model",
        help="Nama model Ollama (mis. 'qwen2.5:7b', 'llama3.1:8b'). "
        "Pastikan sudah di-pull di container Ollama. "
        "Untuk workflow JSON-structured (anomaly, classify), gunakan model >= 7B.",
    )
    custom_ai_provider_tradeoff_html = fields.Html(
        string="Provider Tradeoff",
        compute="_compute_custom_ai_provider_tradeoff_html",
        sanitize=False,
    )

    @api.depends("custom_ai_provider_override")
    def _compute_custom_ai_provider_tradeoff_html(self):
        for rec in self:
            key = rec.custom_ai_provider_override or ""
            rec.custom_ai_provider_tradeoff_html = PROVIDER_TRADEOFF_HTML.get(key, PROVIDER_TRADEOFF_HTML[""])
