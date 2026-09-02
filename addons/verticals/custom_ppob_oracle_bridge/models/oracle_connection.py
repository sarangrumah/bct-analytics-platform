# -*- coding: utf-8 -*-
"""Oracle DB connection configuration + process-level connection pool.

All SP calls and direct queries go through this model so credentials, pool
sizing, and SP defaults are centralised. Pool is cached per (id, dsn, username)
at the process level and rebuilt on credential/DSN change.
"""

import logging
import threading

from odoo import _, api, fields, models
from odoo.exceptions import UserError

try:
    import oracledb
except ImportError:
    oracledb = None

_logger = logging.getLogger(__name__)


class OracleConnection(models.Model):
    _name = "custom.ppob.oracle.connection"
    _description = "Oracle Bridge Connection Configuration"
    _order = "active desc, id"
    _inherit = ["mail.thread"]

    name = fields.Char(required=True, tracking=True)
    active = fields.Boolean(default=True, tracking=True)

    # ---------- DB connection ----------
    dsn = fields.Char(
        string="DSN",
        required=True,
        tracking=True,
        help="Oracle Easy Connect string: host:port/service_name. Example: oracle-prod.internal:1521/EVSHOPDB",
    )
    username = fields.Char(required=True, tracking=True)
    password = fields.Char(
        required=True,
        help="Service-account DB password. Stored encrypted at rest by Odoo.",
    )
    schema_name = fields.Char(
        default="mobile",
        tracking=True,
        help="Default Oracle schema for SP calls and table queries.",
    )
    pool_min = fields.Integer(default=2)
    pool_max = fields.Integer(default=10)
    connect_timeout = fields.Integer(default=5, string="Connect Timeout (s)")
    stmt_timeout = fields.Integer(default=30, string="Statement Timeout (s)")

    # ---------- SP default parameters (service-account auth) ----------
    sp_default_usr = fields.Char(string="SP Default usr", default="ODOO_BRIDGE", tracking=True)
    sp_default_pwd = fields.Char(string="SP Default pwd")
    sp_default_macadd = fields.Char(string="SP Default macadd", default="ODOO-BRIDGE", tracking=True)
    # noqa: S104 is a false positive by string match. This is a VALUE passed to an
    # Oracle stored procedure as its `ip` argument, not a socket bind address;
    # nothing in this module listens on anything.
    sp_default_ip = fields.Char(string="SP Default IP", default="0.0.0.0", tracking=True)  # noqa: S104

    # ---------- Test connection state ----------
    last_test_at = fields.Datetime(readonly=True)
    last_test_ok = fields.Boolean(readonly=True)
    last_test_msg = fields.Text(readonly=True)
    server_version = fields.Char(readonly=True)

    _name_uniq = models.Constraint(
        "unique(name)",
        "Connection name must be unique.",
    )

    # ---------- Pool management ----------
    _pool_lock = threading.Lock()
    _pool_cache = {}

    @api.model
    def _get_active(self):
        rec = self.search([("active", "=", True)], limit=1)
        if not rec:
            raise UserError(
                _("Tidak ada koneksi Oracle aktif. Konfigurasi via Configuration > Oracle Bridge > Connection.")
            )
        return rec

    def _get_pool(self):
        self.ensure_one()
        if oracledb is None:
            raise UserError(_("Library `oracledb` belum ter-install. Jalankan: pip install oracledb>=2.0"))
        key = (self.id, self.dsn, self.username)
        with self._pool_lock:
            pool = self._pool_cache.get(key)
            if pool is None:
                pool = oracledb.create_pool(
                    user=self.username,
                    password=self.password or "",
                    dsn=self.dsn,
                    min=max(self.pool_min, 1),
                    max=max(self.pool_max, self.pool_min, 1),
                    timeout=max(self.connect_timeout, 1),
                )
                self._pool_cache[key] = pool
                _logger.info("Oracle pool opened for %s@%s", self.username, self.dsn)
            return pool

    def _drop_pool(self):
        self.ensure_one()
        key = (self.id, self.dsn, self.username)
        with self._pool_lock:
            pool = self._pool_cache.pop(key, None)
            if pool is not None:
                try:
                    pool.close()
                except Exception:
                    _logger.exception("Failed to close Oracle pool")

    # ---------- Public API ----------
    def action_test_connection(self):
        self.ensure_one()
        try:
            pool = self._get_pool()
            with pool.acquire() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT banner FROM v$version WHERE rownum = 1")
                    row = cur.fetchone()
                    version = row[0] if row else "unknown"
            self.write(
                {
                    "last_test_at": fields.Datetime.now(),
                    "last_test_ok": True,
                    "last_test_msg": "OK",
                    "server_version": version,
                }
            )
            return self._notify(_("Connection OK: %s") % version, "success")
        except Exception as exc:
            _logger.exception("Oracle test connection failed")
            self.write(
                {
                    "last_test_at": fields.Datetime.now(),
                    "last_test_ok": False,
                    "last_test_msg": str(exc)[:1000],
                }
            )
            return self._notify(_("Connection failed: %s") % exc, "danger")

    def _notify(self, message, ntype):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"title": _("Oracle Connection"), "message": message, "type": ntype},
        }

    def execute_sp(self, sp_name, params, out_specs):
        """Execute an Oracle stored procedure with named parameters.

        :param out_specs: {param_name: oracledb_type} for OUT params.
        :return: {param_name: value} of OUT params.
        """
        self.ensure_one()
        if oracledb is None:
            raise UserError(_("oracledb library not installed."))
        full_name = sp_name if "." in sp_name else f"{self.schema_name}.{sp_name}"
        pool = self._get_pool()
        with pool.acquire() as conn:
            with conn.cursor() as cur:
                bind = dict(params)
                for name, dtype in out_specs.items():
                    bind[name] = cur.var(dtype)
                cur.callproc(full_name, keyword_parameters=bind)
                result = {name: bind[name].getvalue() for name in out_specs}
                conn.commit()
        _logger.info("SP %s called with keys=%s, OUT=%s", full_name, list(params.keys()), list(out_specs.keys()))
        return result

    def query(self, sql, params=None, fetch="all"):
        """Execute a SELECT. fetch='all' -> list of tuples, 'one' -> tuple/None."""
        self.ensure_one()
        if oracledb is None:
            raise UserError(_("oracledb library not installed."))
        pool = self._get_pool()
        with pool.acquire() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params or {})
                if fetch == "all":
                    return cur.fetchall()
                if fetch == "one":
                    return cur.fetchone()
                return list(cur)

    def write(self, vals):
        sensitive = {"dsn", "username", "password", "pool_min", "pool_max"}
        rebuild = bool(set(vals) & sensitive)
        res = super().write(vals)
        if rebuild:
            for rec in self:
                rec._drop_pool()
        return res

    def unlink(self):
        for rec in self:
            rec._drop_pool()
        return super().unlink()
