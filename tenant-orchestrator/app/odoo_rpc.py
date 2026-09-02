"""The one call this service makes into Odoo: enqueue a provisioning job.

WHY IT DELEGATES INSTEAD OF DOING IT.

Building an Odoo database means running `odoo -d <db> -i <modules>`. Odoo's RPC
route to create one is closed -- ``exp_create_database`` carries
``@check_db_management_enabled``, which refuses whenever ``list_db`` is False,
and it is False here on purpose. The upstream platform repo's answer is to give
this service ``/var/run/docker.sock`` and shell out. That would make a
network-facing HTTP service root on the host, in a stack where everything else
runs ``cap_drop: ALL`` and ``read_only``.

So the privilege stays where it already was: ``custom_athera_provisioner``
inside Odoo spawns the CLI as a child process, and this service only asks it
to. The orchestrator holds a Postgres role and an Odoo login. Nothing else.
"""

from __future__ import annotations

import http.client
import logging
import urllib.parse
import xmlrpc.client

logger = logging.getLogger("orchestrator.odoo")


class OdooError(Exception):
    pass


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """Claim one hostname, connect to another address.

    ``dbfilter`` is ``^%d$``, so Odoo reads the database from the FIRST LABEL of
    the Host header -- and http.client derives Host from the CONNECTION's host,
    not from the URL. Overriding either alone cannot produce "Host says
    athera_admin.athera.localhost, socket goes to the odoo service". This splits
    them, which also keeps the service working when the compose alias and the
    routable address differ (a VPS split, say).
    """

    def __init__(self, vhost, port, connect_addr, **kwargs):
        super().__init__(vhost, port, **kwargs)
        self._connect_addr = connect_addr

    def connect(self):
        self.sock = self._create_connection(
            self._connect_addr, self.timeout, self.source_address
        )
        if self._tunnel_host:
            self._tunnel()


class _Transport(xmlrpc.client.Transport):
    def __init__(self, connect_addr, timeout=30, **kwargs):
        super().__init__(**kwargs)
        self._connect_addr = connect_addr
        self._timeout = timeout

    def make_connection(self, host):
        if self._connection and host == self._connection[0]:
            return self._connection[1]
        chost, _extra, _x509 = self.get_host_info(host)
        vhost, _, vport = chost.partition(":")
        conn = _PinnedHTTPConnection(
            vhost, int(vport) if vport else None, self._connect_addr,
            timeout=self._timeout,
        )
        self._connection = host, conn
        return conn


class OdooClient:
    def __init__(self, base_url: str, db: str, login: str, password: str) -> None:
        parsed = urllib.parse.urlsplit(base_url)
        self._vhost = parsed.hostname or "odoo"
        self._port = parsed.port or 8069
        self._db = db
        self._login = login
        self._password = password
        self._uid: int | None = None

    def _proxy(self, endpoint: str, timeout: int = 60):
        return xmlrpc.client.ServerProxy(
            "http://%s:%d/xmlrpc/2/%s" % (self._vhost, self._port, endpoint),
            allow_none=True,
            transport=_Transport((self._vhost, self._port), timeout=timeout),
        )

    def authenticate(self) -> int:
        if self._uid is not None:
            return self._uid
        try:
            uid = self._proxy("common").authenticate(
                self._db, self._login, self._password, {}
            )
        except Exception as exc:  # noqa: BLE001
            raise OdooError("Odoo is unreachable: %s" % exc) from exc
        if not uid:
            raise OdooError("Odoo refused the orchestrator's credentials.")
        self._uid = int(uid)
        return self._uid

    def enqueue_provision(self, slug: str, modules: list[str], admin_password: str) -> dict:
        uid = self.authenticate()
        try:
            # A short timeout is correct here BECAUSE the call only enqueues.
            # The install itself takes minutes and runs in the job runner; if
            # this ever starts blocking for that long, something has quietly
            # stopped being a job.
            return self._proxy("object", timeout=60).execute_kw(
                self._db, uid, self._password,
                "athera.provisioner", "enqueue_provision",
                [slug],
                {"modules": modules, "admin_password": admin_password},
            )
        except xmlrpc.client.Fault as fault:
            raise OdooError(str(fault.faultString)[:500]) from fault
        except Exception as exc:  # noqa: BLE001
            raise OdooError("Odoo is unreachable: %s" % exc) from exc

    def ping(self) -> bool:
        try:
            self._proxy("common", timeout=10).version()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error("odoo ping failed: %s", exc)
            return False
