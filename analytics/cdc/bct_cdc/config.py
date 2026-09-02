"""Loader configuration, assembled from the environment only.

Nothing here is read from a tracked file. Per-tenant salts come from ``WAREHOUSE_MASK_SALT_<TENANT>``
(SOPS-managed, ``changeme`` in ``.env.example``) and are never logged, never written to
``pipeline_state`` and never returned by an HTTP endpoint.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

#: Deliberately EMPTY. The set of replicated tables is read at runtime from
#: ``warehouse.column_policy`` -- DWH decides what is replicated by classifying it, and the loader
#: follows. A constant here would drift the first time DWH added or removed a table, as it already
#: has: ``res_users`` is classified in Odoo but deliberately absent from the warehouse policy.
#: ``CDC_SOURCE_TABLES`` narrows the list for tests; it can never widen it past the policy.
DEFAULT_SOURCE_TABLES = ()


def _tenant_key(tenant: str) -> str:
    """``erp_dev`` -> ``ERP_DEV``. MODULE_KNOWLEDGE.md §2 "Salt resolution"."""
    return "".join(c if c.isalnum() else "_" for c in tenant).upper()


class SaltMissing(RuntimeError):
    """Raised when no salt resolves for a tenant. Never degrade to an unkeyed hash."""


def resolve_salt(tenant: str, environ: dict[str, str] | None = None) -> str:
    """Resolve the per-tenant salt, first hit wins, exactly as the Odoo module does.

    1. ``WAREHOUSE_MASK_SALT_<TENANT>``
    2. ``WAREHOUSE_MASK_SALT_DEFAULT``

    The module's third source (``ir.config_parameter``) is deliberately *not* reachable here: the
    loader connects as ``warehouse_reader``, which is the point -- a salt the loader could read out
    of the ERP database would be a salt an ERP compromise leaks.
    """
    env = os.environ if environ is None else environ
    salt = env.get("WAREHOUSE_MASK_SALT_" + _tenant_key(tenant)) or env.get(
        "WAREHOUSE_MASK_SALT_DEFAULT"
    )
    if not salt:
        raise SaltMissing(
            f"No masking salt for tenant {tenant!r}. Set WAREHOUSE_MASK_SALT_{_tenant_key(tenant)} "
            "or WAREHOUSE_MASK_SALT_DEFAULT. Refusing to start: hashing without a salt would "
            "produce an unkeyed digest that is reversible by dictionary attack."
        )
    return salt


@dataclass(frozen=True)
class Settings:
    """Everything the CDC consumer needs, resolved once at startup."""

    tenant: str
    slug: str

    source_dsn: str
    source_replication_dsn: str
    warehouse_dsn: str

    publication: str
    slot: str

    source_tables: tuple[str, ...] = DEFAULT_SOURCE_TABLES
    batch_size: int = 2000
    status_interval_seconds: float = 10.0
    metrics_port: int = 9108
    odoo_url: str = "http://odoo:8069"
    odoo_db: str = "bct"
    odoo_login: str = ""
    odoo_password: str = ""
    verify_digest_spec: bool = True
    _salt: str = field(default="", repr=False)

    @property
    def salt(self) -> str:
        return self._salt

    def __repr__(self) -> str:  # pragma: no cover - defensive, keeps salts out of tracebacks
        return (
            f"Settings(tenant={self.tenant!r}, slug={self.slug!r}, publication={self.publication!r},"
            f" slot={self.slot!r}, tables={len(self.source_tables)}, salt=<redacted>)"
        )


def _require(name: str, environ: dict[str, str]) -> str:
    value = environ.get(name, "")
    if not value:
        raise RuntimeError(f"Required environment variable {name} is unset")
    return value


def settings_from_env(environ: dict[str, str] | None = None) -> Settings:
    env = dict(os.environ if environ is None else environ)

    tenant = env.get("CDC_TENANT_DB") or env.get("ODOO_DB_NAME") or "bct"
    slug = env.get("CDC_TENANT_SLUG") or tenant

    host = env.get("CDC_SOURCE_HOST", env.get("POSTGRES_HOST", "postgres"))
    port = env.get("CDC_SOURCE_PORT", env.get("POSTGRES_PORT", "5432"))
    user = env.get("WAREHOUSE_READER_USER", "warehouse_reader")
    password = _require("WAREHOUSE_READER_PASSWORD", env)

    base = f"host={host} port={port} dbname={tenant} user={user} password={password}"

    wh_host = env.get("CDC_WAREHOUSE_HOST", "warehouse-db")
    wh_port = env.get("CDC_WAREHOUSE_PORT", "5432")
    wh_db = env.get("WAREHOUSE_DB", "warehouse")
    # warehouse_loader, never `warehouse` (that is dbt's) and never `warehouse_admin` (superuser,
    # which bypasses RLS unconditionally). Contract 05 section A.
    wh_user = env.get("WAREHOUSE_LOADER_USER", "warehouse_loader")
    wh_password = _require("WAREHOUSE_LOADER_PASSWORD", env)

    tables = env.get("CDC_SOURCE_TABLES")
    source_tables = (
        tuple(t.strip() for t in tables.split(",") if t.strip())
        if tables
        else DEFAULT_SOURCE_TABLES
    )

    return Settings(
        tenant=tenant,
        slug=slug,
        source_dsn=base,
        # A logical decoding connection names a real database and appends replication=database.
        # Contract 04 §2: pg_hba's `all` line already matches it; do not add a `host replication` line.
        source_replication_dsn=base + " replication=database",
        warehouse_dsn=(
            f"host={wh_host} port={wh_port} dbname={wh_db} user={wh_user} password={wh_password}"
        ),
        # `or`, not a dict default. An env var that is PRESENT BUT EMPTY must mean "not set" here:
        # the run script forwards `-e CDC_PUBLICATION="${CDC_PUBLICATION:-}"` so the variable is
        # always defined in the container, and `env.get(name, default)` would then return "" and
        # silently override the per-tenant default. That produced `publication=''` and a startup
        # refusal that named the right problem for the wrong reason.
        publication=env.get("CDC_PUBLICATION") or "bct_cdc_%s" % slug,
        slot=env.get("CDC_SLOT") or "bct_slot_%s" % slug,
        source_tables=source_tables,
        batch_size=int(env.get("CDC_BATCH_SIZE", "2000")),
        status_interval_seconds=float(env.get("CDC_STATUS_INTERVAL_SECONDS", "10")),
        metrics_port=int(env.get("CDC_METRICS_PORT", "9108")),
        odoo_url=env.get("CDC_ODOO_URL", "http://odoo:8069"),
        odoo_db=env.get("CDC_ODOO_DB", tenant),
        odoo_login=env.get("CDC_ODOO_LOGIN", ""),
        odoo_password=env.get("CDC_ODOO_PASSWORD", ""),
        verify_digest_spec=env.get("CDC_VERIFY_DIGEST_SPEC", "1") not in ("0", "false", "no"),
        _salt=resolve_salt(tenant, env),
    )
