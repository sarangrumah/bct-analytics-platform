"""Startup agreement check against ``custom_pdp_masking`` over JSON-RPC.

Contract 05: "If the two disagree, joins break silently and the discrepancy will surface as a
reconciliation failure, not as an error -- so verify equality with a cross-language test on shared
vectors."

That is the whole reason this module exists. A digest mismatch has no symptom at load time. Every
row still lands, every dbt model still builds, every dashboard still renders -- and every join
between a hashed key produced by Odoo and one produced by the loader returns nothing. The failure
appears weeks later as a reconciliation number that is quietly wrong. So the loader checks it once,
at startup, before it opens a replication slot, and refuses to run if it does not match.

Two checks, not one:

* the **published spec** (``get_digest_spec``) must describe the construction this loader implements;
* the **actual digests** (``hash_value`` with the shared test salt) must equal the loader's output
  on the known-answer vectors. A spec can be right while an implementation drifts.
"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request

from .pdp_hash import KNOWN_ANSWER_VECTORS, PDP_DIGEST_ALGORITHM, pdp_hmac_sha256

_logger = logging.getLogger(__name__)

#: An opener built with **only** HTTP and HTTPS handlers.
#:
#: ``urllib.request.urlopen`` uses the global opener, which carries ``FileHandler`` and
#: ``FTPHandler``. In a process holding the warehouse credentials and the per-tenant masking salt,
#: that is a local-file-read primitive the moment the configured URL can be influenced: a
#: ``file:///etc/passwd`` or ``file:///run/secrets/...`` URL would be fetched and its contents
#: compared against the digest spec.
#:
#: Removing the capability beats checking for it, so this is a structural fix rather than a
#: validated one -- ``opener.open("file:///etc/passwd")`` raises ``URLError: unknown url type``.
#: The scheme assertion at construction stays as well, because it turns a misconfiguration into a
#: clear startup error instead of a runtime URLError.
class _JsonContentTypeHandler(urllib.request.BaseHandler):
    """Set ``Content-Type: application/json`` on every outgoing request.

    Needed because this opener deliberately does not build a ``urllib.request.Request`` to carry
    headers. ``OpenerDirector.addheaders`` cannot do the job: ``AbstractHTTPHandler.do_request_``
    applies the default ``application/x-www-form-urlencoded`` *before* it consults ``addheaders``,
    and only fills a header that is not already present -- so the default always wins and Odoo
    answers ``415 UNSUPPORTED MEDIA TYPE``. Found by running it, not by reading the source.

    ``handler_order`` below 500 puts this ahead of ``AbstractHTTPHandler`` in the request-processing
    chain, so the correct type is already set by the time the default would be applied.
    """

    handler_order = 100

    def http_request(self, request):
        if request.data is not None and not request.has_header("Content-type"):
            request.add_unredirected_header("Content-type", "application/json")
        return request

    https_request = http_request


def _build_http_only_opener():
    """Build an opener that physically cannot speak anything but HTTP(S).

    ``build_opener()`` is the obvious call and it is WRONG here: it *adds* to the default handler
    set rather than replacing it, so ``FileHandler`` and ``FTPHandler`` survive and
    ``file:///etc/passwd`` still opens. Verified, not assumed -- the first version of this function
    used ``build_opener`` and a test read ``/etc/passwd`` straight through it.

    An ``OpenerDirector`` built by hand carries only what is added. ``UnknownHandler`` is required:
    without it an unsupported scheme falls off the end of the handler chain and ``open()`` returns
    ``None`` instead of raising, which is a silent failure rather than a refusal.

    ``HTTPRedirectHandler`` is deliberately omitted. Odoo's JSON-RPC endpoint has no reason to
    redirect, and following one would let a 302 walk this client to an arbitrary host while holding
    the warehouse credentials and the masking salt.
    """
    opener = urllib.request.OpenerDirector()
    for handler in (
        urllib.request.HTTPHandler,
        urllib.request.HTTPSHandler,
        urllib.request.HTTPDefaultErrorHandler,
        urllib.request.HTTPErrorProcessor,
        urllib.request.UnknownHandler,
        _JsonContentTypeHandler,
    ):
        opener.add_handler(handler())
    return opener


_HTTP_ONLY_OPENER = _build_http_only_opener()


class DigestSpecMismatch(RuntimeError):
    """The Odoo module and the loader do not agree on the digest. Fatal at startup."""


class OdooRpcError(RuntimeError):
    pass


class OdooClient:
    def __init__(self, url: str, db: str, login: str, password: str, timeout: float = 20.0) -> None:
        scheme = urllib.parse.urlparse(url).scheme
        if scheme not in ("http", "https"):
            # Defence in depth alongside _HTTP_ONLY_OPENER: this turns a misconfigured
            # CDC_ODOO_URL into a clear startup error rather than a runtime URLError.
            raise ValueError("CDC_ODOO_URL must be http or https, got %r" % scheme)
        self.url = url.rstrip("/")
        self.db = db
        self.login = login
        self.password = password
        self.timeout = timeout
        self._uid = None

    def _call(self, service: str, method: str, args: list):
        payload = {
            "jsonrpc": "2.0",
            "method": "call",
            "params": {"service": service, "method": method, "args": args},
            "id": 1,
        }
        body = json.dumps(payload).encode("utf-8")
        response = _HTTP_ONLY_OPENER.open(
            self.url + "/jsonrpc", data=body, timeout=self.timeout
        )
        with response:
            parsed = json.loads(response.read().decode("utf-8"))
        if "error" in parsed:
            # Never echo args: they can carry a salt or a personal value.
            raise OdooRpcError(
                "Odoo JSON-RPC %s.%s failed: %s"
                % (service, method, parsed["error"].get("message", "unknown"))
            )
        return parsed.get("result")

    def authenticate(self) -> int:
        if self._uid is None:
            uid = self._call("common", "authenticate", [self.db, self.login, self.password, {}])
            if not uid:
                raise OdooRpcError("Odoo authentication failed for the digest-spec check")
            self._uid = uid
        return self._uid

    def execute(self, model: str, method: str, args: list, kwargs: dict | None = None):
        uid = self.authenticate()
        return self._call(
            "object",
            "execute_kw",
            [self.db, uid, self.password, model, method, args, kwargs or {}],
        )


#: The fields of ``get_digest_spec()`` the loader depends on, and the value it requires.
REQUIRED_SPEC = {
    "algorithm": PDP_DIGEST_ALGORITHM,
    "primitive": "hmac",
    "digest": "sha256",
    "key_encoding": "utf-8",
    "message_encoding": "utf-8",
    "null_in_null_out": True,
    "empty_string_is_null": True,
}


def verify_digest_agreement(client: OdooClient) -> dict:
    """Assert the loader and ``custom_pdp_masking`` produce identical digests. Raises on mismatch."""
    spec = client.execute("pdp.masking.rule", "get_digest_spec", [])
    if not isinstance(spec, dict):
        raise DigestSpecMismatch("pdp.masking.rule.get_digest_spec() returned %r" % type(spec))

    for key, expected in REQUIRED_SPEC.items():
        actual = spec.get(key)
        if actual != expected:
            raise DigestSpecMismatch(
                "Digest spec disagreement on %r: Odoo says %r, the loader implements %r. "
                "Refusing to start -- a digest mismatch has no symptom at load time and surfaces "
                "much later as a reconciliation failure." % (key, actual, expected)
            )
    normalisation = str(spec.get("normalisation", ""))
    if not normalisation.startswith("none"):
        raise DigestSpecMismatch(
            "Digest spec says normalisation=%r; the loader applies none (no trim, no case fold, no "
            "Unicode normalisation)." % normalisation
        )
    if "lowercase hex" not in str(spec.get("output", "")):
        raise DigestSpecMismatch("Digest spec output is %r, expected lowercase hex" % spec.get("output"))

    # The spec can be right while the implementation has drifted. Compare real digests, using the
    # published test salts -- never the production salt, which must not travel over RPC.
    for value, salt, expected_digest in KNOWN_ANSWER_VECTORS:
        ours = pdp_hmac_sha256(value, salt)
        if ours != expected_digest:
            raise DigestSpecMismatch(
                "Loader failed its own known-answer vector for a test salt; the build is broken."
            )
        theirs = client.execute("pdp.masking.rule", "hash_value", [value, salt])
        if theirs != expected_digest:
            raise DigestSpecMismatch(
                "custom_pdp_masking.hash_value disagrees with the frozen known-answer vector for a "
                "test salt: Odoo returned %r, contract 05 pins %r. Every digest already in the "
                "warehouse is built on the pinned value; this is a migration, not a bug fix."
                % (theirs, expected_digest)
            )
    _logger.info(
        "digest agreement verified against custom_pdp_masking: %d known-answer vectors match",
        len(KNOWN_ANSWER_VECTORS),
    )
    return spec
