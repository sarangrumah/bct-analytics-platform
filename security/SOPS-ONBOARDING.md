# SOPS onboarding — how to add a secret without ever writing a plaintext one

Owner: Security agent. Applies to **every** agent and every engineer on this repository.

The rule this document exists to make easy: **a secret is either encrypted, or it is
outside the repository. There is no third option, and no "just for now".**

---

## The model in one paragraph

Encryption uses [age](https://age-encryption.org) via [SOPS](https://github.com/getsops/sops).
Each person holds an age **key pair**. The **public** half (`age1…`) is a lock: it is
listed in `.sops.yaml`, committed, and is not a secret. The **private** half
(`AGE-SECRET-KEY-1…`) is the key: it lives in your home directory, mode 600, and never
enters the repository, a brief, a chat message or a screenshot. SOPS encrypts each *value*
in a YAML file to every listed recipient and leaves the *keys* readable, so a diff of an
encrypted file still shows which setting changed.

---

## First-time setup (5 minutes, once per machine)

### 1. Install the tools

Already present on the operator host: `sops 3.13.0`, `age-keygen v1.3.1`. Otherwise take
them from the SOPS and age releases and verify the checksum.

### 2. Generate your key

```bash
mkdir -p "$HOME/.config/sops/age"
age-keygen -o "$HOME/.config/sops/age/bct-analytics.txt"
chmod 600 "$HOME/.config/sops/age/bct-analytics.txt"
```

It prints `Public key: age1…`. **That line is what you send.** The file it wrote is what
you never send.

### 3. Tell SOPS where your key is

SOPS looks in the OS config directory by default, which differs per platform. Pick one:

```bash
# Portable and explicit — recommended. Put it in your shell profile.
export SOPS_AGE_KEY_FILE="$HOME/.config/sops/age/bct-analytics.txt"
```

or install it at the platform default so no environment variable is needed:

| Platform | Default location |
|---|---|
| Linux / WSL / macOS | `$HOME/.config/sops/age/keys.txt` |
| Windows | `%AppData%\sops\age\keys.txt` |

> On Windows, `~/.config/sops/age/keys.txt` is **not** consulted — SOPS uses
> `os.UserConfigDir()`, which is `%AppData%`. A key in `~/.config` there is silently
> ignored and you get "identity did not match any of the recipients", which reads like a
> permissions problem and is not one.

A file may hold several identities, one per line; SOPS tries all of them.

### 4. Get yourself added as a recipient

Send **only** the `age1…` public line to the Security agent (via the Lead). Security then:

```bash
# 1. add the recipient under `keys:` in .sops.yaml, with a comment naming the holder
# 2. re-encrypt every existing file to the new recipient set
sops updatekeys .secrets.enc.yaml
# 3. commit .sops.yaml and the re-keyed files together, in ONE commit
```

Until step 2 runs you can encrypt but not decrypt: SOPS encrypts to the recipients in
`.sops.yaml`, and the existing ciphertext was written before you were on the list.

---

## Daily use

### Read a secret

```bash
sops --decrypt .secrets.enc.yaml            # to stdout
sops --decrypt --extract '["odoo"]["ODOO_ADMIN_PASSWD"]' .secrets.enc.yaml
```

### Edit a secret

```bash
sops .secrets.enc.yaml
```

Opens your `$EDITOR` on the **plaintext**, re-encrypts on save. The plaintext exists only
in a temporary file that SOPS removes. Do not `cat > plaintext.yaml` and encrypt afterwards
if you can avoid it — that leaves the plaintext on disk and in your shell history.

### Add a NEW encrypted file

Name it so a creation rule in `.sops.yaml` matches — `*.enc.yaml`, `*.enc.json`,
`*.enc.env` — then:

```bash
sops --encrypt --in-place analytics/warehouse.enc.yaml
```

Check the result before staging. It must be full of `ENC[AES256_GCM,…]`:

```bash
grep -c 'ENC\[AES256_GCM' analytics/warehouse.enc.yaml   # > 0
grep -i 'changeme\|password' analytics/warehouse.enc.yaml  # keys visible, values not
```

### Rotate the data key

```bash
sops --rotate --in-place .secrets.enc.yaml
```

Do this whenever a recipient is removed. Removing someone from `.sops.yaml` stops them
decrypting *future* versions; it does not un-read what they already read, so a departure
means rotating the underlying credentials too, not just the data key.

---

## Where each kind of secret actually belongs

| Kind | Goes where | Never |
|---|---|---|
| Dev credential for local bring-up | generated into `.env` by `make dev-bootstrap` (gitignored) | committed |
| The *shape* and default of a variable | `.env.example`, value literally `changeme` | a real value, even a weak one |
| A value that must be versioned and shared | `*.enc.yaml` via SOPS | a plaintext YAML "temporarily" |
| Per-tenant PDP masking salt (contract 01) | SOPS only, `WAREHOUSE_MASK_SALT_<TENANT>` | a file, a default, a fallback |
| `login-gateway` RS256 private key (contract 02) | mounted by **path** into the container | an env var, the image, or the repo |
| Your age private key | `$HOME/.config/sops/age/…`, mode 600 | anywhere under this repository |

`.env.example` carries the literal string `changeme` and nothing else. This is enforced,
not requested: `.gitleaks.toml` allowlists `changeme` **by name**, so a realistic-looking
placeholder like `SuperSecret123` fails the secret gate while `changeme` passes.

---

## What stops you getting this wrong

| Control | When it fires |
|---|---|
| `forbid-plaintext-secret-files` pre-commit hook | you `git add` a `.env`, `*.pem`, `*.key`, `id_rsa`, or an age key file |
| `detect-private-key` pre-commit hook | a PEM private key block appears in any staged file |
| `gitleaks` pre-commit hook | a secret is in your **staged diff** |
| `secrets` CI job | a secret is anywhere in the **working tree or full git history** |
| `bct-age-secret-key` gitleaks rule | an `AGE-SECRET-KEY-1…` reaches a tracked file |

If you are ever unsure whether something counts as a secret: encrypt it. The cost of
encrypting a non-secret is one command. The cost of committing a real one is rotation
across every environment that uses it.

---

## If a secret has already been committed

1. **Rotate the credential first.** Assume it is compromised the moment it hits the object
   database — this repository has no remote today, but the next thing that happens to it
   is a push.
2. Then remove it from history (`git filter-repo`) — after rotation, not instead of it.
3. Tell the Lead. A committed secret is a gate item, not a cleanup task.

Rewriting history without rotating is the common mistake: it makes the scanner quiet while
the credential is still valid.

---

## Current state of this repository — stated plainly

- **One recipient** is configured: the project development key generated on the operator
  host on 2026-08-31, public `age14mh5ttdkqd9sah0pnpdx3mx385pdsy7ukjh663th8zw3hmyh2pzsytq7ws`.
- **`.secrets.enc.yaml` holds only `changeme` values.** It exists to prove the round-trip
  works and to fix the variable names, not to carry anything real. Its round-trip
  (`sops --decrypt`) is **verified**, not assumed.
- **No CI/CD recipient exists yet.** That is deliberate: there is no remote and no
  deployment target, so no automation needs decryption rights. Phase 5 adds one, and it
  should be a *separate* key so a CI compromise does not expose a human's other projects.
