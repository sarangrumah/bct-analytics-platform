# -*- coding: utf-8 -*-
"""Render the DJP Coretax import workbooks.

Each ``_rows_*`` builder returns plain Python rows; ``_render`` turns them into
a workbook. Keeping the two apart means the column layouts can be unit-tested
without xlsxwriter in the loop.

Every literal in the ``*_COLUMNS`` tuples below is transcribed from the official
DJP template and must not be "corrected" — ``Nomor Setifikat Insentif`` is
misspelled in the template itself, and Coretax matches on the header text.
"""

from __future__ import annotations

import base64
import io
import logging

from odoo import _, fields, models
from odoo.addons.custom_tax_id.models.uom_inherit import CORETAX_UOM_FALLBACK
from odoo.exceptions import UserError
from odoo.tools import html2plaintext

_logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Column layouts (verbatim from the DJP template workbooks)
# --------------------------------------------------------------------------

BPPU_COLUMNS = (
    "NPWP Pemotong",
    "NITKU Pemotong (6 Digit Terakhir)",
    "Masa Pajak",
    "Tahun Pajak",
    "NPWP Penerima Penghasilan",
    "NITKU Penerima Penghasilan (22 Digit)",
    "Nama Penerima Penghasilan",
    "Email",
    "Jenis PPh",
    "Kode Objek Pajak",
    "Fasilitas Insentif",
    "Nomor Setifikat Insentif",
    "Tarif Fasilitas",
    "Penghasilan Bruto",
    "Jenis Dokumen Referensi",
    "Nomor Dokumen Referensi",
    "Tanggal Dokumen Referensi",
    "Metode Pembayaran bagi Pemotong Instansi Pemerintah",
    "Nomor SP2D",
    "NPWP Penandatangan",
    "Tanggal Pemotongan",
    "User Id",
    "Referensi",
)

BP21_COLUMNS = (
    "NPWP Pemotong",
    "NITKU Pemotong (6 Digit Terakhir)",
    "Masa Pajak",
    "Tahun Pajak",
    "NPWP Penerima Penghasilan",
    "NITKU Penerima Penghasilan (22 Digit)",
    "Nama Penerima Penghasilan",
    "Email",
    "Kode Objek Pajak",
    "Fasilitas Insentif",
    "Nomor Setifikat Insentif",
    "Tarif Fasilitas",
    "Gross Up (Y/N)",
    "Penghasilan Bruto Sebelumnya",
    "Penghasilan Bruto",
    "Norma Penghasilan Neto",
    "PTKP",
    "Jenis Dokumen Referensi",
    "Nomor Dokumen Referensi",
    "Tanggal Dokumen Referensi",
    "NPWP Penandatangan",
    "Tanggal Pemotongan",
    "User Id",
    "Referensi",
    "Referensi 3",
    "Referensi 4",
    "Referensi 5",
)

BPNR_COLUMNS = (
    "NPWP Pemotong",
    "NITKU Pemotong (6 Digit Terakhir)",
    "Masa Pajak",
    "Tahun Pajak",
    "TIN Penerima Penghasilan",
    "Nama Penerima Penghasilan",
    "Alamat Penerima Penghasilan",
    "Email",
    "Kode Negara",
    "Nomor Passport",
    "Nomor KITAP/KITAS",
    "Tempat Lahir",
    "Tanggal lahir",
    "Jenis PPh",
    "Kode Objek Pajak",
    "Fasilitas Insentif",
    "Nomor Setifikat Insentif",
    "Tarif Fasilitas",
    "Penghasilan Bruto",
    "Norma Penghasilan Neto",
    "Jenis Dokumen Referensi",
    "Nomor Dokumen Referensi",
    "Tanggal Dokumen Referensi",
    "Metode Pembayaran bagi Pemotong Instansi Pemerintah",
    "Nomor SP2D",
    "NPWP Penandatangan",
    "Tanggal Pemotongan",
    "User Id",
    "Referensi",
    "Referensi 3",
    "Referensi 4",
    "Referensi 5",
)

