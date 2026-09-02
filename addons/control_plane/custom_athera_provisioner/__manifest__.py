# -*- coding: utf-8 -*-
{
    "name": "ATHERA Tenant Provisioner",
    "summary": "Create a tenant database from inside Odoo, as a queue_job",
    "description": """
ATHERA Tenant Provisioner
=========================

The one step of tenant provisioning that cannot be done by the
tenant-orchestrator's own Postgres role: building an Odoo database.

WHY THIS EXISTS AT ALL, rather than the orchestrator doing it.

``odoo.service.db.exp_create_database`` is decorated with
``@check_db_management_enabled``, which raises ``AccessDenied`` whenever
``list_db`` is False. It is False here and must stay that way -- ``odoo.conf``
puts it plainly: "Multi-tenant by construction means databases come from
scripts/tenant-provision.sh, never from the web UI." So there is no RPC route
to create a database, and the alternative the upstream platform repo uses is to
give the orchestrator ``/var/run/docker.sock`` and shell out to
``docker exec odoo odoo -d <db> -i <modules>``.

That trade was rejected. The orchestrator is a network-facing HTTP service, and
the docker socket is root on the host: compromising it would hand over every
tenant's data at once, in a stack whose every other service runs
``cap_drop: ALL``, ``read_only`` and ``no-new-privileges``.

The guard is on the RPC SURFACE, not on the function. Odoo code running inside
Odoo may call the internals directly, and it already holds the database
credentials it would need. So the privilege stays where it already was.

WHY A JOB AND NOT A DIRECT CALL.

Installing the init module set pulls in roughly three hundred dependencies and
takes minutes. An HTTP worker is killed at ``limit_time_real`` (240s here), so
the same code called over RPC would be reaped part-way through and leave a
half-built database. ``queue_job`` is already in ``server_wide_modules``, and
its runner is not an HTTP worker, so the job simply takes as long as it takes.
""",
    "author": "ATHERA",
    "category": "Custom Platform/Operations",
    "version": "19.0.1.0.0",
    "license": "LGPL-3",
    "depends": [
        "custom_super_admin",
        "queue_job",
    ],
    "data": [
        "data/queue_job_channel.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
