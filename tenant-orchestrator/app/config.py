"""Settings, read once from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    #: DSN for the control-plane database. The role behind it holds SELECT,
    #: INSERT and UPDATE on tenant_registry and CREATEDB, and nothing else --
    #: notably not SUPERUSER, so it cannot bypass RLS anywhere in the platform.
    registry_dsn: str
    #: Shared with custom_super_admin, which signs every call with it.
    shared_secret: str
    #: Replay window for a signed request, in seconds.
    hmac_window_seconds: int
    #: Odoo, addressed by the ADMIN database's hostname. dbfilter is ^%d$, so a
    #: URL whose first label is not a database name reaches no database at all.
    odoo_url: str
    odoo_db: str
    odoo_login: str
    odoo_password: str
    #: What a freshly provisioned tenant gets installed.
    provision_modules: tuple


def settings_from_env(environ: dict | None = None) -> Settings:
    env = dict(os.environ if environ is None else environ)

    secret = env.get("ORCHESTRATOR_SHARED_SECRET", "")
    if len(secret) < 32 or "changeme" in secret:
        # Refuse to start rather than run an HMAC boundary that a guess could
        # cross. A short or placeholder secret here is indistinguishable, from
        # the outside, from having no authentication at all.
        raise RuntimeError(
            "ORCHESTRATOR_SHARED_SECRET must be set to at least 32 real characters."
        )

    dsn = env.get("ORCHESTRATOR_REGISTRY_DSN", "")
    if not dsn:
        raise RuntimeError("ORCHESTRATOR_REGISTRY_DSN is required.")

    modules = env.get("ORCHESTRATOR_PROVISION_MODULES", "")
    return Settings(
        registry_dsn=dsn,
        shared_secret=secret,
        hmac_window_seconds=int(env.get("ORCHESTRATOR_HMAC_WINDOW_SECONDS", "300")),
        odoo_url=env.get("ORCHESTRATOR_ODOO_URL", "http://athera_admin.athera.localhost:8069"),
        odoo_db=env.get("ATHERA_ADMIN_DB", "athera_admin"),
        odoo_login=env.get("ORCHESTRATOR_ODOO_LOGIN", "admin"),
        odoo_password=env.get("ORCHESTRATOR_ODOO_PASSWORD", ""),
        provision_modules=tuple(m.strip() for m in modules.split(",") if m.strip()),
    )
