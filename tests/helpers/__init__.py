"""Shared helpers for the odoo19-bct integration suite.

Design constraints, all of them deliberate:

* **Standard library only, plus pytest.** The host has no ``psycopg2`` and no ``requests``. Every
  database call therefore goes through ``docker exec ... psql`` and every HTTP call through
  ``urllib``. That is not a workaround: it means these tests exercise the containers exactly as an
  operator would, rather than a Python driver's view of them.
* **Every compose/docker call is scoped to project ``odoo19-bct``.** This host also runs
  ``odoo19-platform-*``, ``odoo19-analytics-*`` and ``smart-warga-postgres-1``. Nothing in this
  package may name a container outside this project; :func:`assert_project_scoped` enforces it.
* **Identity is asserted, never assumed.** See :func:`role_identity`.
"""
