# -*- coding: utf-8 -*-
"""Build a tenant's Odoo database as a queue_job.

See the module docstring in ``__manifest__.py`` for why this lives inside Odoo
rather than in the orchestrator.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess

from odoo import api, models
from odoo.exceptions import AccessDenied, UserError
from odoo.sql_db import db_connect

_logger = logging.getLogger(__name__)

#: The same expression as scripts/lib/common.sh validate_slug and the CHECK on
#: tenant_registry.tenants.slug. A slug becomes a database name AND a Postgres
#: replication slot name, and slot names forbid dashes -- so all three places
#: enforce the tightest of the constraints, character for character. Two
#: different rules for one identifier is how a tenant gets provisioned that CDC
#: can never follow.
SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{1,30}$")

#: Never provisionable, whatever the caller asks for.
RESERVED = frozenset({"postgres", "template0", "template1", "odoo"})

GROUP_SUPER_ADMIN = "custom_super_admin.group_super_admin"


class AtheraProvisioner(models.AbstractModel):
    _name = "athera.provisioner"
    _description = "ATHERA tenant database provisioner"

    # ------------------------------------------------------------------
    # Entry point. Called over authenticated RPC by tenant-orchestrator.
    # ------------------------------------------------------------------
    @api.model
    def enqueue_provision(self, slug, modules=None, admin_password=None):
        """Validate, then hand the work to the job runner.

        Returns the job's UUID so the caller has something to correlate with;
        it deliberately does NOT wait. Provisioning takes minutes, and no HTTP
        client should be holding a socket open for it.
        """
        self._check_caller()
        slug = self._validate_slug(slug)

        if self._db_exists(slug):
            # Idempotent by refusal rather than by silently succeeding: a
            # caller that believes it created a database it did not create will
            # go on to write a registry row describing someone else's data.
            raise UserError("Database %r already exists; refusing to provision over it." % slug)

        job = self.with_delay(
            description="ATHERA provision tenant %s" % slug,
            channel="root.athera_provision",
        ).provision(slug, modules=modules, admin_password=admin_password)
        _logger.info("athera.provision.enqueued slug=%s job=%s", slug, job.uuid)
        return {"slug": slug, "job_uuid": job.uuid}

    # ------------------------------------------------------------------
    # The job itself.
    # ------------------------------------------------------------------
    def provision(self, slug, modules=None, admin_password=None):
        """Create the database and install the module set, in a child process.

        WHY A SUBPROCESS AND NOT AN IN-PROCESS Registry.new().

        The first version of this called odoo.service.db internals directly, on
        the theory that a queue_job runs outside an HTTP worker. It does not:
        the runner triggers each job with an HTTP GET to /queue_job/runjob, so
        the job body executes inside a request context bound to the ADMIN
        database. Installing `website` into a DIFFERENT database then dies
        inside website's own data file, which calls
        _generate_primary_snippet_templates -> get_current_website() and tries
        to resolve a website against the wrong environment. Measured:

            ParseError: while parsing
            website/views/new_page_template_templates.xml:1049

        Being inside a request also means limit_time_real (240s here) applies,
        and installing this module set takes longer than that.

        `odoo -d <db> -i <modules> --stop-after-init` is the same command
        scripts/tenant-provision.sh has always used, and it runs HERE, inside
        the Odoo container, launched by the process that already holds the
        database credentials. No docker socket, no new grant, and the child gets
        a clean request-free context of its own. It creates the database itself
        if it does not exist.
        """
        slug = self._validate_slug(slug)
        module_names = self._module_list(modules)
        if not module_names:
            raise UserError("Refusing to provision %r with an empty module list." % slug)

        if self._db_exists(slug):
            # Re-checked here, not only in enqueue_provision: a job can be
            # retried or replayed long after it was queued, and by then the
            # database may exist.
            raise UserError("Database %r already exists; refusing to provision over it." % slug)

        cmd = [
            "odoo",
            "-c", os.environ.get("ODOO_RC", "/opt/odoo/conf/odoo.conf"),
            "-d", slug,
            "-i", ",".join(module_names),
            "--stop-after-init",
            "--without-demo=True",
            "--load-language=en_US",
            # The child must not bind a port: this container already has 8069
            # open, and a second listener would fail the whole install.
            "--no-http",
        ]
        _logger.info("athera.provision.start slug=%s modules=%s", slug, module_names)
        # Fixed argv, and slug is validated against SLUG_RE above, so nothing
        # here is shell-interpreted or caller-controlled.
        proc = subprocess.run(  # noqa: S603
            cmd, capture_output=True, text=True, timeout=self._install_timeout(),
        )
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "")[-2000:]
            _logger.error("athera.provision.failed slug=%s rc=%s", slug, proc.returncode)
            raise UserError(
                "Provisioning %s failed (odoo exited %s). Last output:\n%s"
                % (slug, proc.returncode, tail)
            )

        # Assert the OUTCOME, not the exit code. `odoo -i` on a module that is
        # not in addons_path exits 0 having installed nothing, which is exactly
        # the silently half-provisioned tenant this check exists to catch.
        missing = self._not_installed(slug, module_names)
        if missing:
            raise UserError(
                "odoo exited 0 but these are not installed in %s: %s"
                % (slug, ", ".join(missing))
            )

        # `odoo -d <db> -i ...` leaves admin on Odoo's DEFAULT password. The
        # first version of this method accepted admin_password and then never
        # used it, which produced a tenant that provisioned cleanly, reported
        # success, and could not be logged into -- measured as
        # invalid_credentials from the gateway on the documented password.
        if admin_password:
            self._set_admin_password(slug, admin_password)

        _logger.info("athera.provision.done slug=%s", slug)
        return {"slug": slug, "installed": module_names}

    def _set_admin_password(self, slug, password):
        """Set the new tenant's admin password, through Odoo's own hasher.

        A direct UPDATE on res_users would store the plaintext where a hash
        belongs, and Odoo would then refuse every login -- silently, because a
        password that does not verify is indistinguishable from a wrong one.

        The password reaches the child through its ENVIRONMENT, not argv and not
        stdin. Not argv, because a container's process list is readable by
        anything else in the same namespace. Not stdin, because `odoo shell`
        consumes the whole of stdin as its script, so a password appended after
        the snippet is read as code -- and a `sys.stdin.readline()` inside the
        snippet just gets EOF.
        """
        snippet = (
            "import os\n"
            "u = env['res.users'].search([('login','=','admin')], limit=1)\n"
            "assert u, 'no admin user in the new database'\n"
            "u.write({'password': os.environ['ATHERA_NEW_ADMIN_PW']})\n"
            "env.cr.commit()\n"
            "print('PWSET_OK')\n"
        )
        child_env = dict(os.environ, ATHERA_NEW_ADMIN_PW=password)
        proc = subprocess.run(  # noqa: S603
            [
                "odoo", "shell",
                "-c", os.environ.get("ODOO_RC", "/opt/odoo/conf/odoo.conf"),
                "-d", slug, "--no-http",
            ],
            input=snippet,
            env=child_env,
            capture_output=True, text=True, timeout=300,
        )
        if "PWSET_OK" not in (proc.stdout or ""):
            tail = (proc.stderr or proc.stdout or "")[-1000:]
            raise UserError(
                "Provisioned %s but could not set its admin password:\n%s" % (slug, tail)
            )
        _logger.info("athera.provision.password_set slug=%s", slug)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _check_caller(self):
        """Only the super-admin group may ask for a database.

        Checked here as well as at the orchestrator's HMAC boundary. The
        orchestrator authenticates the CALLER; this authorises the ODOO USER it
        authenticates as, and those are different questions.
        """
        if self.env.su:
            return
        if not self.env.user.has_group(GROUP_SUPER_ADMIN):
            raise AccessDenied()

    @staticmethod
    def _validate_slug(slug):
        slug = (slug or "").strip()
        if not SLUG_RE.match(slug):
            raise UserError(
                "Invalid tenant slug %r. Must match %s -- lowercase, starts with a "
                "letter, no dashes (Postgres replication slot names forbid them)."
                % (slug, SLUG_RE.pattern)
            )
        if slug in RESERVED:
            raise UserError("Tenant slug %r is reserved." % slug)
        return slug

    def _module_list(self, modules):
        if modules is None:
            modules = self.env["ir.config_parameter"].sudo().get_param(
                "athera.provision_modules", ""
            )
        if isinstance(modules, str):
            modules = [m.strip() for m in modules.split(",")]
        return [m for m in (modules or []) if m]

    @staticmethod
    def _install_timeout():
        """Wall-clock ceiling for the child, in seconds.

        Generous on purpose: this module set pulls in roughly three hundred
        dependencies. A ceiling that is too tight turns a slow-but-correct
        install into a half-built database, which is worse than waiting.
        """
        try:
            return int(os.environ.get("ATHERA_PROVISION_TIMEOUT_S", "3600"))
        except ValueError:
            return 3600

    @staticmethod
    def _db_exists(name):
        """Ask Postgres directly.

        Not `odoo.service.db.exp_db_exist`, which consults the dbfilter and
        would answer False for a database that exists but is not currently
        served -- exactly the case where provisioning over it does the most
        damage.
        """
        db = db_connect("postgres")
        with db.cursor() as cr:
            cr.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,))
            return bool(cr.fetchone())

    @staticmethod
    def _not_installed(db_name, module_names):
        db = db_connect(db_name)
        with db.cursor() as cr:
            cr.execute(
                "SELECT name FROM ir_module_module WHERE name IN %s AND state != 'installed'",
                (tuple(module_names),),
            )
            return sorted(r[0] for r in cr.fetchall())
