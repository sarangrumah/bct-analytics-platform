# -*- coding: utf-8 -*-
{
    "name": "Custom PPOB - Biller: Digiflazz",
    "summary": "Digiflazz H2H provider adapter (prepaid topup, postpaid inquiry "
    "and payment) with MD5 signing and ref_id idempotency.",
    "description": """
Custom PPOB Suite - Biller: Digiflazz
=====================================
First CONCRETE biller adapter for the PPOB vertical. Registers ``ppob_digiflazz``
in the PPOB adapter registry (``custom.ppob.provider.adapter_class``).

Built against the public Digiflazz technical documentation
(https://developer.digiflazz.com/api/). All endpoints are ``POST`` JSON to
``https://api.digiflazz.com/v1``:

    /transaction   prepaid topup, ``inq-pasca``, ``pay-pasca``, ``status-pasca``
    /cek-saldo     provider deposit balance          sign = md5(user + key + "depo")
    /price-list    catalogue                         sign = md5(user + key + "pricelist")

Transaction signing is ``sign = md5(username + apiKey + ref_id)``.

NOT YET VERIFIED AGAINST A LIVE SERVER
--------------------------------------
This adapter has never spoken to Digiflazz. It is written to the published spec
and covered by tests that mock the HTTP layer, which proves the request shape,
the signature derivation and the response mapping are what the spec describes --
and proves NOTHING about how the real endpoint behaves. Real providers return
undocumented rc codes, wrap errors differently under load, and disagree with
their own docs. Before go-live, run this against a Digiflazz sandbox and expect
to change the rc mapping in ``constants.py``.

Idempotency: ref_id, and why status() re-sends the sale
-------------------------------------------------------
Digiflazz deduplicates prepaid transactions by ``ref_id``. Their documented way
to check a prepaid transaction's status is to **re-send the identical topup
request with the same ref_id** -- the same call, not a separate read endpoint.
That looks alarming next to the suite's D1 rule ("never re-send pay(), it could
double-sell"), but the rule is intact: D1 forbids re-sending because a retry may
create a SECOND sale. Here the ref_id makes the call idempotent by contract, so
re-sending is a read. The danger is not re-sending -- it is re-sending with a
ref_id that Digiflazz no longer recognises, which is a NEW sale. Hence
``digiflazz_ref_id`` is stored, stable, and never regenerated for a transaction.

Two documented hazards are enforced as guards, not comments:

* **< 1 minute:** "Pemanggilan berulang dalam rentang waktu (kurang dari 1
  menit) dapat menimbulkan race condition atau duplikasi proses." status()
  refuses to fire until ``digiflazz_status_min_age_s`` (default 60) has elapsed
  since dispatch. Note the stale reaper's own floor is ``max(threshold, 1)``
  minute, so a provider left at ``stale_threshold_minutes = 1`` would otherwise
  sit exactly on this boundary.
* **> 90 days:** re-sending a ref_id older than Digiflazz's retention does not
  return a status -- it **books a brand-new transaction and charges the
  deposit**. status() refuses beyond ``digiflazz_status_max_age_days``
  (default 90) and leaves the transaction for manual ops. An unresolved
  transaction is an inconvenience; a silent duplicate sale is real money.

Scope
-----

* Prepaid topup + status: implemented.
* Postpaid ``inq-pasca`` / ``pay-pasca`` / ``status-pasca``: implemented.
  ``product.inquiry_required`` is the postpaid marker (the suite has no other),
  so a postpaid product MUST have it set or it will be sold as prepaid.
* ``topup()`` (funding our own Digiflazz deposit): NOT implemented -- Digiflazz
  deposit is a manual bank transfer plus a ticket, not an API purchase. Use the
  existing DP-100% topup wizard. ``cek-saldo`` is exposed instead as
  ``action_digiflazz_check_balance`` for reconciling the provider bucket.
* **Gaming: deliberately absent.** Digiflazz carries game top-ups, but the
  published docs do not state how ``customer_no`` encodes User ID + Zone/Server
  ID (concatenated? separated? per-game?). Rather than guess a format that would
  silently top up the WRONG ACCOUNT, gaming is left out pending D8/D9. When the
  format is confirmed, compose ``customer_no`` from the per-game dynamic fields
  that ``custom_ppob_pps_gateway`` already models.
""",
    "author": "Custom Platform Team",
    "website": "https://custom.local",
    "category": "Industry/PPOB",
    "version": "19.0.1.0.0",
    "license": "LGPL-3",
    "depends": [
        "custom_ppob_sale",
    ],
    "external_dependencies": {"python": ["requests"]},
    "data": [
        "views/generated_search_views.xml",
        "views/ppob_provider_views.xml",
        "views/ppob_transaction_views.xml",
    ],
    "application": False,
    "installable": True,
    "auto_install": False,
}
