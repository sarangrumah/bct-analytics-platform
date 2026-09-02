# syntax=docker/dockerfile:1.7
# ---------------------------------------------------------------------------
# ai-gateway — the server behind ATHERA Agent.
#
# This image holds no database credential and no docker socket, only an LLM
# API key. It reads NO database at all. Base image pinned by digest.
# ---------------------------------------------------------------------------
FROM python:3.12-slim@sha256:09f7da3bc104798d0afb40bc08d23ab2da20a76130cec1f2ef170848f5d85217

USER root

SHELL ["/bin/bash", "-eo", "pipefail", "-c"]

# --- OS crypto patch --------------------------------------------------------
# The pinned digest ships openssl 3.5.6-1~deb13u2 with three fixed-status HIGH
# CVEs. `ignore-unfixed` does not cover fixed-status findings and must not.
# Especially here: this process is reachable over the network.
#
# Pinned exactly, in Platform-Infra's form (odoo/Dockerfile:19-36). If a pin
# stops resolving the build MUST fail loudly. Re-check with:
#   docker run --rm -u 0 --entrypoint bash <image> \
#     -c 'apt-get update >/dev/null && apt-cache policy openssl libssl3t64'
ARG OPENSSL_VERSION=3.5.7-1~deb13u2
ARG LIBSSL_VERSION=3.5.7-1~deb13u2
ARG OPENSSL_PROVIDER_LEGACY_VERSION=3.5.7-1~deb13u2

RUN set -eux; \
    apt-get update; \
    # OS crypto only. Deliberately NOT `dist-upgrade`.
    apt-get install -y --no-install-recommends --only-upgrade \
        "openssl=${OPENSSL_VERSION}" \
        "libssl3t64=${LIBSSL_VERSION}" \
        "openssl-provider-legacy=${OPENSSL_PROVIDER_LEGACY_VERSION}"; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/*; \
    openssl version

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app /app/app
COPY asgi.py /app/asgi.py

# Non-root, numeric. Nothing is written to disk at all: the registry is the
# only state, so this runs with a read-only root filesystem.
RUN useradd --system --uid 10006 --no-create-home --shell /usr/sbin/nologin aigateway
USER 10006

EXPOSE 8080
ENTRYPOINT ["uvicorn", "asgi:app", "--host", "0.0.0.0", "--port", "8080", "--no-server-header"]
