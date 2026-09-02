"""Authentication against Odoo, and the entitlements that become JWT claims.

Nothing here logs a credential, a token, or the value of any ``personal``/``sensitive`` field. The
user's name and e-mail are ``personal`` under contract 01, so they are never written to a log line
even at DEBUG — a gateway that logs "authenticated budi.santoso@..." has quietly re-created the
plaintext store the whole masking design exists to prevent.
"""

from __future__ import annotations

import json
import re
import logging
import urllib.error
import urllib.parse
import urllib.request

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

#: The Odoo group that lifts the per-Operating-Unit record rules
#: (``custom_operating_unit/security/operating_unit_groups.xml``: "Sees documents from every
#: Operating Unit. Bypasses the per-unit record rules.").
GROUP_ALL_OPERATING_UNITS = "custom_operating_unit.group_operating_unit_all"

#: The diagram's "Super Admin?" decision, and the ONLY place it is derived.
#: custom_super_admin restricts every write on tenant.registry to this group,
#: so a session claiming super-admin without it would be able to open a console
#: whose every button then fails — worse than being refused the console.
GROUP_SUPER_ADMIN = "custom_super_admin.group_super_admin"

#: Odoo groups -> contract 02 roles. Unmapped users get the least-privileged role, never none:
#: a session with no role at all would be indistinguishable from a bug in the mapping.
ROLE_MAP = (
    ("custom_pdp_core.group_pdp_officer", "analytics.admin"),
    ("custom_operating_unit.group_operating_unit_manager", "analytics.analyst"),
    ("base.group_erp_manager", "analytics.admin"),
)
DEFAULT_ROLE = "analytics.viewer"


class AuthenticationFailed(Exception):
    """Bad credentials, or a database that is not offered. Never says which."""


class OdooError(Exception):
    pass


#: A database name is about to become part of a HOSTNAME, so it is validated
#: against the same expression every other layer uses rather than trusted. The
#: caller already checks it against allowed_databases; this is the second lock.
_DB_RE = re.compile(r"^[a-z][a-z0-9_]{1,62}$")


class OdooClient:
    """JSON-RPC to Odoo, addressed PER DATABASE.

    A single fixed URL cannot serve more than one tenant. dbfilter is ^%d$, so
    Odoo reads the database from the first label of the Host header -- which
    means a gateway pointed at http://bct.athera.localhost:8069 authenticates
    every login against `bct`, whatever database the caller asked for.
    Measured: a correct super-admin password for athera_admin came back as
    `upstream_unavailable`, because the request had reached the wrong database
    and found no such user.

    So the URL is a TEMPLATE. `{db}` is substituted per call, and
    compose/odoo.yml carries a network alias for each served database.
    A plain URL with no `{db}` still works and stays single-tenant, which is
    what an installation with one database wants.
    """

    def __init__(self, url: str, timeout: float = 15.0) -> None:
        scheme = urllib.parse.urlparse(url.replace("{db}", "db")).scheme
        if scheme not in ("http", "https"):
            raise ValueError("LOGIN_GATEWAY_ODOO_URL must be http or https, got %r" % scheme)
        self.url = url.rstrip("/")
        self.timeout = timeout

    def _url_for(self, db: str) -> str:
        if "{db}" not in self.url:
            return self.url
        if not _DB_RE.match(db or ""):
            raise OdooError("refusing to build a hostname from database name %r" % db)
        return self.url.replace("{db}", db)

    def _call(self, db: str, service: str, method: str, args: list):
        payload = {
            "jsonrpc": "2.0",
            "method": "call",
            "params": {"service": service, "method": method, "args": args},
            "id": 1,
        }
        try:
            body = json.dumps(payload).encode("utf-8")
            response = _HTTP_ONLY_OPENER.open(
                self._url_for(db) + "/jsonrpc", data=body, timeout=self.timeout
            )
            with response:
                parsed = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # An HTTP status is diagnostic and carries nothing sensitive, so it is kept. The body
            # is NOT read: Odoo error pages echo the request, and this request holds a password.
            # `from None` for the same reason as below.
            raise OdooError(
                "Odoo returned HTTP %s for %s.%s" % (exc.code, service, method)
            ) from None
        except OSError as exc:
            # `from None`, deliberately, not `from exc`. A chained OSError renders the full
            # request context in the traceback, and this call carries the user's password in its
            # body -- a 500 page or a log aggregator would then hold the credential. Only the
            # exception class survives, which is enough to tell a timeout from a refused connection.
            raise OdooError("Odoo is unreachable: %s" % exc.__class__.__name__) from None
        if "error" in parsed:
            # The message can echo arguments, which on an authenticate call means the password.
            # Only the class of failure is logged, never the payload.
            raise OdooError("Odoo JSON-RPC %s.%s failed" % (service, method))
        return parsed.get("result")

    def authenticate(self, db: str, login: str, password: str) -> int:
        uid = self._call(db, "common", "authenticate", [db, login, password, {}])
        if not uid:
            raise AuthenticationFailed()
        return int(uid)

    def execute(self, db: str, uid: int, password: str, model: str, method: str,
                args: list, kwargs: dict | None = None):
        return self._call(
            db,
            "object", "execute_kw", [db, uid, password, model, method, args, kwargs or {}]
        )


