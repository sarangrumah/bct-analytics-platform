---
name: athera-service-add
description: Add a new containerised service to the ATHERA platform with every guardrail satisfied — compose stack, env-driven URLs, CI scan registration, health checks, tests. Use when adding any new service, Dockerfile or Node project to this repo.
---

# Adding a service

This repo has guardrails that fail the build rather than warn. Satisfy them in the **same commit** as the service, or CI goes red on someone else's change.

## 0. Which stack does it belong to?

| If it… | Stack |
|---|---|
| serves one product only | that product's file |
| is shared by all products (identity, routing, control plane) | `compose/platform.yml` |
| is part of the dashboard path | `compose/insight.yml` |
| watches everything | `compose/observability.yml` |

Getting this wrong is not cosmetic: the whole point of the split is that a product can move to its own VPS, and a shared service living inside a product stack silently prevents that. `login-gateway` is the worked example — it is the Login node for all three products, so it is in `platform`, not in `insight`.

## 1. Register it for scanning — do this first

`security/scan-targets.yml` is a **hard gate**: an unregistered `Dockerfile` or `package.json` anywhere in the tree fails CI outright, and a registered path that does not exist fails too.

```yaml
  - name: my-service
    dockerfile: my-service/Dockerfile
    context: my-service
    owner: Backend
    wave: 3
    status: present        # `pending` only while the Dockerfile genuinely does not exist yet
```

Node projects get a second entry under the Node section (`path:` instead of `dockerfile:`/`context:`).

```bash
python3 security/scan_targets.py --check    # must print SCAN_COVERAGE_OK
```

## 2. Compose service

Copy the shape of an existing one. Non-negotiables:

```yaml
  my-service:
    build: { context: ../my-service, dockerfile: Dockerfile }
    image: ${COMPOSE_PROJECT_NAME:-odoo19-bct}-my-service:local
    container_name: ${COMPOSE_PROJECT_NAME:-odoo19-bct}-my-service
    <<: [*restart-policy, *hardening]      # no-new-privileges + cap_drop ALL
    logging: *default-logging
    read_only: true                        # add tmpfs only for what genuinely needs it
    mem_limit: 512m
    ports:
      - "${BIND_ADDRESS:-127.0.0.1}:${MY_SERVICE_HOST_PORT:-38xxx}:8080"
    healthcheck: { ... }
    networks: [bct]
```

- **Paths are `../`.** The compose files live in `compose/`, so a path relative to the file resolves one level up to the repo root.
- **`127.0.0.1` only.** This host runs three other stacks; `0.0.0.0` exposes them all to the LAN together.
- **Pin images by digest**, inline, never from `.env`. A digest is a security control, not a tunable. Get the real one — do not invent it:
  ```bash
  docker pull image:tag && docker inspect --format '{{index .RepoDigests 0}}' image:tag
  ```
- **A build target is not optional.** `semantic-api` and `cdc` were run by scripts and built by nothing for months; a fresh clone could not start them.

## 3. URLs come from env, never from the image

```yaml
      MY_SERVICE_UPSTREAM_URL: ${MY_SERVICE_UPSTREAM_URL:-http://other-service:8080}
```

Use the **compose service name** as the default, not the container name. Both resolve on the shared network, but only the service name survives being moved to another host with an `.env` change — which is the entire VPS-split mechanism. Add the variable to `.env.example` too.

If it talks to Odoo, the URL must be a **hostname whose first label is the tenant** (`http://bct.athera.localhost:8069`), and `compose/odoo.yml` needs the matching network alias. `dbfilter` is `^%d$` and applies to JSON-RPC as much as to a browser.

## 4. Secrets

- `.env.example` gets the variable with the literal value `changeme`. `make scan-secret` enforces that and gitleaks allowlists that exact string.
- `.env` gets a real generated value.
- Never bake a key into an image. Mount it read-only, as `login-gateway` does with `/run/secrets`.
- Never echo a credential. `\gexec` in psql prints the statement it runs — wrap it in `\o /dev/null` if that statement contains a password.

## 5. Makefile

Add `up-<thing>` next to its siblings and check the `RESERVED` block at the bottom first — make takes the **last** definition of a duplicated target, silently.

## 6. Tests

Add the container to `REQUIRED` in `tests/test_00_environment.py` if it must always be running. Leave it out if it only runs on demand — `cdc` is out for exactly that reason, and the comment there says so.

Add the file to the list in `tests/test_12_clone_install.py` if a fresh clone cannot start the stack without it. That test clones the branch, so **it fails until the file is committed** — an expected failure while work is in progress, not a defect.

## 7. Verify

```bash
python3 security/scan_targets.py --check
make config          # every stack
make up && make ps
make verify
make test
```