FK_COLUMNS = (
    "FK",
    "NPWP_WP",
    "ID_TKU_WP",
    "KD_JENIS_TRANSAKSI",
    "FG_PENGGANTI",
    "NOMOR_FAKTUR",
    "MASA_PAJAK",
    "TAHUN_PAJAK",
    "TANGGAL_FAKTUR",
    "NPWP",
    "JENIS_IDENTITAS",
    "NIK_NOMOR_PASSPORT",
    "KODE_NEGARA",
    "NAMA",
    "EMAIL_PEMBELI",
    "ALAMAT_PEMBELI",
    "TKU_PEMBELI",
    "JUMLAH_DPP",
    "JUMLAH_DPP_LAIN",
    "JUMLAH_PPN",
    "JUMLAH_PPNBM",
    "ID_KETERANGAN_TAMBAHAN",
    "FG_UANG_MUKA",
    "NOMOR_FAKTUR_UM_SEBELUMNYA",
    "UANG_MUKA_DPP",
    "UANG_MUKA_DPP_LAIN",
    "UANG_MUKA_PPN",
    "UANG_MUKA_PPNBM",
    "REFERENSI",
    "KODE_DOKUMEN_PENDUKUNG",
    "BRANCH/FIELD_TAMBAHAN_1",
    "FIELD_TAMBAHAN_2",
    "FIELD_TAMBAHAN_3",
    "FIELD_TAMBAHAN_4",
    "FIELD_TAMBAHAN_5",
)

OF_COLUMNS = (
    "OF",
    "BARANG_JASA",
    "KODE_OBJEK",
    "NAMA",
    "SATUAN",
    "HARGA_SATUAN",
    "JUMLAH_BARANG",
    "HARGA_TOTAL",
    "DISKON",
    "CHECK_DPP_LAIN",
    "DPP",
    "DPP_LAIN",
    "TARIF_PPN",
    "PPN",
    "TARIF_PPNBM",
    "PPNBM",
)

RETUR_COLUMNS = (
    "Baris",
    "Nomor Faktur",
    "NPWP Penjual",
    "Tanggal Retur",
    "DPP Retur",
    "DPP Lain Retur",
    "PPN Retur",
    "PPnBM Retur",
    "Total DPP Retur",
    "Total DPP Lain Retur",
    "Total PPN Retur",
    "Total PPnBM Retur",
)

RETUR_DETAIL_COLUMNS = (
    "Baris",
    "Jenis Barang Jasa",
    "Nama Barang Jasa",
    "Kode Barang Jasa",
    "Jumlah Barang Jasa",
    "Satuan Ukur Barang Jasa",
    "Harga Satuan",
    "Jumlah Barang Retur",
    "Diskon Retur",
    "DPP Retur",
    "Flag DPP Lain Retur",
    "DPP Lain Retur",
    "PPN Retur",
    "Tarif PPnBM",
    "PPNBM Retur",
)

# Tax List — not a DJP import file, so no template to transcribe. This is a
# reference dump of the company's configured taxes grouped by tax group ("tax
# categories"), with the Coretax-relevant DPP nilai-lain settings alongside the
# rate so the client can reconcile Odoo's tax master against Coretax.
TAXLIST_COLUMNS = (
    "Grup Pajak",
    "Nama Pajak",
    "Penggunaan",
    "Tipe",
    "Tarif (%)",
    "Metode DPP",
    "Faktor DPP",
    "Kategori DPP",
    "Deskripsi",
    "Aktif",
)

# Human-readable labels for the account.tax selection values.
TYPE_TAX_USE_LABEL = {
    "sale": "Penjualan",
    "purchase": "Pembelian",
    "none": "Tidak Digunakan",
}
AMOUNT_TYPE_LABEL = {
    "percent": "Persen",
    "fixed": "Nominal",
    "group": "Grup",
    "division": "Bagi",
}
DPP_METHOD_LABEL = {
    "regular": "Regular",
    "nilai_lain": "Nilai Lain",
}

