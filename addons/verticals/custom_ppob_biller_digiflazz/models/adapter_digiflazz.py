# -*- coding: utf-8 -*-
"""Digiflazz H2H adapter.

Registered as ``ppob_digiflazz``. Written against the public spec at
https://developer.digiflazz.com/api/ and NEVER run against a live Digiflazz
server -- see the module description.

It subclasses PPOBProviderAdapter directly rather than ppob_http_json, because
the two disagree on transport: ppob_http_json signs with HMAC-SHA256 over
timestamp+body and posts to a path per verb, while Digiflazz signs with MD5 over
username+apiKey+ref_id and multiplexes verbs onto /transaction via a ``commands``
key. Reusing it would have meant overriding every method that matters.

Like ppob_http_json, there is deliberately NO retry loop (D1) -- but for a
different reason. There, a re-sent pay() might double-sell. Here, Digiflazz
deduplicates by ref_id, so a re-send is safe BY CONTRACT and status() relies on
exactly that. The retry loop is still omitted because a blind retry cannot know
whether the ref_id it is re-sending is still within Digiflazz's retention
window; only status(), which checks the age guards, may re-send.
"""

import hashlib
import json
import logging
import time
from datetime import timedelta

import requests

from odoo import fields

from odoo.addons.custom_ppob_provider.models.ppob_provider_adapter_base import (
    AdapterResult,
    PPOBProviderAdapter,
    register_adapter,
)

from .constants import (
    CMD_INQUIRY_POSTPAID,
    CMD_PAY_POSTPAID,
    CMD_STATUS_POSTPAID,
    DEFAULT_TIMEOUT_S,
    PATH_CEK_SALDO,
    PATH_PRICE_LIST,
    PATH_TRANSACTION,
    SIGN_SUFFIX_DEPOSIT,
    SIGN_SUFFIX_PRICELIST,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_SUCCESS,
)

_logger = logging.getLogger(__name__)


class DigiflazzError(Exception):
    """Raised for local misconfiguration, never for a provider business error."""


