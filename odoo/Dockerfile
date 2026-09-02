# syntax=docker/dockerfile:1.7
# ---------------------------------------------------------------------------
# Odoo 19 CE — hardened runtime image.
#
# Pinned by digest, not by tag. `odoo:19.0` is a moving target; this digest is
# the one recorded in docs/agents/PLAN.md and is non-negotiable. Changing it is
# a reviewed change, not a rebuild side effect.
# ---------------------------------------------------------------------------
FROM odoo:19.0@sha256:f99ffac95cb39a0924622ea4118481c95651d9c84187e5b30a21c2cc4419c7dd

# Everything below runs as root; the final USER instruction drops back to odoo.
USER root

# The base image sets `-x` (command tracing) in its SHELL. Drop it, keep -e.
SHELL ["/bin/bash", "-eo", "pipefail", "-c"]

# --- Package versions -------------------------------------------------------
# Pinned exactly. If a pin stops resolving the build MUST fail loudly, so the
# upgrade becomes a reviewed change rather than silent drift. Re-check with:
#   docker run --rm -u 0 --entrypoint bash <image> \
#     -c 'apt-get update >/dev/null && apt-cache policy openssl libssl3t64'
ARG OPENSSL_VERSION=3.0.13-0ubuntu3.15
ARG LIBSSL_VERSION=3.0.13-0ubuntu3.15
# postgresql-client already ships in the base image (psql 18.6, pgdg build).
# Requesting the exact version makes apt *assert* it instead of us silently
# inheriting whatever a future digest happens to carry.
ARG POSTGRESQL_CLIENT_VERSION=18+293.pgdg24.04+1

RUN set -eux; \
    apt-get update; \
    # OS crypto only. Deliberately NOT `dist-upgrade`: a blanket upgrade would
    # move packages that the pinned digest was tested against.
    apt-get install -y --no-install-recommends --only-upgrade \
        "openssl=${OPENSSL_VERSION}" \
        "libssl3t64=${LIBSSL_VERSION}"; \
    # scripts/init-db.sh, tenant-provision.sh and tenant-backup.sh need these.
    apt-get install -y --no-install-recommends \
        "postgresql-client=${POSTGRESQL_CLIENT_VERSION}"; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/*; \
    # Assertions: fail the build, not the container, if a tool went missing.
    command -v psql; \
    command -v pg_dump; \
    command -v curl; \
    openssl version

# --- Python dependencies ----------------------------------------------------
# This image shipped zero extra Python packages until ADR 0002 brought in the
# odoo-platform addon suite. Eight of those modules declare external_dependencies
# the base image does not carry, so the rule the original comment set out is now
# exercised rather than repealed: every package is pinned with `==` AND a hash.
#
# --require-hashes makes an unpinned or unhashed line a build error.
# --no-deps means pip resolves nothing at build time; requirements.txt is the
# complete transitive closure, computed once and committed. A dependency that
# appears from nowhere is therefore a build failure, not a silent addition.
COPY requirements.txt /tmp/requirements.txt
# The final python3 -c asserts the imports the addons actually perform, rather
# than trusting that pip exited 0.
RUN set -eux; \
    pip install --no-cache-dir --require-hashes --no-deps \
        --break-system-packages -r /tmp/requirements.txt; \
    rm -f /tmp/requirements.txt; \
    python3 -c "import xmlschema, jwt, httpx, croniter, pymupdf, docx, pptx, oracledb"

# --- Config template --------------------------------------------------------
# `admin_passwd` is a FileOnlyOption in odoo/tools/config.py: no CLI flag, no
# env var. So odoo.conf is rendered from a template at container start. The
# template is root-owned and world-read-only; only the rendered output
# directory is writable by the runtime user, and the rendered file is 0640
# because it carries db_password and admin_passwd.
RUN set -eux; \
    mkdir -p /opt/odoo/conf; \
    chown odoo:odoo /opt/odoo/conf; \
    chmod 0750 /opt/odoo/conf

COPY --chown=root:root --chmod=0444 odoo.conf            /opt/odoo/odoo.conf.template
COPY --chown=root:root --chmod=0555 render-config.py     /usr/local/bin/render-config.py
COPY --chown=root:root --chmod=0555 docker-entrypoint.sh /usr/local/bin/bct-entrypoint.sh

# --- Filesystem hardening ---------------------------------------------------
# Strip every setuid/setgid bit. Nothing in an Odoo container legitimately
# needs to escalate: no su, no mount, no passwd. Acceptance criterion 7 asserts
# `find / -xdev -perm /6000 -type f` prints nothing.
# Runs LAST so it also covers anything apt pulled in above.
RUN set -eux; \
    find / -xdev -perm /6000 -type f -exec chmod -s '{}' + 2>/dev/null || true; \
    # Prove it at build time so a regression fails the build, not just the test.
    test -z "$(find / -xdev -perm /6000 -type f 2>/dev/null)"; \
    # Volume mount points: fix ownership at build time so a fresh named volume
    # inherits the right uid from the image.
    mkdir -p /var/lib/odoo /mnt/extra-addons; \
    chown -R odoo:odoo /var/lib/odoo /mnt/extra-addons

# --- Runtime ----------------------------------------------------------------
USER odoo

ENV ODOO_RC=/opt/odoo/conf/odoo.conf \
    ODOO_CONF_TEMPLATE=/opt/odoo/odoo.conf.template \
    ODOO_HTTP_PORT=8069 \
    ODOO_LONGPOLLING_PORT=8072 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8069 8072

# A TCP-open check would pass while Odoo is deadlocked or has lost Postgres.
# /web/health is a real Odoo controller (addons/web/controllers/home.py); with
# ?db_server_status=1 it opens a live libpq cursor and returns HTTP 500 when the
# database server is unreachable. `curl -f` turns that 500 into a failure.
# So this exercises: WSGI worker alive -> Odoo routing -> psycopg2 -> Postgres.
#
# DL3025 (shell form) is suppressed for THIS LINE ONLY, and deliberately not in
# .hadolint.yaml. Shell form is required here: JSON exec form does not expand
# ${ODOO_HTTP_PORT}, so the check would request a literal '${ODOO_HTTP_PORT}'
# and fail forever. The rule stays on globally because shell form on a real
# ENTRYPOINT/CMD means PID 1 is /bin/sh and never receives SIGTERM, so the
# container takes the full kill timeout and Odoo shuts down uncleanly. The
# ENTRYPOINT and CMD below are exec form for exactly that reason.
# hadolint ignore=DL3025
HEALTHCHECK --interval=15s --timeout=10s --start-period=90s --retries=6 \
    CMD curl -fsS -o /dev/null "http://127.0.0.1:${ODOO_HTTP_PORT}/web/health?db_server_status=1" || exit 1

ENTRYPOINT ["/usr/local/bin/bct-entrypoint.sh"]
CMD ["odoo"]
