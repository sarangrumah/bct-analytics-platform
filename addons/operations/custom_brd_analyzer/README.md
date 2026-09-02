# Custom BRD Analyzer

AI-driven BRD (Business Requirements Document) gap analyzer for the platform
module hub.

## Pipeline

1. Upload a PDF / DOCX / PPTX BRD.
2. `Extract` parses the document into structured sections (PyMuPDF /
   python-docx / python-pptx).
3. `Run AI Analysis` calls `custom_ai_bridge` with:
   - System prompt (cached, instruction-tuned)
   - Capability catalog of installed `custom_*` modules (cached, rarely
     changing)
   - Per-BRD sections (varies)
   It writes back `brd.analysis` rows and `brd.recommendation` rows.
4. `Request Review` triggers the `custom_approval_engine` if a matrix is
   defined for `brd.document`.
5. `Approve` finalises the BRD.
6. `Push to Backlog` on each recommendation creates a `project.task` under
   the "Hub Backlog - BRD" project.

## Graceful degradation

If `PyMuPDF`, `python-docx`, or `python-pptx` are not installed in the Odoo
runtime, the module still loads — only the affected extraction path raises a
user-friendly install hint.

## Prompt caching

The capability catalog block is appended to the system prompt. Since
`custom_ai_bridge` already sets `cache_system=True` on `/v1/chat`, this means
the (rarely changing) catalog is cache-eligible across BRDs within the
5-minute cache TTL.

## Tests

`pytest -k test_brd_analyzer` (six test methods, all using a mocked AI gateway).