@register_adapter("ppob_digiflazz")
class DigiflazzAdapter(PPOBProviderAdapter):
    # ------------------------------------------------------------------
    # Signing & transport
    # ------------------------------------------------------------------

    def _sign(self, suffix):
        """sign = md5(username + apiKey + suffix).

        ``suffix`` is the ref_id for transactions, or a fixed word for the
        read-only endpoints ("depo", "pricelist").

        MD5 is dictated by the Digiflazz API contract -- their gateway rejects
        any other digest -- so this is protocol interop, not a hash we chose.
        The API key never leaves the request signature, and the call rides TLS.
        """
        username = self.provider.digiflazz_username or ""
        api_key = self.provider._digiflazz_api_key()
        if not username or not api_key:
            raise DigiflazzError(
                "Digiflazz provider %s is missing a username or API key. Set "
                "digiflazz_username and point credential_ref at the "
                "ir.config_parameter holding the key." % self.provider.code
            )
        raw = f"{username}{api_key}{suffix}"
        # nosemgrep: python.lang.security.insecure-hash-algorithms-md5.insecure-hash-algorithm-md5,semgrep.weak-hash-md5-sha1
        # noqa: S324 alongside the existing nosec, because ruff does not read `# nosec`.
        # Digiflazz specifies md5(username + api_key + suffix) as its request
        # signature; a stronger digest here produces a signature their API rejects.
        # The weakness is in their protocol and cannot be fixed on this side.
        return hashlib.md5(raw.encode("utf-8")).hexdigest()  # noqa: S324  # nosec B324 - vendor-mandated digest

    def _timeout(self):
        cfg = self.provider.adapter_config_id
        return (cfg.timeout_s if cfg else 0) or DEFAULT_TIMEOUT_S

    def _log_call(self, path, body, status_code, latency_ms, error, ok):
        """Mirror ppob_http_json's observability: write custom.adapter.call.log
        when the provider has an adapter_config_id. Best-effort -- a failure to
        log must never break the sale."""
        cfg = self.provider.adapter_config_id
        if not cfg:
            return
        try:
            self.provider.env["custom.adapter.call.log"].sudo().create(
                {
                    "config_id": cfg.id,
                    "endpoint": path,
                    "request_hash": hashlib.sha256(body or b"").hexdigest() if body else "",
                    "response_status": status_code,
                    "latency_ms": latency_ms,
                    "error": (error or "")[:512] if error else False,
                    "ok": ok,
                }
            )
        except Exception as exc:  # pragma: no cover - never block the business call
            _logger.error("digiflazz adapter call log write failed: %s", exc)

    def _post(self, path, payload):
        """Single POST, no retry. Returns (status_code, parsed_data).

        Digiflazz wraps every response in a top-level ``data`` key; that wrapper
        is unwrapped here so callers never see it. A response that is not a JSON
        object (an HTML error page from a proxy, say) is surfaced as a transport
        error rather than being coerced into a fake business result.
        """
        url = self.provider._digiflazz_base_url() + path
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        t0 = time.time()
        try:
            resp = requests.post(url, data=body, headers=headers, timeout=self._timeout())
            latency_ms = int((time.time() - t0) * 1000)
        except requests.RequestException as exc:
            latency_ms = int((time.time() - t0) * 1000)
            _logger.warning("Digiflazz %s call failed: %s", self.provider.code, exc)
            self._log_call(path, body, 0, latency_ms, str(exc), False)
            return 0, {"error": str(exc)}

        try:
            parsed = resp.json() if resp.content else {}
        except ValueError:
            self._log_call(path, body, resp.status_code, latency_ms, "non-JSON response", False)
            return resp.status_code, {"error": "Digiflazz returned a non-JSON response"}

        ok = 200 <= resp.status_code < 300
        self._log_call(path, body, resp.status_code, latency_ms, None if ok else f"HTTP{resp.status_code}", ok)
        data = parsed.get("data") if isinstance(parsed, dict) else None
        if data is None:
            # No "data" wrapper: either an error envelope or something
            # unexpected. Pass the raw payload up rather than inventing a shape.
            return resp.status_code, parsed if isinstance(parsed, dict) else {"_raw": parsed}
        return resp.status_code, data

    # ------------------------------------------------------------------
    # Response mapping
    # ------------------------------------------------------------------

    def _result_from(self, status_code, data):
        """Map a Digiflazz /transaction payload onto AdapterResult.

        ok is TRI-STATE and that is the whole point of this method:
            Sukses  -> True   (settle, keep the wallet debit)
            Gagal   -> False  (refund)
            Pending -> None   (leave in_progress; the reaper will ask again)
        Collapsing Pending into False would refund a sale Digiflazz then
        fulfils: the mitra gets both the money and the pulsa.
        """
        data = data or {}
        if not 200 <= status_code < 300:
            return AdapterResult(
                ok=False,
                raw=data,
                error_code=f"HTTP{status_code}",
                error_message=data.get("error") or data.get("message") or "Digiflazz HTTP error",
            )
        status = (data.get("status") or "").strip()
        rc = data.get("rc")
        provider_ref = data.get("ref_id")
        sn = data.get("sn")
        price = data.get("price")

        if status == STATUS_SUCCESS:
            return AdapterResult(ok=True, provider_ref=provider_ref, serial_token=sn, amount=price, raw=data)
        if status == STATUS_PENDING:
            return AdapterResult(
                ok=None,
                provider_ref=provider_ref,
                serial_token=sn,
                amount=price,
                raw=data,
                error_code=rc,
                error_message=data.get("message"),
            )
        if status == STATUS_FAILED:
            return AdapterResult(
                ok=False,
                provider_ref=provider_ref,
                amount=price,
                raw=data,
                error_code=rc or "DIGIFLAZZ_FAIL",
                error_message=data.get("message") or "Digiflazz returned Gagal",
            )
        # An unrecognised status is NOT a failure -- refunding on a status we do
        # not understand could refund a successful sale. Treat it as pending and
        # make a human look. This is the branch most likely to fire in
        # production, since the adapter has never met the real server.
        _logger.warning(
            "Digiflazz %s returned unrecognised status %r (rc=%s) for ref %s; "
            "treating as pending for the reaper to resolve.",
            self.provider.code,
            status,
            rc,
            provider_ref,
        )
        return AdapterResult(
            ok=None,
            provider_ref=provider_ref,
            raw=data,
            error_code=rc or "DIGIFLAZZ_UNKNOWN_STATUS",
            error_message=data.get("message") or f"Unknown status: {status!r}",
        )

    # ------------------------------------------------------------------
    # Request builders
    # ------------------------------------------------------------------

    def _is_postpaid(self, transaction):
        """``product.inquiry_required`` is the suite's only postpaid marker.

        A postpaid product left without it is sold down the prepaid path, which
        Digiflazz rejects rather than mis-sells -- noisy, but not dangerous.
        """
        return bool(transaction.product_id.inquiry_required)

    def _ref_id(self, transaction):
        """Return the transaction's stable ref_id, assigning it on first use.

        Never regenerate: the ref_id IS the idempotency contract with Digiflazz.
        """
        ref = transaction.digiflazz_ref_id
        if not ref:
            ref = transaction._digiflazz_build_ref_id()
            transaction.digiflazz_ref_id = ref
        return ref

    def _transaction_payload(self, transaction, commands=None):
        ref_id = self._ref_id(transaction)
        payload = {
            "username": self.provider.digiflazz_username or "",
            "buyer_sku_code": transaction.provider_sku or "",
            "customer_no": transaction.msisdn or "",
            "ref_id": ref_id,
            "sign": self._sign(ref_id),
        }
        if commands:
            payload["commands"] = commands
        if self.provider.digiflazz_testing:
            payload["testing"] = True
        return payload

    # ------------------------------------------------------------------
    # PPOBProviderAdapter protocol
    # ------------------------------------------------------------------

    def inquiry(self, transaction):
        """Postpaid bill inquiry (inq-pasca).

        Prepaid has no inquiry endpoint at Digiflazz: pricing comes from the SKU
        map. Rather than fake a success, say so -- a caller asking to inquire a
        prepaid product has a configuration bug worth surfacing.
        """
        if not self._is_postpaid(transaction):
            raise NotImplementedError(
                "Digiflazz has no prepaid inquiry endpoint. Product %s is marked "
                "inquiry_required but is not a postpaid product." % transaction.product_id.code
            )
        status_code, data = self._post(PATH_TRANSACTION, self._transaction_payload(transaction, CMD_INQUIRY_POSTPAID))
        return self._result_from(status_code, data)

    def pay(self, transaction):
        """Prepaid topup, or postpaid payment (pay-pasca).

        Digiflazz's own constraint: a postpaid bill can only be paid on the same
        date it was inquired. That is not enforced here -- it belongs to the
        engine's inquiry->pay flow, and silently blocking a payment the operator
        asked for would be worse than letting Digiflazz reject it explicitly.
        """
        commands = CMD_PAY_POSTPAID if self._is_postpaid(transaction) else None
        status_code, data = self._post(PATH_TRANSACTION, self._transaction_payload(transaction, commands))
        return self._result_from(status_code, data)

    def status(self, provider_ref):
        """Ask Digiflazz for the outcome of ``provider_ref``.

        Prepaid has no read-only status endpoint: the documented method is to
        re-send the identical topup with the same ref_id, which Digiflazz
        deduplicates. So this method reconstructs the original request -- and
        that means it needs the transaction, not just the ref, since the payload
        carries buyer_sku_code and customer_no.

        Two documented hazards are refused outright rather than risked:
          * younger than digiflazz_status_min_age_s -> race/duplicate window
          * older than digiflazz_status_max_age_days -> a re-send is a NEW SALE
        Both return ok=None ("still in progress"), because the reaper only
        refunds on a confirmed failure and neither case confirms anything.
        """
        transaction = self._find_transaction(provider_ref)
        if not transaction:
            return AdapterResult(
                ok=None,
                raw={"state": "unknown"},
                error_code="DIGIFLAZZ_TXN_NOT_FOUND",
                error_message=(
                    "No transaction found for ref_id %s. A prepaid status check "
                    "must re-send the original topup, which needs the SKU and "
                    "customer number -- the ref alone is not enough."
                )
                % provider_ref,
            )

        blocked = self._status_guard(transaction)
        if blocked:
            return blocked

        if self._is_postpaid(transaction):
            status_code, data = self._post(
                PATH_TRANSACTION, self._transaction_payload(transaction, CMD_STATUS_POSTPAID)
            )
        else:
            # A re-send, safe only because ref_id is stable and within retention.
            status_code, data = self._post(PATH_TRANSACTION, self._transaction_payload(transaction))
        result = self._result_from(status_code, data)
        # The reaper reads raw["state"] to decide; translate Digiflazz's
        # vocabulary into the engine's rather than making the reaper speak it.
        if result.raw is not None:
            state = (data or {}).get("status")
            if state == STATUS_SUCCESS:
                result.raw = dict(result.raw, state="success")
            elif state == STATUS_FAILED:
                result.raw = dict(result.raw, state="failed")
        return result

    def _find_transaction(self, provider_ref):
        Txn = self.provider.env["custom.ppob.transaction"]
        txn = Txn.search([("digiflazz_ref_id", "=", provider_ref)], limit=1)
        if txn:
            return txn
        # The reaper falls back to txn.name when provider_ref is unset (pay()
        # never got an answer), and the name still carries its slashes.
        return Txn.search([("name", "=", provider_ref)], limit=1)

    def _status_guard(self, transaction):
        """Return an AdapterResult to abort with, or None to proceed."""
        now = fields.Datetime.now()
        dispatched = transaction.dispatched_at
        if not dispatched:
            return AdapterResult(
                ok=None,
                raw={"state": "unknown"},
                error_code="DIGIFLAZZ_NOT_DISPATCHED",
                error_message="Transaction %s was never dispatched." % transaction.name,
            )

        min_age = max(int(self.provider.digiflazz_status_min_age_s or 0), 0)
        if now - dispatched < timedelta(seconds=min_age):
            return AdapterResult(
                ok=None,
                raw={"state": "too_soon"},
                error_code="DIGIFLAZZ_STATUS_TOO_SOON",
                error_message=(
                    "Refusing to check %s: dispatched less than %ss ago. A "
                    "prepaid status check re-sends the topup, and Digiflazz "
                    "warns that repeat calls inside a minute can duplicate the "
                    "transaction."
                )
                % (transaction.name, min_age),
            )

        max_age_days = max(int(self.provider.digiflazz_status_max_age_days or 0), 0)
        if max_age_days and now - dispatched > timedelta(days=max_age_days):
            _logger.error(
                "Digiflazz: %s is older than %s days; refusing to re-send its "
                "ref_id because that would book a NEW sale. Resolve it manually.",
                transaction.name,
                max_age_days,
            )
            return AdapterResult(
                ok=None,
                raw={"state": "too_old"},
                error_code="DIGIFLAZZ_STATUS_TOO_OLD",
                error_message=(
                    "Refusing to check %s: dispatched more than %s days ago. "
                    "Past Digiflazz's retention a re-sent ref_id is not "
                    "recognised as the original and books a BRAND-NEW charged "
                    "transaction. Resolve this one manually."
                )
                % (transaction.name, max_age_days),
            )
        return None

    def topup(self, amount):
        raise NotImplementedError(
            "Funding the Digiflazz deposit is a manual bank transfer plus a "
            "deposit ticket, not an API purchase. Use the DP-100% provider "
            "topup wizard, and reconcile with action_digiflazz_check_balance."
        )

    # ------------------------------------------------------------------
    # Read-only extras (not part of the four-verb protocol)
    # ------------------------------------------------------------------

    def check_balance(self):
        """cek-saldo. Read-only: moves no money, creates no transaction."""
        status_code, data = self._post(
            PATH_CEK_SALDO,
            {
                "cmd": "deposit",
                "username": self.provider.digiflazz_username or "",
                "sign": self._sign(SIGN_SUFFIX_DEPOSIT),
            },
        )
        if not 200 <= status_code < 300:
            return AdapterResult(
                ok=False,
                raw=data,
                error_code=f"HTTP{status_code}",
                error_message=(data or {}).get("message") or "cek-saldo failed",
            )
        deposit = (data or {}).get("deposit")
        if deposit is None:
            return AdapterResult(
                ok=False,
                raw=data,
                error_code="DIGIFLAZZ_NO_DEPOSIT",
                error_message=(data or {}).get("message") or "cek-saldo response carried no deposit",
            )
        return AdapterResult(ok=True, amount=deposit, raw=data)

    def price_list(self, cmd="prepaid"):
        """price-list. Read-only catalogue fetch, for SKU-map maintenance."""
        if cmd not in ("prepaid", "pasca"):
            raise DigiflazzError("price_list cmd must be 'prepaid' or 'pasca', got %r" % cmd)
        status_code, data = self._post(
            PATH_PRICE_LIST,
            {
                "cmd": cmd,
                "username": self.provider.digiflazz_username or "",
                "sign": self._sign(SIGN_SUFFIX_PRICELIST),
            },
        )
        if not 200 <= status_code < 300:
            return AdapterResult(
                ok=False, raw={"data": data}, error_code=f"HTTP{status_code}", error_message="price-list failed"
            )
        return AdapterResult(ok=True, raw={"data": data})
