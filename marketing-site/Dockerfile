# syntax=docker/dockerfile:1

# insight-site - the BCT analytics dashboard.
#
# Multi-stage on the digest pinned in docs/agents/PLAN.md. A digest, not a tag: node:22-alpine moves
# and a tag would silently change the base of a signed image between two builds of the same commit.
#
# The runtime stage carries Next's standalone output and nothing else - no npm, no build toolchain,
# no node_modules tree the application does not import. There is no database driver in the image
# because there is none in the dependency tree: the site reaches the warehouse only through the
# semantic API, and holds no credential that reaches Postgres.

FROM node:22-alpine@sha256:c610fcdfb1d5b4740dd70c284ed3cb16bb857e0f7166196e36a5501df7a3aa32 AS deps
WORKDIR /app
COPY package.json package-lock.json ./
# `npm ci` and not `npm install`: the lockfile is the input, so the image cannot drift from the tree
# that was scanned. Cache mount keeps rebuilds cheap without baking the cache into a layer.
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund

FROM node:22-alpine@sha256:c610fcdfb1d5b4740dd70c284ed3cb16bb857e0f7166196e36a5501df7a3aa32 AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:22-alpine@sha256:c610fcdfb1d5b4740dd70c284ed3cb16bb857e0f7166196e36a5501df7a3aa32 AS runtime
WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

# OS crypto, pinned exactly. Same shape as odoo/Dockerfile: an exact version, upgrade-only, with the
# command for re-deriving it written down so the next person does not have to guess. Alpine, so apk
# rather than apt, but the properties that matter carry over unchanged.
#
# Pinned rather than a bare `apk upgrade` for the reason Backend pinned ==9.0.3 over >=: a floating
# upgrade makes the scan's verdict depend on whatever the mirror happens to serve that day, and a
# green run that cannot be reproduced tomorrow is not evidence. If a pin stops resolving the build
# MUST fail loudly, so moving it becomes a reviewed change rather than silent drift. `apk add` with
# an exact constraint IS that assertion - an unsatisfiable pin fails the build with "unable to
# select packages" - so no separate verification step is needed here. The installed version is
# checked in the built image instead, which is the artefact that ships.
#
# Re-check with:
#   docker run --rm -u 0 --entrypoint sh <image> \
#     -c 'apk update >/dev/null && apk policy libcrypto3 libssl3'
#
# Closes CVE-2026-14456 (openssl: denial of service via unbounded memory), the last two findings in
# container-scan. Applied to the RUNTIME stage only: the deps and build stages never ship, so
# upgrading them would cost a layer and prove nothing.
ARG LIBCRYPTO3_VERSION=3.5.8-r0
ARG LIBSSL3_VERSION=3.5.8-r0

RUN set -eux; \
    apk add --no-cache --upgrade \
        "libcrypto3=${LIBCRYPTO3_VERSION}" \
        "libssl3=${LIBSSL3_VERSION}"

# A dedicated uid, distinct from the gateway's 10002 and the semantic API's 10003, so a process
# listing and an audit log can tell the three apart.
RUN addgroup -g 10007 -S site && adduser -u 10007 -S -G site site

COPY --from=build --chown=root:root /app/.next/standalone ./
COPY --from=build --chown=root:root /app/.next/static ./.next/static
COPY --from=build --chown=root:root /app/public ./public

# Remove the package managers from the runtime stage.
#
# This is the change that matters most, and it is not a version chase. npm and yarn are not
# application dependencies and are not in package-lock.json; they arrive inside the base image, and
# they bring their own vendored trees with them - tar, pacote, sigstore, ip-address,
# brace-expansion, picomatch. container-scan found 13 findings in node-pkg from that source alone,
# 12 HIGH and 1 CRITICAL, none of which any amount of work on this project's dependencies could
# reach. `npm audit` cannot see them either: it reads the declared tree, and these are not declared.
#
# A Next standalone runtime needs `node`. A package manager sitting beside the application in
# production is also an arbitrary-code-download tool sitting beside the application in production,
# so removing it is attack-surface reduction that stays true across future base images rather than
# a fix that expires with the next advisory.
#
# corepack goes too, for the same reason: it is a package-manager shim and nothing here invokes one.
# `docker-entrypoint.sh` is kept - it is the base image's ENTRYPOINT and simply execs the CMD.
#
# Verified rather than assumed: with all of this gone the standalone server starts and all five
# views return 200. The HEALTHCHECK below fails the container if that ever stops being true.
RUN rm -rf /usr/local/lib/node_modules/npm \
           /usr/local/lib/node_modules/corepack \
           /opt/yarn-v1.22.22 \
    && rm -f /usr/local/bin/npm \
             /usr/local/bin/npx \
             /usr/local/bin/yarn \
             /usr/local/bin/yarnpkg \
             /usr/local/bin/corepack

# Drop sharp and its libvips binaries too.
#
# No longer a CVE mitigation - the `overrides` block in package.json now resolves sharp to 0.35.4
# and postcss to 8.5.26, so `npm audit --audit-level=high` is clean at source and the nested
# postcss 8.4.31 that used to ship under next/ is gone. This deletion is plain surface reduction:
# the image optimizer is off in next.config.ts, this dashboard renders no images (public/ is empty,
# nothing imports next/image), and native libvips binaries that nothing can invoke are still native
# binaries in the image.
RUN rm -rf ./node_modules/sharp ./node_modules/@img

# Owned by root and readable by the runtime user, never writable by it: the process has no reason to
# modify its own code, and a read-only root filesystem in compose makes that structural rather than
# a matter of file modes alone.
USER 10007:10007

EXPOSE 3000

# JSON (exec) notation, not shell form: hadolint DL3025. Shell form wraps the probe in /bin/sh,
# so a hung probe leaves an extra process behind on every interval and the timeout signal goes to
# the shell rather than to node.
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
  CMD ["node", "-e", "fetch('http://127.0.0.1:' + (process.env.PORT || 3000) + '/healthz').then(r => process.exit(r.ok ? 0 : 1)).catch(() => process.exit(1))"]

CMD ["node", "server.js"]
