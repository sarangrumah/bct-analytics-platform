# -*- coding: utf-8 -*-
{
    "name": "Currency NBSP / CSV BOM Fix",
    "version": "19.0.1.0.0",
    "summary": "Render money without non-breaking spaces and prepend a UTF-8 BOM "
    "to CSV exports, so amounts stop showing a stray 'Â'.",
    "description": """
Currency NBSP / CSV BOM Fix
===========================
Odoo separates the currency symbol from the amount with a NON-BREAKING SPACE
(U+00A0) and prefixes negative amounts with a ZERO WIDTH NO-BREAK SPACE
(U+FEFF). In UTF-8 those are the byte sequences ``C2 A0`` and ``EF BB BF``.
Any consumer that decodes the output as Latin-1 / cp1252 instead of UTF-8 —
Excel opening a CSV without a BOM, wkhtmltopdf on an HTML fragment that lost
its charset declaration, some mail clients — renders them as the stray
characters ``Â`` and ``ï»¿``::

    Rp 1.234.567   ->   RpÂ 1.234.567
    -1.234.567     ->   -ï»¿1.234.567

The amounts themselves are correct; only the separator is exotic. This module
replaces it with a plain space at every server-side formatting site, and makes
CSV exports self-describing so Excel stops guessing the encoding.

What it patches
---------------
1. ``odoo.tools.misc.format_amount`` and ``odoo.tools.misc.formatLang`` — the
   Python helpers behind emails, XLSX/CSV report builders and computed display
   strings. Wrapped, not rewritten: the originals still compute the value, we
   only normalise the whitespace of the result.
2. ``ir.qweb.field.monetary`` — the QWeb ``monetary`` widget, i.e. every PDF and
   HTML report.
3. ``/web/export/csv`` — prepends a UTF-8 BOM so spreadsheet apps detect the
   encoding instead of falling back to the system codepage.

Scoping
-------
(2) and (3) are ordinary Odoo overrides and therefore already apply only to
databases where this module is installed. (1) is a monkey patch and would
otherwise leak across every database served by the same worker process, so the
wrapper checks ``nbsp.free.currency`` in the calling environment's registry —
an abstract marker model that exists only where this module is installed. On
every other database the original string is returned untouched.
""",
    "author": "Platform",
    "website": "https://example.com/custom-platform",
    "category": "Technical/Localization",
    "depends": ["base", "web"],
    "data": [],
    "installable": True,
    "auto_install": False,
    "application": False,
    "license": "LGPL-3",
}