# `pph_kind` on tax.withholding.category -> the Jenis PPh literal each template
# expects. The Unifikasi list sheet spells 4(2) as "PPH4-2"; the Non-Resident
# one uses mixed case and "PPh4a2". Do not unify these.
JENIS_PPH_UNIFIKASI = {
    "pph_15": "PPH15",
    "pph_22": "PPH22",
    "pph_23": "PPH23",
    "pph_4_2": "PPH4-2",
}
JENIS_PPH_NON_RESIDENT = {
    "pph_26": "PPh26",
    "pph_4_2": "PPh4a2",
}
UNIFIKASI_KINDS = tuple(JENIS_PPH_UNIFIKASI)
NON_RESIDENT_KINDS = tuple(JENIS_PPH_NON_RESIDENT)

# "OTHER : Lainnya" — the catch-all in the Jenis Dokumen Referensi master, and
# what the client's own samples use for invoice-backed bupot.
DOC_REF_OTHER = "07"


class CoretaxTemplateExportWizard(models.TransientModel):
    _name = "custom.coretax.template.export.wizard"
    _description = "Coretax Import File Export (DJP Templates)"

    template = fields.Selection(
        [
            ("bppu", "Bupot Unifikasi (PPh 23 / 4(2) / 22 / 15)"),
            ("bp21", "Bupot PPh 21"),
            ("bpnr", "Bupot Non-Resident (PPh 26)"),
            ("fk", "e-Faktur Keluaran (Import FK)"),
            ("retur", "Retur Masukan"),
            ("taxlist", "Tax List (Daftar Pajak)"),
        ],
        required=True,
        default="bppu",
        string="Template",
    )
    masa_pajak = fields.Selection(
        [(str(m).zfill(2), str(m).zfill(2)) for m in range(1, 13)],
        required=True,
        string="Masa Pajak",
        default=lambda self: str(fields.Date.context_today(self).month).zfill(2),
    )
    tahun_pajak = fields.Integer(
        required=True,
        string="Tahun Pajak",
        default=lambda self: fields.Date.context_today(self).year,
    )
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        string="Pemotong",
    )
    tanggal_pemotongan = fields.Date(
        string="Tanggal Pemotongan",
        help="Emitted in the 'Tanggal Pemotongan' column. Defaults to the last "
        "day of the masa pajak, which is how the client's samples are cut.",
    )

    file_data = fields.Binary(string="File", readonly=True, attachment=False)
    file_name = fields.Char(readonly=True)
    line_count = fields.Integer(readonly=True, string="Baris Terekspor")

    # ---------------------------------------------------------------- helpers

    def _period_bounds(self):
        """First and last date of the selected masa pajak."""
        self.ensure_one()
        start = fields.Date.to_date("%04d-%02d-01" % (self.tahun_pajak, int(self.masa_pajak)))
        if int(self.masa_pajak) == 12:
            end = fields.Date.to_date("%04d-12-31" % self.tahun_pajak)
        else:
            nxt = fields.Date.to_date("%04d-%02d-01" % (self.tahun_pajak, int(self.masa_pajak) + 1))
            end = fields.Date.subtract(nxt, days=1)
        return start, end

    def _cut_date(self):
        self.ensure_one()
        return self.tanggal_pemotongan or self._period_bounds()[1]

    @staticmethod
    def _fmt_date(value):
        """DJP asks for dd/mm/yyyy and matches it as text.

        Writing a real date cell would let Excel's locale decide the rendering,
        which is exactly how import files get silently rejected.
        """
        return value.strftime("%d/%m/%Y") if value else ""

    @staticmethod
    def _digits(value):
        return (value or "").replace(".", "").replace("-", "").replace(" ", "")

    # Layouts that carry an "NPWP Penandatangan" column, and therefore cannot be
    # emitted without one. FK/OF and Retur Masukan have no such column, so they
    # must not be blocked on it.
    _SIGNER_TEMPLATES = ("bppu", "bp21", "bpnr")

    def _pemotong(self):
        """(npwp, nitku_suffix, penandatangan, user_id), validated."""
        self.ensure_one()
        company = self.company_id
        npwp = company._check_coretax_pemotong(require_signer=self.template in self._SIGNER_TEMPLATES)
        return (
            npwp,
            company.x_custom_nitku_suffix or "",
            self._digits(company.x_custom_npwp_penandatangan),
            company.x_custom_coretax_user_id or "",
        )

    def _withholding_lines(self, kinds):
        """Posted withholding lines of the given pph kinds inside the masa pajak."""
        self.ensure_one()
        start, end = self._period_bounds()
        return self.env["account.move.withholding.line"].search(
            [
                ("company_id", "=", self.company_id.id),
                ("pph_kind", "in", list(kinds)),
                ("move_id.state", "=", "posted"),
                ("move_id.invoice_date", ">=", start),
                ("move_id.invoice_date", "<=", end),
            ],
            order="move_id, id",
        )

    # ------------------------------------------------------------- row builders

    def _rows_bppu(self):
        npwp_pemotong, nitku, signer, user_id = self._pemotong()
        cut = self._fmt_date(self._cut_date())
        rows = []
        for line in self._withholding_lines(UNIFIKASI_KINDS):
            move = line.move_id
            partner = move.partner_id.commercial_partner_id
            rows.append(
                [
                    npwp_pemotong,
                    nitku,
                    self.masa_pajak,
                    str(self.tahun_pajak),
                    self._digits(partner.x_custom_npwp),
                    partner._custom_coretax_nitku(),
                    partner.name or "",
                    partner.email or "",
                    JENIS_PPH_UNIFIKASI.get(line.pph_kind, ""),
                    line.category_id.bupot_object_code or "",
                    line.fasilitas_insentif or "9",
                    line.nomor_sertifikat_insentif or "",
                    line.tarif_fasilitas or 0,
                    line.base_amount,
                    DOC_REF_OTHER,
                    move.ref or move.name or "",
                    self._fmt_date(move.invoice_date),
                    "",  # Metode Pembayaran — non-government cutter, left blank
                    "",  # Nomor SP2D — idem
                    signer,
                    cut,
                    user_id,
                    "",  # Referensi — not mandatory
                ]
            )
        return [BPPU_COLUMNS], rows

    def _rows_bp21(self):
        npwp_pemotong, nitku, signer, user_id = self._pemotong()
        cut = self._fmt_date(self._cut_date())
        rows = []
        for line in self._withholding_lines(("pph_21",)):
            move = line.move_id
            partner = move.partner_id.commercial_partner_id
            rows.append(
                [
                    npwp_pemotong,
                    nitku,
                    self.masa_pajak,
                    str(self.tahun_pajak),
                    self._digits(partner.x_custom_npwp) or self._digits(partner.x_custom_nik),
                    partner._custom_coretax_nitku(),
                    partner.name or "",
                    partner.email or "",
                    line.category_id.bupot_object_code or "",
                    line.fasilitas_insentif or "9",
                    line.nomor_sertifikat_insentif or "",
                    line.tarif_fasilitas or 0,
                    "N",  # Gross Up — the engine books gross, never grossed-up
                    0,  # Penghasilan Bruto Sebelumnya
                    line.base_amount,
                    line.norma_penghasilan_neto or 0,
                    partner.x_custom_ptkp or "",
                    DOC_REF_OTHER,
                    move.ref or move.name or "",
                    self._fmt_date(move.invoice_date),
                    signer,
                    cut,
                    user_id,
                    "",
                    "",
                    "",
                    "",
                ]
            )
        return [BP21_COLUMNS], rows

    def _rows_bpnr(self):
        npwp_pemotong, nitku, signer, user_id = self._pemotong()
        cut = self._fmt_date(self._cut_date())
        rows = []
        for line in self._withholding_lines(NON_RESIDENT_KINDS):
            move = line.move_id
            partner = move.partner_id.commercial_partner_id
            rows.append(
                [
                    npwp_pemotong,
                    nitku,
                    self.masa_pajak,
                    str(self.tahun_pajak),
                    partner.x_custom_tin or "",
                    partner.name or "",
                    self._partner_address(partner),
                    partner.email or "",
                    partner.country_id.x_custom_code_alpha3 or "",
                    partner.x_custom_passport or "",
                    partner.x_custom_kitas or "",
                    partner.x_custom_birth_place or "",
                    self._fmt_date(partner.x_custom_birth_date),
                    JENIS_PPH_NON_RESIDENT.get(line.pph_kind, ""),
                    line.category_id.bupot_object_code or "",
                    line.fasilitas_insentif or "9",
                    line.nomor_sertifikat_insentif or "",
                    line.tarif_fasilitas or 0,
                    line.base_amount,
                    line.norma_penghasilan_neto or 0,
                    DOC_REF_OTHER,
                    move.ref or move.name or "",
                    self._fmt_date(move.invoice_date),
                    "",
                    "",
                    signer,
                    cut,
                    user_id,
                    "",
                    "",
                    "",
                    "",
                ]
            )
        return [BPNR_COLUMNS], rows

    # ------------------------------------------------- e-Faktur / Retur helpers

    def _vat_moves(self, move_types):
        self.ensure_one()
        start, end = self._period_bounds()
        return self.env["account.move"].search(
            [
                ("company_id", "=", self.company_id.id),
                ("move_type", "in", move_types),
                ("state", "=", "posted"),
                ("invoice_date", ">=", start),
                ("invoice_date", "<=", end),
            ],
            order="invoice_date, name",
        )

    @staticmethod
    def _line_vat(line):
        """(dpp, dpp_lain, ppn, tarif_ppn, uses_dpp_lain) for one invoice line.

        ``dpp`` is the contractual base; ``dpp_lain`` is the PMK 131/2024 "nilai
        lain" base the PPN is actually charged on. When no nilai-lain tax
        applies the two coincide and CHECK_DPP_LAIN is 'N'.
        """
        dpp = line.price_subtotal
        vat = line.tax_ids.filtered(lambda t: t.amount_type == "percent" and t.amount > 0)[:1]
        if not vat:
            return dpp, 0.0, 0.0, 0.0, False
        dpp_lain = vat._dpp_adjust(dpp)
        uses = vat.x_custom_dpp_method == "nilai_lain" and bool(vat.x_custom_dpp_factor)
        return dpp, dpp_lain, dpp_lain * vat.amount / 100.0, vat.amount, uses

    @staticmethod
    def _item_jenis(line):
        """ "Jenis Barang Jasa" for one OF item row: "Jasa" or "Barang".

        A down-payment line carries no product of its own — core builds it from
        a "fake" SO line — so reading ``line.product_id.type`` reports every
        down payment as "Barang", even one paid against a pure services order.
        Fall back to the products of the originating order, which is what the
        down payment is actually for.

        "Jasa" only when *every* product billed is a service: a mixed order has
        no single truthful answer, and "Barang" is the safer of the two.
        """
        products = line.product_id
        if not products and "sale_line_ids" in line._fields:
            products = line.sale_line_ids.order_id.order_line.filtered(
                lambda sol: sol.product_id and not sol.display_type and not sol.is_downpayment
            ).product_id
        if products and all(product.type == "service" for product in products):
            return "Jasa"
        return "Barang"

    def _rows_fk(self):
        """One FK row per invoice, each followed by its OF item rows."""
        npwp_wp, nitku, _signer, _user = self._pemotong()
        rows = []
        for move in self._vat_moves(("out_invoice",)):
            partner = move.partner_id.commercial_partner_id
            items = move.invoice_line_ids.filtered(lambda l: l.display_type == "product")
            if not items:
                continue
            totals = [0.0, 0.0, 0.0]
            of_rows = []
            for line in items:
                dpp, dpp_lain, ppn, tarif, uses = self._line_vat(line)
                totals[0] += dpp
                totals[1] += dpp_lain
                totals[2] += ppn
                of_rows.append(
                    [
                        "OF",
                        self._item_jenis(line),
                        # Kode objek barang/jasa: '000000' is the generic
                        # catch-all in CODE_OF_GOODS / CODE_OF_SERVICES, and
                        # what the client's own samples use throughout.
                        "000000",
                        line.product_id.name or line.name or "",
                        line.product_uom_id.x_custom_coretax_code or CORETAX_UOM_FALLBACK,
                        line.price_unit,
                        line.quantity,
                        line.price_unit * line.quantity,
                        0,  # Diskon — already netted into price_subtotal
                        "Y" if uses else "N",
                        dpp,
                        dpp_lain,
                        tarif,
                        ppn,
                        0,
                        0,
                    ]
                )
            rows.append(
                [
                    "FK",
                    npwp_wp,
                    nitku,
                    # KD_JENIS_TRANSAKSI 04 = "Other Tax Base" (DPP Nilai Lain),
                    # 01 = to another party. Chosen per invoice from whether any
                    # line actually rides a nilai-lain tax.
                    "04" if any(r[9] == "Y" for r in of_rows) else "01",
                    "0",  # FG_PENGGANTI — replacements go through the Faktur
                    # Pengganti wizard, which owns the 01/02 status code.
                    "",  # NOMOR_FAKTUR — assigned by Coretax on import
                    self.masa_pajak,
                    str(self.tahun_pajak),
                    self._fmt_date(move.invoice_date),
                    self._digits(partner.x_custom_npwp),
                    "",  # JENIS_IDENTITAS
                    "",  # NIK_NOMOR_PASSPORT
                    partner.country_id.x_custom_code_alpha3 or "",
                    partner.name or "",
                    partner.email or "",
                    self._partner_address(partner),
                    partner._custom_coretax_nitku()[-6:] if partner.x_custom_npwp else "",
                    totals[0],
                    totals[1],
                    totals[2],
                    0,  # JUMLAH_PPNBM
                    "",  # ID_KETERANGAN_TAMBAHAN
                    "0",  # FG_UANG_MUKA
                    "",
                    0,
                    0,
                    0,
                    0,
                    move.ref or move.name or "",
                    "",  # KODE_DOKUMEN_PENDUKUNG
                    "HO",  # BRANCH
                    "",
                    "",
                    "",
                    "",
                ]
            )
            rows.extend(of_rows)
        return [FK_COLUMNS, OF_COLUMNS], rows

    def _rows_retur(self):
        """Retur Masukan: banner, Retur table, END sentinel, DetailRetur table.

        Unlike the bupot templates this is not a flat sheet, so it returns a
        pre-laid-out block that ``_render`` writes verbatim.
        """
        # Retur carries the NPWP Pembeli banner only — no signer column.
        npwp_pembeli = self.company_id._check_coretax_pemotong(require_signer=False)
        block = [["Retur"], ["NPWP Pembeli", "", npwp_pembeli], [], list(RETUR_COLUMNS)]
        details = [[], ["DetailRetur"], list(RETUR_DETAIL_COLUMNS)]

        baris = 0
        for move in self._vat_moves(("in_refund",)):
            origin = move.reversed_entry_id
            faktur = origin.x_custom_nsfp if origin else ""
            if not faktur:
                # A retur must point at the faktur it reverses; without the
                # NSFP the row is unimportable, so skip rather than emit junk.
                _logger.info("Retur Masukan: skipping %s, no NSFP on origin", move.name)
                continue
            baris += 1
            items = move.invoice_line_ids.filtered(lambda l: l.display_type == "product")
            t_dpp = t_lain = t_ppn = 0.0
            for line in items:
                dpp, dpp_lain, ppn, _tarif, uses = self._line_vat(line)
                t_dpp += dpp
                t_lain += dpp_lain
                t_ppn += ppn
                details.append(
                    [
                        baris,
                        "A",  # Jenis Barang Jasa — 'A' = barang in the sample
                        line.product_id.name or line.name or "",
                        "000000",
                        line.quantity,
                        line.product_uom_id.x_custom_coretax_code or CORETAX_UOM_FALLBACK,
                        line.price_unit,
                        line.quantity,
                        0,
                        dpp,
                        "Yes" if uses else "No",
                        dpp_lain,
                        ppn,
                        0,
                        0,
                    ]
                )
            block.append(
                [
                    baris,
                    faktur,
                    self._digits(move.partner_id.commercial_partner_id.x_custom_npwp),
                    self._fmt_date(move.invoice_date),
                    t_dpp,
                    t_lain,
                    t_ppn,
                    0,
                    t_dpp,
                    t_lain,
                    t_ppn,
                    0,
                ]
            )
        if baris:
            block.append(["END"])
        return [], block + details if baris else []

    # -------------------------------------------------------------- Tax List

    def _rows_taxlist(self):
        """Every tax configured for the pemotong, grouped by tax group.

        Not period-bound: this is a master reference, so masa/tahun pajak are
        ignored. Ordering follows the tax group name, then sale before purchase,
        then descending rate, so PPN keluaran/masukan land next to each other.
        """
        self.ensure_one()
        taxes = (
            self.env["account.tax"].with_context(active_test=False).search([("company_id", "=", self.company_id.id)])
        )
        taxes = taxes.sorted(
            key=lambda t: (
                (t.tax_group_id.name or "").lower(),
                0 if t.type_tax_use == "sale" else 1,
                -t.amount,
                t.id,
            )
        )
        rows = []
        for tax in taxes:
            rows.append(
                [
                    tax.tax_group_id.name or "",
                    tax.name or "",
                    TYPE_TAX_USE_LABEL.get(tax.type_tax_use, tax.type_tax_use or ""),
                    AMOUNT_TYPE_LABEL.get(tax.amount_type, tax.amount_type or ""),
                    tax.amount,
                    DPP_METHOD_LABEL.get(tax.x_custom_dpp_method, tax.x_custom_dpp_method or ""),
                    tax.x_custom_dpp_factor or 0,
                    tax.x_custom_dpp_category or "",
                    html2plaintext(tax.description).strip() if tax.description else "",
                    "Y" if tax.active else "N",
                ]
            )
        return [TAXLIST_COLUMNS], rows

    @staticmethod
    def _partner_address(partner):
        parts = [
            partner.street,
            partner.street2,
            partner.city,
            partner.state_id.name,
            partner.zip,
        ]
        return ", ".join([p for p in parts if p])

    # ------------------------------------------------------------------ render

    def _render(self, header_rows, data_rows, sheet_name):
        """Header rows first (row 1..n), then data. No banner, no formatting."""
        import xlsxwriter

        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {"in_memory": True})
        sheet = workbook.add_worksheet(sheet_name[:31])
        # Everything is written as text or number exactly as built; a bold
        # header is cosmetic and Coretax ignores it.
        bold = workbook.add_format({"bold": True})

        row = 0
        for header in header_rows:
            for col, value in enumerate(header):
                sheet.write(row, col, value, bold)
            row += 1
        for data in data_rows:
            for col, value in enumerate(data):
                sheet.write(row, col, value)
            row += 1

        workbook.close()
        return output.getvalue()

    # ------------------------------------------------------------------ action

    _BUILDERS = {
        "bppu": ("_rows_bppu", "Template", "sample_pph_uni_bppu"),
        "bp21": ("_rows_bp21", "Template", "sample_pph_21_bp_21"),
        "bpnr": ("_rows_bpnr", "Template", "sample_pph_uni_bp_nr"),
        "fk": ("_rows_fk", "Import FK", "faktur_keluaran"),
        "retur": ("_rows_retur", "Retur", "retur_masukan"),
        "taxlist": ("_rows_taxlist", "Tax List", "tax_list"),
    }

    def action_export(self):
        self.ensure_one()
        builder = self._BUILDERS.get(self.template)
        if not builder:
            raise UserError(_("Template %s belum diimplementasikan.") % self.template)
        method, sheet_name, stem = builder
        header_rows, data_rows = getattr(self, method)()
        if not data_rows:
            raise UserError(
                _(
                    "Tidak ada data untuk masa pajak %(masa)s/%(tahun)s pada template ini.\n\n"
                    "Periksa: apakah ada bukti potong ter-posting di periode tersebut?",
                    masa=self.masa_pajak,
                    tahun=self.tahun_pajak,
                )
            )
        content = self._render(header_rows, data_rows, sheet_name)
        self.write(
            {
                "file_data": base64.b64encode(content),
                "file_name": "%s_%s_%s.xlsx" % (stem, self.masa_pajak, self.tahun_pajak),
                "line_count": len(data_rows),
            }
        )
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }
