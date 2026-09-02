# syntax=docker/dockerfile:1.7
# ---------------------------------------------------------------------------
# dbt-core + dbt-postgres — the transformation runner for the analytics
# warehouse.
#
# Base pinned by DIGEST, from docs/agents/PLAN.md. A digest is a security
# control, not a tunable; `python:3.12-slim` is a moving tag and floating it
# would let an unreviewed base into the stack between two identical builds.
#
# This image is registered in security/scan-targets.yml as `analytics/dbt`
# with context `analytics/dbt`. Every COPY below is relative to that
# directory, so the declared context is the real one.
# ---------------------------------------------------------------------------
FROM python:3.12-slim@sha256:09f7da3bc104798d0afb40bc08d23ab2da20a76130cec1f2ef170848f5d85217

# Everything below runs as root; the final USER drops to a numeric non-root uid.
USER root

SHELL ["/bin/bash", "-eo", "pipefail", "-c"]

# --- OS security patches ---------------------------------------------------
# The pinned base ships openssl/libssl3t64 3.5.6-1~deb13u2, which carries
# fixed-status HIGH CVEs. `ignore-unfixed` does not apply to a fixed CVE, so
# the container-scan gate blocks the image — correctly — until it is patched.
#
# Pinned EXACTLY, following odoo/Dockerfile:30-36. If a pin stops resolving the
# build must fail loudly so the upgrade is a reviewed change rather than silent
# drift. Deliberately NOT `dist-upgrade`: a blanket upgrade moves packages the
# pinned digest was never tested against.
#
# Re-check the target versions with:
#   docker run --rm --entrypoint sh python:3.12-slim@sha256:09f7da3b... \
#     -c 'apt-get update >/dev/null && apt-cache policy openssl libssl3t64'
ARG OPENSSL_VERSION=3.5.7-1~deb13u2
ARG LIBSSL_VERSION=3.5.7-1~deb13u2

# hadolint ignore=DL3008
# DL3008 asks for pinned versions and these ARE pinned; the pragma is scoped to
# this instruction only, never globally, because "pin your versions" is exactly
# the rule that would otherwise freeze a known-vulnerable package in place.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends --only-upgrade \
        "openssl=${OPENSSL_VERSION}" \
        "libssl3t64=${LIBSSL_VERSION}"; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/*; \
    openssl version

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DBT_PROFILES_DIR=/dbt \
    # dbt writes ~/.dbt/.user.yml for anonymous usage stats on first run. With
    # a non-root uid and no home directory that is a permission error on every
    # invocation, so the feature is turned off rather than worked around.
    DO_NOT_TRACK=1

WORKDIR /dbt

# --- Python dependencies ---------------------------------------------------
# requirements.txt pins the FULL transitive closure with exact `==` versions,
# not just the top-level requirement — see its header for why and for how it
# was generated. psycopg2-binary ships its own libpq, so no build toolchain and
# no libpq-dev is installed here: a smaller attack surface and nothing extra to
# keep patched.
COPY requirements.txt /tmp/requirements.txt
RUN set -eux; \
    pip install --no-cache-dir -r /tmp/requirements.txt; \
    rm -f /tmp/requirements.txt; \
    dbt --version

# --- NO dbt deps -----------------------------------------------------------
# This project deliberately declares NO dbt packages, so there is no
# `dbt deps` step, no packages.yml, and no runtime fetch from hub.getdbt.com.
#
# The only things it would have pulled from dbt_utils are generate_surrogate_key
# and date_spine, both of which are a handful of lines and both of which are in
# macros/. That trade — twenty lines of local SQL against a network dependency
# resolved at run time, inside a container that otherwise talks to nothing but
# the warehouse — is worth making. It also means the project directory can be
# mounted read-write over this image without a package directory going stale.

# --- Non-root --------------------------------------------------------------
# Numeric uid, no shell, no home. dbt writes target/ and logs/ into the mounted
# project directory, which is why that mount is read-write and this uid must be
# able to write it.
RUN set -eux; \
    useradd --system --uid 10002 --no-create-home --shell /usr/sbin/nologin dbt; \
    mkdir -p /dbt; \
    chown 10002:10002 /dbt
USER 10002

ENTRYPOINT ["dbt"]
CMD ["--version"]