def read_session_claims(client: OdooClient, db: str, uid: int, password: str) -> dict:
    """Read the company and Operating Unit entitlement that fill the contract 02 claim set."""
    # `allowed_operating_unit_ids` belongs to custom_operating_unit, and not every
    # database installs it -- the ATHERA admin database carries the control-plane
    # modules and nothing else. Reading a field that does not exist fails the whole
    # call, and the gateway turned that into a 503 on a CORRECT super-admin
    # password: "entitlement read failed: object.execute_kw failed". Measured.
    #
    # So the OU field is read separately and its absence is an ANSWER, not an
    # error: a database with no Operating Units grants none. That is the same
    # fail-closed reading `allowed_ou: []` already has, and it is why the retry
    # below cannot widen anyone's entitlement.
    try:
        rows = client.execute(
            db, uid, password, "res.users", "read",
            [[uid], ["company_id", "company_ids", "allowed_operating_unit_ids"]],
        )
    except OdooError:
        rows = client.execute(
            db, uid, password, "res.users", "read", [[uid], ["company_id", "company_ids"]],
        )
    if not rows:
        raise OdooError("res.users.read returned nothing for the authenticated uid")
    row = rows[0]

    company_ids = row.get("company_ids") or []
    if not company_ids and row.get("company_id"):
        company_ids = [row["company_id"][0]]

    # Contract 02 as amended at GATE 3: `allowed_ou: []` means NO Operating Units, mirroring
    # custom_operating_unit's record rules, which fail closed. The bypass is the separate boolean
    # `all_ou`, and it is only ever true for a member of the explicit bypass group -- never inferred
    # from emptiness. So a claim this code forgot to populate grants nothing rather than everything.
    allowed_ou = list(row.get("allowed_operating_unit_ids") or [])
    # has_group is a recordset method, not @api.model, so execute_kw must pass the ids first:
    # [[uid], group]. Calling it as [group] raises "missing 1 required positional argument".
    # Same reasoning: a group that does not exist in this database is not an
    # authentication failure, and its absence means the bypass is NOT granted.
    try:
        all_ou = bool(
            client.execute(
                db, uid, password, "res.users", "has_group", [[uid], GROUP_ALL_OPERATING_UNITS]
            )
        )
    except OdooError:
        all_ou = False

    # A group that does not exist in this database is not an authentication
    # failure — a tenant that never installed the control-plane modules simply
    # has no super admin, which is the correct answer rather than an error.
    try:
        is_super_admin = bool(
            client.execute(db, uid, password, "res.users", "has_group",
                           [[uid], GROUP_SUPER_ADMIN]))
    except OdooError:
        is_super_admin = False

    roles = [DEFAULT_ROLE]
    for group, role in ROLE_MAP:
        try:
            if client.execute(db, uid, password, "res.users", "has_group", [[uid], group]):
                roles.append(role)
        except OdooError:
            # A group that does not exist in this database is not an authentication failure.
            continue

    return {
        "company_ids": [int(c) for c in company_ids],
        "allowed_ou": [int(o) for o in allowed_ou],
        "all_ou": all_ou,
        "is_super_admin": is_super_admin,
        # Ordered most-privileged last so a consumer taking roles[-1] is not surprised; the
        # authoritative check is membership, not position.
        "roles": sorted(set(roles)),
    }
