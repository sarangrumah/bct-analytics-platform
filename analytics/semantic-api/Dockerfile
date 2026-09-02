# syntax=docker/dockerfile:1.7
# ---------------------------------------------------------------------------
# Semantic API — the single place each metric is defined. Read-only.
#
# Base image pinned by digest, per the Backend brief. A digest is a security
# control, not a tunable.
# ---------------------------------------------------------------------------
FROM python:3.12-slim@sha256:09f7da3bc104798d0afb40bc08d23ab2da20a76130cec1f2ef170848f5d85217

# Everything below runs as root; the final USER instruction drops to uid 10003.
USER root

SHELL ["/bin/bash", "-eo", "pipefail", "-c"]

# --- OS crypto patch --------------------------------------------------------
# The pinned digest ships openssl 3.5.6-1~deb13u2, which carries three HIGH
# CVEs with fixes available (CVE-2026-14456 and two others). Those are
# fixed-status findings, so `ignore-unfixed` does not and must not cover them —
# and this is the one process in the platform that handles unmasked personal
# data before it is ever hashed, which is precisely what the gate exists to
# protect.
#
# Pinned exactly, in Platform-Infra's form (odoo/Dockerfile:19-36). If a pin
# stops resolving the build MUST fail loudly, so the upgrade becomes a reviewed
# change rather than silent drift. Re-check with:
#   docker run --rm -u 0 --entrypoint bash <image> \
#     -c 'apt-get update >/dev/null && apt-cache policy openssl libssl3t64'
#
# Pinning to the *fixed* version means DL3008 is satisfied outright, so no
# hadolint ignore is needed here — the pragma is only required when a pin would
# freeze a known-vulnerable package, which is the opposite of this case.
ARG OPENSSL_VERSION=3.5.7-1~deb13u2
ARG LIBSSL_VERSION=3.5.7-1~deb13u2
ARG OPENSSL_PROVIDER_LEGACY_VERSION=3.5.7-1~deb13u2

RUN set -eux; \
    apt-get update; \
    # OS crypto only. Deliberately NOT `dist-upgrade`: a blanket upgrade would
    # move packages that the pinned digest was tested against.
    apt-get install -y --no-install-recommends --only-upgrade \
        "openssl=${OPENSSL_VERSION}" \
        "libssl3t64=${LIBSSL_VERSION}" \
        "openssl-provider-legacy=${OPENSSL_PROVIDER_LEGACY_VERSION}"; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/*; \
    # Assert the patch landed: fail the build, not the container.
    openssl version

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# psycopg2-binary ships its own libpq, so no build toolchain and no libpq-dev is
# installed here — a smaller attack surface and nothing extra to keep patched.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app /app/app
COPY asgi.py /app/asgi.py
COPY metrics /app/metrics

# Non-root, numeric. The loader needs no filesystem writes at all: its state
# holds no local state at all, so this
# image runs happily with a read-only root filesystem.
RUN useradd --system --uid 10003 --no-create-home --shell /usr/sbin/nologin semantic
USER 10003

EXPOSE 8080
ENTRYPOINT ["uvicorn", "asgi:app", "--host", "0.0.0.0", "--port", "8080", "--no-server-header"]
