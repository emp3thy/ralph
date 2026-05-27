# ROSA Packaging Plan (Plan 12 of 13)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. This is a devops-flavoured plan — the TDD discipline is adapted: build the artifact, drive a deterministic smoke command against it, assert exit 0 / expected output. Every artifact (Dockerfile, manifest, CI workflow) has a corresponding verification step.

**Goal:** Package `ralph-executor` for deployment onto ROSA (Red Hat OpenShift on AWS), supporting BOTH operational models the spec admits — a long-running k8s Deployment that loops continuously over a service repo, and a Job-based Coder workspaces "task pod" that handles a single PBI per pod invocation — AND both git-host backends (GitHub, Azure DevOps) via a `RALPH_GIT_HOST` Docker build arg. Produce a reproducible container image (multi-stage Dockerfile), the k8s manifests for both modes, a non-secret ConfigMap, a template Secret manifest, a minimal RBAC scaffold, build/push scripts, a pre-deploy CI gate that runs `ralph-doctor` against the just-built image, and a runbook (`docs/deployment.md`) that tells an operator how to choose a mode/host and how to deploy it. The image's baked `.claude/settings.json` enables `--dangerously-skip-permissions` AND explicitly allows every tool Ralph needs (per Spec "Local vs ROSA differences"). The pre-deploy gate is non-optional: Spec section "ralph-doctor checks" requires it, and the gate is host-aware — `ralph-doctor` probes whichever host the image was built for, driven by the image's baked `RALPH_GIT_HOST` env.

**Architecture:** Multi-stage Dockerfile on `python:3.12-slim-bookworm`. A `builder` stage installs `uv`, syncs the locked Python dependencies from `pyproject.toml` / `uv.lock`, and builds the `ralph_executor` wheel. A `runtime` stage installs Node.js (for `@anthropic-ai/claude-code`), copies the virtualenv and wheel from `builder`, installs `claude-code` via `npm`, copies the host-agnostic skills (`ralph-add`, `ralph-status`, `ralph-cancel`, `ralph-promote`, `ralph-triage`, `ralph-doctor`) into `/opt/ralph/skills/`, then — driven by the **build arg `RALPH_GIT_HOST`** — copies the chosen host's `pr-${RALPH_GIT_HOST}/` and `workitem-fetch-${RALPH_GIT_HOST}/` skill directories TWICE: once into `/opt/ralph/skills/pr-${RALPH_GIT_HOST}/` (auditable source-of-truth) AND once into the canonical Claude skills location `/root/.claude/skills/pr/` and `/root/.claude/skills/workitem-fetch/` (so Claude finds them without any runtime staging). The Dockerfile sets `ENV RALPH_GIT_HOST=${RALPH_GIT_HOST}` so `ralph-doctor` and the executor see it. The image is intentionally agnostic of operational mode — the k8s artifact selects mode — but is NOT host-agnostic: each build produces a single-host image, tagged accordingly (e.g. `ralph:0.1.0-github`, `ralph:0.1.0-ado`). `manifests/ralph-deployment.yaml` is the long-running mode; `manifests/ralph-job.yaml` is the Coder task-pod mode; both reference the image via a `__IMAGE__` placeholder that the operator (or CI) substitutes with the host-matching tag. `manifests/ralph-configmap.yaml` and `manifests/ralph-secrets.template.yaml` carry env that differs per host — both hosts' env vars are listed with comments indicating which to populate per deployment. `scripts/build_image.sh` takes a REQUIRED `--host github|ado` argument and passes it through as a `--build-arg`, producing host-tagged images. The runbook `docs/deployment.md` is split into a Phase 1 (GitHub / "at home") path and a Phase 2 (ADO / "at work") path so an operator picks the right branch immediately.

**Tech Stack:** Docker (BuildKit), Python 3.12, `uv`, Node.js 22 LTS (for Claude Code CLI), `@anthropic-ai/claude-code` (npm), `kubectl` 1.30+ (or `oc` for ROSA), Kubernetes 1.30+, ROSA + Coder workspaces, GitHub Actions (primary CI path), Azure Pipelines (documented alternative), `shellcheck` (for the bash scripts).

---

## Phases

This plan delivers two host-specific images from one Dockerfile + one build script. **Both phases use the same Dockerfile, the same build script, the same manifests — only the `RALPH_GIT_HOST` build arg differs.**

- **Phase 1 — GitHub-targeted image** (`ralph-executor:<ver>-github`). This is the home / dogfooding image. It is buildable IMMEDIATELY once this plan's tasks complete, because the Phase 1 skills (`pr-github/`, `workitem-fetch-github/`) are delivered by Plans 3 and 5's Phase 1 work. Use this image to run Ralph against real GitHub repos from home.
- **Phase 2 — ADO-targeted image** (`ralph-executor:<ver>-ado`). This is the work / production image. It requires Plans 2/3/5's Phase 2 work to be complete first (the `pr-ado/` and `workitem-fetch-ado/` skill directories must exist on disk). Until those Phase 2 skills land, `bash scripts/build_image.sh --host ado` will fail at the COPY step in the Dockerfile — and that's the correct failure mode (don't silently build a broken image).

Phase 1 work is the immediate value of this plan; Phase 2 is unblocked the moment Plans 2/3/5 finish their Phase 2 slices.

**Runtime-staging alternative.** Plan 7's `host_select.py` (referenced in the orchestrator's "Host selection architecture" section) supports the inverse approach: ship a single host-agnostic image carrying BOTH `pr-github/` AND `pr-ado/`, then at pod startup `host_select.py` reads `RALPH_GIT_HOST` and symlinks/copies the chosen skills into `~/.claude/skills/pr/`. That model is documented at the end of this plan and remains supported by Plan 7. The DEFAULT model is the build-time approach because (a) pod startup is simpler with no staging step, (b) the image manifest documents exactly one host, removing a runtime configuration knob, and (c) the auth-env-var contract becomes a build-time check (the Dockerfile can fail fast if the wrong host's auth template is missing). The runtime-staging model is a fallback for teams that want a single image to deploy to both environments.

---

## File Structure

| Path | Responsibility |
|---|---|
| `Dockerfile` | Multi-stage image build. Accepts `ARG RALPH_GIT_HOST` (default empty — must be set explicitly at build time). Builder installs `uv` and Python deps; runtime image carries the venv, the `ralph_executor` wheel, the host-agnostic `skills/` tree, the chosen host's `pr-${RALPH_GIT_HOST}` + `workitem-fetch-${RALPH_GIT_HOST}` (copied to BOTH `/opt/ralph/skills/` and `/root/.claude/skills/pr/` + `/root/.claude/skills/workitem-fetch/`), the baked `.claude/settings.json`, Node.js + Claude Code CLI, and a non-root `ralph` user. Sets `ENV RALPH_GIT_HOST=${RALPH_GIT_HOST}`. `ENTRYPOINT ["ralph-executor"]`. |
| `.dockerignore` | Excludes everything that is not needed in the build context: `.git/`, `.venv/`, `dist/`, `build/`, `__pycache__/`, `.pytest_cache/`, `.ruff_cache/`, `.mypy_cache/`, `node_modules/`, `tests/`, `docs/`, `samples/`, IDE files, OS junk. Keeps the build context small and deterministic. |
| `manifests/ralph-deployment.yaml` | k8s Deployment for long-running pod mode. Comment header notes the image tag MUST match the deployment's intended host. Container env documents that `RALPH_GIT_HOST` is set by the image's ENV directive (no override needed). `replicas: 1`. `securityContext` non-root, `runAsUser: 10001`, `readOnlyRootFilesystem: true`, `allowPrivilegeEscalation: false`. CPU/memory requests + limits. `readinessProbe` + `livenessProbe` invoke `ralph-executor health` (Plan 7 to expose; documented assumption). Mounts `ralph-config` ConfigMap and `ralph-secrets` Secret as env. `serviceAccountName: ralph`. |
| `manifests/ralph-job.yaml` | k8s Job for Coder task-pod mode. Same host-tag comment header. `restartPolicy: Never`. `backoffLimit: 0`. `ttlSecondsAfterFinished: 3600`. `RALPH_RUN_ONCE=true` env asserted. Same `securityContext`, ConfigMap, Secret, and ServiceAccount as the Deployment. |
| `manifests/ralph-rbac.yaml` | `ServiceAccount: ralph` + an opt-in `Role` and `RoleBinding` granting `get` / `list` on `configmaps`, `secrets` (scoped to `ralph-config` / `ralph-secrets` only). IAM via IRSA documented in the runbook. Host-agnostic (RBAC is the same for github and ado). |
| `manifests/ralph-secrets.template.yaml` | TEMPLATE Secret manifest. `metadata.name: ralph-secrets`. Covers BOTH host paths: `GH_TOKEN`+`GH_OWNER` (for github deployments) AND `ADO_PAT`+`ADO_ORG_URL`+`ADO_PROJECT` (for ado deployments), plus the shared `ANTHROPIC_API_KEY`. Each block is commented with which deployments need it. Sentinel values prefix `__REPLACE_WITH_...__` plus a leading `# RALPH-TEMPLATE-DO-NOT-APPLY-AS-IS` comment so a `kubectl apply` against an unsubstituted file fails the preflight script. |
| `manifests/ralph-configmap.yaml` | `metadata.name: ralph-config`. Non-secret env values: `RALPH_REPO_URL`, `RALPH_QUEUE_BRANCH=ralph-queue`, `RALPH_MAIN_BRANCH=main`, `ANTHROPIC_MODEL=claude-opus-4-7`, `RALPH_LOG_LEVEL=INFO`, `RALPH_RUN_ONCE=false`. Comment header explains that host-specific config (org URL, project, owner) lives in the Secret (since these are commonly sensitive in production), but a comment block in the ConfigMap documents the variable names so an operator knows what to populate. `RALPH_GIT_HOST` is NOT in the ConfigMap — it is set by the image's ENV directive at build time and is intentionally immutable post-build. |
| `scripts/build_image.sh` | Bash script. Takes REQUIRED `--host github\|ado` argument; exits non-zero with usage error if missing. Computes a tag from `RALPH_VERSION` (env override) or `git rev-parse --short HEAD`, suffixed with `-<host>` (e.g. `ralph-executor:0.1.0-github`). Passes `--build-arg RALPH_GIT_HOST=$host` to `docker build`. Tags the image with both `:<version>-<host>` and `:latest-<host>` (no plain `:latest` — the host suffix is mandatory). Honours `RALPH_REGISTRY` for the push path. Prints the final fully-qualified tag on the last line of stdout. |
| `scripts/preflight.sh` | Bash script. Takes one positional arg: the image tag to test. Runs `docker run --rm <tag> ralph-executor doctor --json`. Asserts exit code 0. Captures stdout/stderr for diagnostics. `ralph-doctor` inside the image probes the host indicated by the image's baked `RALPH_GIT_HOST` env — no extra argument needed; the doctor reads the env. |
| `.claude/settings.json` | The settings.json baked into the image at `/etc/ralph/.claude/settings.json`. Lists `--dangerously-skip-permissions: true` and a `permissions.allow` entry for every tool Ralph might call (including `Skill(pr)` and `Skill(workitem-fetch)` — note these are the staged names, the same regardless of host). |
| `.github/workflows/ralph-image.yml` | GitHub Actions workflow. On push to `main` and on tag `v*`: builds BOTH host images (matrix over `[github, ado]`), runs `scripts/preflight.sh` against each, pushes to the registry on success. Includes a manual-dispatch (`workflow_dispatch`) entry point with a `host` input so an operator can rebuild a single host on demand. |
| `docs/deployment.md` | Operator runbook with a clear **branching structure**: a short intro that asks "Are you at home (GitHub) or at work (ADO)?", then a Phase 1 section that walks through the GitHub path end-to-end, then a Phase 2 section that does the same for ADO. Each path covers: prerequisites, building the image (with the right `--host` flag), the pre-deploy gate, secrets handling (which env vars matter for that host), applying manifests, verifying the pod, troubleshooting, and the Azure Pipelines alternative for the pre-deploy gate. |
| `pyproject.toml` | (Modify) Register a `ralph-executor` console script in `[project.scripts]` so the image's `ENTRYPOINT ["ralph-executor"]` resolves. Plan 7 owns the script itself; this plan asserts the registration. |
| `tests/packaging/__init__.py` | Empty marker. |
| `tests/packaging/test_dockerfile.py` | Pytest tests: Dockerfile parses (using `dockerfile-parse` library), declares both build stages, sets a non-root USER, declares ENTRYPOINT exactly as `["ralph-executor"]`, declares `ARG RALPH_GIT_HOST`, sets `ENV RALPH_GIT_HOST=${RALPH_GIT_HOST}`, copies BOTH host-specific skill directories from `skills/pr-${RALPH_GIT_HOST}` and `skills/workitem-fetch-${RALPH_GIT_HOST}`, copies the baked settings.json, runs as non-root, no shell-form RUN with hard-coded secrets. |
| `tests/packaging/test_manifests.py` | Pytest tests: every YAML in `manifests/` parses; deployment.yaml's `securityContext` is non-root, has resource limits, references the secrets/configmap names exactly; job.yaml has `restartPolicy: Never`, `ttlSecondsAfterFinished`, `backoffLimit: 0`, and the `RALPH_RUN_ONCE=true` env; the template secret carries the `RALPH-TEMPLATE-DO-NOT-APPLY-AS-IS` sentinel AND covers both `GH_TOKEN`/`ADO_PAT` blocks; RBAC references the `ralph` service account. |
| `tests/packaging/test_settings_json.py` | Pytest tests: the baked `.claude/settings.json` parses; `permissions.allow` includes Bash, Edit, Write, Read, Grep, Glob, Task, TodoWrite, `Skill(pr)`, `Skill(workitem-fetch)`, the supervisor skills. `dangerously-skip-permissions` is `true`. |
| `tests/packaging/test_scripts.py` | Pytest tests: `scripts/build_image.sh` and `scripts/preflight.sh` exist, are executable, pass `shellcheck`. The build script's `--help` exits 0. The build script EXITS NON-ZERO when invoked without `--host`. The build script ACCEPTS `--host github` and `--host ado` (parses both, without actually running docker — uses a `--dry-run` flag or stubs `docker` on PATH). |

---

## Cross-plan assumptions ledger

This plan depends on capabilities introduced earlier in the orchestrator. Several of those plans are still in flight at the time of writing; the plan documents each assumption so the implementer can decide whether to stub or to wait.

| Assumption | Source | If not yet true |
|---|---|---|
| `ralph-executor` is installable as a wheel with a console-script entry point named `ralph-executor`. | Plan 7 | Implementer SHALL add a temporary stub `ralph_executor/cli.py` that prints "stub" and exits 0 — the Dockerfile builds against this until Plan 7 lands. Note the stub in `docs/deployment.md`. |
| `ralph-executor health` subcommand returns exit 0 + a JSON `{"ok": true}` blob when the executor is healthy. | Plan 7 (sub-task) | Same — temporary stub returning `{"ok": true}` is acceptable. |
| `ralph-executor doctor` subcommand runs `ralph-doctor` checks inline and exits 0 on pass. | Plan 11 | Same — temporary stub returning exit 0. The doctor probes the host indicated by `RALPH_GIT_HOST` (set by the Dockerfile ENV directive); the runbook MUST flag this so operators don't ship while the stub is in place. |
| `ralph-executor` honours `RALPH_RUN_ONCE=true` to process one PBI and exit (used by Job mode). | Plan 7 | Implementer SHALL document this as a Plan 7 follow-up if not already present. |
| `skills/` tree exists at the repo root with the host-agnostic skills (`ralph-add/`, `ralph-status/`, `ralph-cancel/`, `ralph-promote/`, `ralph-triage/`, `ralph-doctor/`) AND the host-specific skill pairs (`pr-github/`, `workitem-fetch-github/`, `pr-ado/`, `workitem-fetch-ado/`). | Plans 3, 4, 5, 10, 11 | **Phase 1 (github):** requires `pr-github/` and `workitem-fetch-github/` from Plans 5 and 3 respectively. If either is absent, `bash scripts/build_image.sh --host github` will fail at the Dockerfile COPY step — that's the correct fail-fast. **Phase 2 (ado):** requires `pr-ado/` and `workitem-fetch-ado/` (also from Plans 5 and 3, Phase 2 work). The runbook explicitly lists which skills each phase requires and instructs the operator to rebuild after the corresponding plan lands. |
| `ralph-doctor` reads `RALPH_GIT_HOST` and probes only that host. | Plan 11 | If Plan 11 is still stubbed, the doctor probe is trivially successful; once Plan 11 ships real checks, the host-specific probe becomes meaningful. The Dockerfile sets `ENV RALPH_GIT_HOST=${RALPH_GIT_HOST}` so doctor sees it. |

The implementer SHOULD NOT block this plan on Plans 7 / 11 / 10 — the stubs and the assumption ledger let the packaging artifacts land and be verifiable in isolation.

---

## Dockerfile (full content the implementer writes)

The Dockerfile is multi-stage. Stage 1 builds the wheel; stage 2 is the runtime image. The build arg `RALPH_GIT_HOST` selects which host's skills land in the image. **The build arg has no default — it must be supplied via `--build-arg RALPH_GIT_HOST=github|ado`; the build script (next section) enforces this.**

```dockerfile
# syntax=docker/dockerfile:1.7

# ============================================================
# Stage 1 — builder
# Installs uv, syncs locked Python dependencies, builds the
# ralph_executor wheel into /build/dist.
# ============================================================
FROM python:3.12-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1

# Build-only system deps. git is used by uv for git-sourced deps.
RUN apt-get update \
 && apt-get install --no-install-recommends -y \
        build-essential \
        git \
        ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Install uv from the official distribution image (pinned).
COPY --from=ghcr.io/astral-sh/uv:0.4.30 /uv /uvx /usr/local/bin/

WORKDIR /build

# Copy only the dependency manifests first so the layer caches.
COPY pyproject.toml uv.lock README.md ./

# Install dependencies into a project-local virtualenv at /build/.venv.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project

# Now copy the package source and build the wheel.
COPY ralph_executor/ ./ralph_executor/

# Re-sync to install the project itself into .venv.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen

# Build a wheel for the runtime image to install.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv build --wheel --out-dir /build/dist

# ============================================================
# Stage 2 — runtime
# Slim image with Python, Node.js (for Claude Code CLI), the
# ralph_executor wheel, the host-agnostic skills tree, the
# chosen host's skills (copied twice — to /opt/ralph/skills for
# audit and to /root/.claude/skills for Claude's discovery),
# and the baked .claude/settings.json. Runs as a non-root user.
# ============================================================
FROM python:3.12-slim-bookworm AS runtime

# RALPH_GIT_HOST selects the host-specific skill bundle baked
# into this image. There is NO default — the build MUST be
# invoked with --build-arg RALPH_GIT_HOST=github (Phase 1) or
# --build-arg RALPH_GIT_HOST=ado (Phase 2). The check below
# fails the build immediately if the arg is empty.
ARG RALPH_GIT_HOST=""
RUN test -n "${RALPH_GIT_HOST}" \
 || (echo "ERROR: RALPH_GIT_HOST build arg is required (github|ado)" >&2 && exit 2)
RUN test "${RALPH_GIT_HOST}" = "github" -o "${RALPH_GIT_HOST}" = "ado" \
 || (echo "ERROR: RALPH_GIT_HOST must be 'github' or 'ado', got '${RALPH_GIT_HOST}'" >&2 && exit 2)

# Persist the host into the runtime environment so the executor,
# ralph-doctor, and any subprocess inherits it.
ENV RALPH_GIT_HOST=${RALPH_GIT_HOST}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/ralph/venv/bin:/usr/local/bin:/usr/bin:/bin" \
    CLAUDE_CONFIG_DIR=/etc/ralph/.claude \
    RALPH_LOG_LEVEL=INFO

# Runtime system deps.
RUN apt-get update \
 && apt-get install --no-install-recommends -y \
        git \
        ca-certificates \
        curl \
        gnupg \
 && rm -rf /var/lib/apt/lists/*

# Install Node.js 22 LTS via the NodeSource setup script (official).
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
 && apt-get install --no-install-recommends -y nodejs \
 && rm -rf /var/lib/apt/lists/*

# Install Claude Code CLI globally. Version pinned so the image is
# reproducible. Bump deliberately when upgrading.
RUN npm install -g --omit=dev @anthropic-ai/claude-code@1.0.0 \
 && npm cache clean --force

# Create the non-root user and the directories Ralph needs.
RUN groupadd --system --gid 10001 ralph \
 && useradd  --system --uid 10001 --gid 10001 \
        --home-dir /home/ralph --create-home \
        --shell /usr/sbin/nologin ralph \
 && mkdir -p /opt/ralph /opt/ralph/skills /etc/ralph/.claude /var/ralph \
            /root/.claude/skills /home/ralph/.claude/skills \
 && chown -R ralph:ralph /opt/ralph /etc/ralph /var/ralph /home/ralph

WORKDIR /opt/ralph

# Bring the virtualenv from the builder.
COPY --from=builder --chown=ralph:ralph /build/.venv /opt/ralph/venv

# Install the ralph_executor wheel into the venv.
COPY --from=builder /build/dist/*.whl /tmp/
RUN /opt/ralph/venv/bin/pip install --no-deps --no-cache-dir /tmp/*.whl \
 && rm /tmp/*.whl

# Copy the HOST-AGNOSTIC skills. These are the supervisor skills
# and the host-agnostic orchestrators — they ship in every image
# regardless of RALPH_GIT_HOST.
COPY --chown=ralph:ralph skills/ralph-add/       /opt/ralph/skills/ralph-add/
COPY --chown=ralph:ralph skills/ralph-status/    /opt/ralph/skills/ralph-status/
COPY --chown=ralph:ralph skills/ralph-cancel/    /opt/ralph/skills/ralph-cancel/
COPY --chown=ralph:ralph skills/ralph-promote/   /opt/ralph/skills/ralph-promote/
COPY --chown=ralph:ralph skills/ralph-triage/    /opt/ralph/skills/ralph-triage/
COPY --chown=ralph:ralph skills/ralph-doctor/    /opt/ralph/skills/ralph-doctor/

# Mirror the host-agnostic skills into Claude's discovery location
# so claude -p finds them by their canonical names.
COPY --chown=ralph:ralph skills/ralph-add/       /root/.claude/skills/ralph-add/
COPY --chown=ralph:ralph skills/ralph-status/    /root/.claude/skills/ralph-status/
COPY --chown=ralph:ralph skills/ralph-cancel/    /root/.claude/skills/ralph-cancel/
COPY --chown=ralph:ralph skills/ralph-promote/   /root/.claude/skills/ralph-promote/
COPY --chown=ralph:ralph skills/ralph-triage/    /root/.claude/skills/ralph-triage/
COPY --chown=ralph:ralph skills/ralph-doctor/    /root/.claude/skills/ralph-doctor/

# Copy the HOST-SPECIFIC skills. The build arg RALPH_GIT_HOST
# (now in ENV above) drives the source paths. The skills are
# copied TWICE:
#   1) into /opt/ralph/skills/pr-${RALPH_GIT_HOST}/ as the
#      auditable source-of-truth (so "what host did this image
#      bake?" is obvious from `ls /opt/ralph/skills/`);
#   2) into /root/.claude/skills/pr/ and
#      /root/.claude/skills/workitem-fetch/ as the canonical
#      names Claude looks up, so claude -p discovers the skills
#      without any runtime staging step.
COPY --chown=ralph:ralph skills/pr-${RALPH_GIT_HOST}/             /opt/ralph/skills/pr-${RALPH_GIT_HOST}/
COPY --chown=ralph:ralph skills/workitem-fetch-${RALPH_GIT_HOST}/ /opt/ralph/skills/workitem-fetch-${RALPH_GIT_HOST}/
COPY --chown=ralph:ralph skills/pr-${RALPH_GIT_HOST}/             /root/.claude/skills/pr/
COPY --chown=ralph:ralph skills/workitem-fetch-${RALPH_GIT_HOST}/ /root/.claude/skills/workitem-fetch/

# Copy the baked .claude/settings.json. This is the contract:
# --dangerously-skip-permissions + an explicit allow list.
COPY --chown=ralph:ralph .claude/settings.json /etc/ralph/.claude/settings.json

# Re-assert non-root, declare the working directory.
USER ralph
WORKDIR /var/ralph

# OCI labels for image traceability. The ralph.git-host label
# makes the image's host obvious in `docker inspect`.
LABEL org.opencontainers.image.title="ralph-executor" \
      org.opencontainers.image.description="Ralph v1 per-repo executor" \
      org.opencontainers.image.source="https://github.com/emp3thy/ralph" \
      org.opencontainers.image.licenses="MIT" \
      ralph.git-host="${RALPH_GIT_HOST}"

# Default to the long-running executor entrypoint. The Job manifest
# overrides args (or sets RALPH_RUN_ONCE=true) for task-pod mode.
ENTRYPOINT ["ralph-executor"]
CMD ["run"]
```

The implementer SHALL write this file verbatim, only changing the `@anthropic-ai/claude-code@1.0.0` pin if a newer version is the team's current standard at build time.

---

## `.claude/settings.json` (full content the implementer writes)

The settings.json baked into the image is the load-bearing artifact for Spec section "Local vs ROSA differences". The permission entries for `Skill(pr)` and `Skill(workitem-fetch)` use the canonical staged names — not the host-suffixed names — so the same settings.json works for both `github` and `ado` images.

```json
{
  "_comment": "Ralph v1 baked settings.json. DO NOT add interactive permissions here. dangerouslySkipPermissions is intentional and is paired with an explicit permissions.allow list for every tool Ralph might call. The Skill(pr) and Skill(workitem-fetch) entries are the canonical staged names; the Dockerfile copies the chosen host's skill directory into those canonical paths so this settings.json is host-agnostic.",
  "dangerouslySkipPermissions": true,
  "model": "claude-opus-4-7",
  "permissions": {
    "allow": [
      "Bash",
      "Edit",
      "Write",
      "Read",
      "Grep",
      "Glob",
      "Task",
      "TodoWrite",
      "WebFetch",
      "WebSearch",
      "Skill(pr)",
      "Skill(workitem-fetch)",
      "Skill(ralph-add)",
      "Skill(ralph-status)",
      "Skill(ralph-cancel)",
      "Skill(ralph-promote)",
      "Skill(ralph-triage)",
      "Skill(ralph-doctor)"
    ],
    "deny": [
      "Bash(rm -rf /*)",
      "Bash(sudo *)",
      "Bash(curl http://*)",
      "Bash(wget http://*)"
    ]
  },
  "env": {
    "RALPH_LOG_LEVEL": "INFO"
  },
  "_v2_memory_mcp_permissions_commented_out": [
    "mcp__better-memory__memory_retrieve",
    "mcp__better-memory__memory_observe",
    "mcp__better-memory__memory_record_use",
    "mcp__better-memory__memory_start_episode",
    "mcp__better-memory__memory_close_episode"
  ]
}
```

Notes:
- `_comment` and `_v2_memory_mcp_permissions_commented_out` are NON-CANONICAL keys. Claude Code ignores them.
- The `deny` list catches catastrophic Bash invocations only.

---

## Manifests (full content the implementer writes)

### `manifests/ralph-deployment.yaml`

```yaml
# Long-running pod mode.
# Use when the team's ROSA cluster is the durable home of Ralph
# and the executor loops continuously over a single service repo.
#
# IMAGE TAG NOTE: the image tag substituted into __IMAGE__ below
# MUST match the host this deployment targets. Use
#   ralph-executor:<ver>-github   for GitHub deployments,
#   ralph-executor:<ver>-ado      for ADO deployments.
# The image's baked ENV RALPH_GIT_HOST tells the executor and
# ralph-doctor which host to probe; you do NOT need to set
# RALPH_GIT_HOST yourself in this manifest.
#
# See docs/deployment.md ("Choosing a mode" and the Phase 1 /
# Phase 2 sections) before applying.
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ralph-executor
  namespace: ralph
  labels:
    app.kubernetes.io/name: ralph-executor
    app.kubernetes.io/component: executor
    app.kubernetes.io/part-of: ralph
    app.kubernetes.io/managed-by: kubectl
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app.kubernetes.io/name: ralph-executor
  template:
    metadata:
      labels:
        app.kubernetes.io/name: ralph-executor
        app.kubernetes.io/component: executor
        app.kubernetes.io/part-of: ralph
    spec:
      serviceAccountName: ralph
      automountServiceAccountToken: true
      securityContext:
        runAsNonRoot: true
        runAsUser: 10001
        runAsGroup: 10001
        fsGroup: 10001
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: ralph-executor
          # Replaced at deploy time. The tag MUST include the
          # -github or -ado suffix that matches the deployment's
          # target host. The image's ENV RALPH_GIT_HOST drives
          # the executor's behaviour; do NOT add RALPH_GIT_HOST
          # to the env block below.
          image: __IMAGE__
          imagePullPolicy: IfNotPresent
          args: ["run"]
          env:
            - name: RALPH_RUN_ONCE
              value: "false"
          envFrom:
            - configMapRef:
                name: ralph-config
            - secretRef:
                name: ralph-secrets
          resources:
            requests:
              cpu: "500m"
              memory: "1Gi"
              ephemeral-storage: "2Gi"
            limits:
              cpu: "2000m"
              memory: "4Gi"
              ephemeral-storage: "10Gi"
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop:
                - ALL
          readinessProbe:
            exec:
              command: ["ralph-executor", "health", "--ready"]
            initialDelaySeconds: 15
            periodSeconds: 30
            timeoutSeconds: 10
            failureThreshold: 3
          livenessProbe:
            exec:
              command: ["ralph-executor", "health", "--live"]
            initialDelaySeconds: 60
            periodSeconds: 60
            timeoutSeconds: 15
            failureThreshold: 5
          volumeMounts:
            - name: scratch
              mountPath: /var/ralph
            - name: tmp
              mountPath: /tmp
            - name: claude-config
              mountPath: /etc/ralph/.claude
              readOnly: true
            - name: home
              mountPath: /home/ralph
      volumes:
        - name: scratch
          emptyDir:
            sizeLimit: 8Gi
        - name: tmp
          emptyDir:
            sizeLimit: 1Gi
        - name: claude-config
          configMap:
            name: ralph-claude-settings
            items:
              - key: settings.json
                path: settings.json
        - name: home
          emptyDir:
            sizeLimit: 1Gi
      terminationGracePeriodSeconds: 30
      restartPolicy: Always
```

### `manifests/ralph-job.yaml`

```yaml
# Coder workspaces task-pod mode.
# Use when a separate scheduler dispatches one pod per PBI.
#
# IMAGE TAG NOTE: as with the Deployment, the image tag MUST
# match the host this job is dispatched against. The image's
# baked ENV RALPH_GIT_HOST is the source of truth — do not
# override RALPH_GIT_HOST in this manifest.
#
# The Job is INTENTIONALLY a template. A scheduler clones this
# YAML, fills in `__PBI_ID__` (used in the Job name suffix and
# as an env var) and `__IMAGE__`, and applies it. The leading
# sentinel comment marks it as a template so preflight catches
# accidental direct application.
#
# RALPH-TEMPLATE-DO-NOT-APPLY-AS-IS
apiVersion: batch/v1
kind: Job
metadata:
  name: ralph-task-__PBI_ID__
  namespace: ralph
  labels:
    app.kubernetes.io/name: ralph-executor
    app.kubernetes.io/component: task
    app.kubernetes.io/part-of: ralph
    ralph.local/pbi-id: "__PBI_ID__"
spec:
  backoffLimit: 0
  ttlSecondsAfterFinished: 3600
  activeDeadlineSeconds: 7200
  template:
    metadata:
      labels:
        app.kubernetes.io/name: ralph-executor
        app.kubernetes.io/component: task
        app.kubernetes.io/part-of: ralph
        ralph.local/pbi-id: "__PBI_ID__"
    spec:
      restartPolicy: Never
      serviceAccountName: ralph
      automountServiceAccountToken: true
      securityContext:
        runAsNonRoot: true
        runAsUser: 10001
        runAsGroup: 10001
        fsGroup: 10001
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: ralph-executor
          image: __IMAGE__
          imagePullPolicy: IfNotPresent
          args: ["run", "--once", "--pbi-id", "__PBI_ID__"]
          env:
            - name: RALPH_RUN_ONCE
              value: "true"
            - name: RALPH_PBI_ID
              value: "__PBI_ID__"
          envFrom:
            - configMapRef:
                name: ralph-config
            - secretRef:
                name: ralph-secrets
          resources:
            requests:
              cpu: "1000m"
              memory: "2Gi"
              ephemeral-storage: "4Gi"
            limits:
              cpu: "4000m"
              memory: "8Gi"
              ephemeral-storage: "20Gi"
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop:
                - ALL
          volumeMounts:
            - name: scratch
              mountPath: /var/ralph
            - name: tmp
              mountPath: /tmp
            - name: claude-config
              mountPath: /etc/ralph/.claude
              readOnly: true
            - name: home
              mountPath: /home/ralph
      volumes:
        - name: scratch
          emptyDir:
            sizeLimit: 16Gi
        - name: tmp
          emptyDir:
            sizeLimit: 2Gi
        - name: claude-config
          configMap:
            name: ralph-claude-settings
            items:
              - key: settings.json
                path: settings.json
        - name: home
          emptyDir:
            sizeLimit: 1Gi
```

### `manifests/ralph-configmap.yaml`

```yaml
# Non-secret configuration. Used by both the Deployment and the
# Job. Update RALPH_REPO_URL per service.
#
# NOTE on RALPH_GIT_HOST:
#   RALPH_GIT_HOST is NOT in this ConfigMap. It is baked into the
#   image via the Dockerfile's ENV directive at build time and is
#   immutable for the lifetime of that image. To switch a
#   deployment from GitHub to ADO (or vice versa), redeploy with
#   the host-matching image tag — do not try to override
#   RALPH_GIT_HOST at runtime.
#
# NOTE on host-specific values:
#   The non-secret host config (org URL, project, owner) lives
#   in the Secret rather than here, because production teams
#   typically treat the ADO org URL / GitHub org as sensitive
#   (they reveal internal naming). If your team treats them as
#   non-sensitive, you can move them here — the executor reads
#   them from envFrom either way.
apiVersion: v1
kind: ConfigMap
metadata:
  name: ralph-config
  namespace: ralph
  labels:
    app.kubernetes.io/name: ralph-executor
    app.kubernetes.io/part-of: ralph
data:
  RALPH_REPO_URL: "https://example.invalid/replace-me"
  RALPH_QUEUE_BRANCH: "ralph-queue"
  RALPH_MAIN_BRANCH: "main"
  ANTHROPIC_MODEL: "claude-opus-4-7"
  RALPH_LOG_LEVEL: "INFO"
  RALPH_RUN_ONCE: "false"
---
# The baked .claude/settings.json projected as a ConfigMap so
# operators can override the image's defaults without rebuilding.
# The data MUST match /etc/ralph/.claude/settings.json in the
# image. Plan 12 task "Bake settings.json" creates both from the
# same source. This ConfigMap is host-agnostic — it uses the
# canonical Skill(pr) and Skill(workitem-fetch) entries.
apiVersion: v1
kind: ConfigMap
metadata:
  name: ralph-claude-settings
  namespace: ralph
  labels:
    app.kubernetes.io/name: ralph-executor
    app.kubernetes.io/part-of: ralph
data:
  settings.json: |
    {
      "_comment": "Mirror of /etc/ralph/.claude/settings.json baked into the image. Host-agnostic — uses canonical Skill(pr)/Skill(workitem-fetch) entries.",
      "dangerouslySkipPermissions": true,
      "model": "claude-opus-4-7",
      "permissions": {
        "allow": [
          "Bash",
          "Edit",
          "Write",
          "Read",
          "Grep",
          "Glob",
          "Task",
          "TodoWrite",
          "WebFetch",
          "WebSearch",
          "Skill(pr)",
          "Skill(workitem-fetch)",
          "Skill(ralph-add)",
          "Skill(ralph-status)",
          "Skill(ralph-cancel)",
          "Skill(ralph-promote)",
          "Skill(ralph-triage)",
          "Skill(ralph-doctor)"
        ],
        "deny": [
          "Bash(rm -rf /*)",
          "Bash(sudo *)",
          "Bash(curl http://*)",
          "Bash(wget http://*)"
        ]
      },
      "env": {
        "RALPH_LOG_LEVEL": "INFO"
      }
    }
```

### `manifests/ralph-secrets.template.yaml`

```yaml
# RALPH-TEMPLATE-DO-NOT-APPLY-AS-IS
#
# This is a TEMPLATE. The data values are sentinels. Substituting
# them is the operator's responsibility, typically via:
#
#   kubectl create secret generic ralph-secrets \
#     --namespace ralph \
#     --from-literal=ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
#     --from-literal=GH_TOKEN="$GH_TOKEN"         # Phase 1
#     --from-literal=GH_OWNER="myorg"             # Phase 1
#
# OR via External Secrets Operator / SealedSecrets / etc. See
# docs/deployment.md ("Secrets handling") for the supported paths.
#
# scripts/preflight.sh refuses to proceed if these sentinels are
# present in a manifest about to be applied.
#
# WHICH KEYS TO POPULATE:
#   For a GitHub-targeted deployment (image tag ends in -github):
#     - ANTHROPIC_API_KEY   (always required)
#     - GH_TOKEN            (required)
#     - GH_OWNER            (required — the GitHub org or user)
#     Leave ADO_* unset.
#
#   For an ADO-targeted deployment (image tag ends in -ado):
#     - ANTHROPIC_API_KEY   (always required)
#     - ADO_PAT             (required)
#     - ADO_ORG_URL         (required — e.g. https://dev.azure.com/myorg)
#     - ADO_PROJECT         (required — the ADO project name)
#     Leave GH_* unset.
#
#   ralph-doctor (host-aware via the image's baked RALPH_GIT_HOST)
#   verifies the correct set is populated at preflight time.
apiVersion: v1
kind: Secret
metadata:
  name: ralph-secrets
  namespace: ralph
  labels:
    app.kubernetes.io/name: ralph-executor
    app.kubernetes.io/part-of: ralph
type: Opaque
stringData:
  # ---------- Shared (always required) ----------
  ANTHROPIC_API_KEY: "__REPLACE_WITH_ANTHROPIC_API_KEY__"

  # ---------- Phase 1: GitHub deployments ----------
  # Populate these for image tags ending in -github.
  # Leave the literal sentinels in place if this is an ADO deployment
  # (the doctor will not probe them when RALPH_GIT_HOST=ado, but the
  # sentinels still flag the file as unsubstituted to preflight).
  GH_TOKEN: "__REPLACE_WITH_GH_TOKEN__"
  GH_OWNER: "__REPLACE_WITH_GH_OWNER__"

  # ---------- Phase 2: ADO deployments ----------
  # Populate these for image tags ending in -ado.
  # Leave the literal sentinels in place if this is a GitHub
  # deployment.
  ADO_PAT: "__REPLACE_WITH_ADO_PAT__"
  ADO_ORG_URL: "__REPLACE_WITH_ADO_ORG_URL__"
  ADO_PROJECT: "__REPLACE_WITH_ADO_PROJECT__"
```

### `manifests/ralph-rbac.yaml`

```yaml
# Minimal RBAC. Host-agnostic — the same RBAC works for both
# Phase 1 (github) and Phase 2 (ado) deployments. The
# ServiceAccount is the IRSA anchor (annotate it with
# eks.amazonaws.com/role-arn at deploy time).
apiVersion: v1
kind: ServiceAccount
metadata:
  name: ralph
  namespace: ralph
  labels:
    app.kubernetes.io/name: ralph-executor
    app.kubernetes.io/part-of: ralph
  annotations:
    # TODO: At deploy time, set the IAM role ARN for IRSA, e.g.
    # eks.amazonaws.com/role-arn: arn:aws:iam::123456789012:role/ralph-executor
    # See docs/deployment.md ("IAM and IRSA").
    {}
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: ralph-read-own-config
  namespace: ralph
  labels:
    app.kubernetes.io/name: ralph-executor
    app.kubernetes.io/part-of: ralph
rules:
  - apiGroups: [""]
    resources: ["configmaps"]
    resourceNames: ["ralph-config", "ralph-claude-settings"]
    verbs: ["get", "watch"]
  - apiGroups: [""]
    resources: ["secrets"]
    resourceNames: ["ralph-secrets"]
    verbs: ["get", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: ralph-read-own-config
  namespace: ralph
  labels:
    app.kubernetes.io/name: ralph-executor
    app.kubernetes.io/part-of: ralph
subjects:
  - kind: ServiceAccount
    name: ralph
    namespace: ralph
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: ralph-read-own-config
```

---

## Build and preflight scripts (full content the implementer writes)

### `scripts/build_image.sh`

```bash
#!/usr/bin/env bash
# build_image.sh — build a host-specific ralph-executor container
# image. The --host flag is REQUIRED and selects which set of
# host-specific skills the image carries. The resulting image tag
# is suffixed with -<host> so the host is obvious in `docker images`.
#
# Required:
#   --host github|ado   Which git host this image targets. Passes
#                       through to docker build as
#                       --build-arg RALPH_GIT_HOST=<host>.
#
# Optional:
#   --push              Push to RALPH_REGISTRY after building.
#   --help              Print this usage text.
#
# Environment:
#   RALPH_VERSION       Explicit version tag (e.g. 0.1.0). Defaults
#                       to `git rev-parse --short HEAD` or "dev".
#   RALPH_REGISTRY      Fully-qualified registry prefix (e.g. an
#                       ECR URL). If set, the image is also tagged
#                       and (with --push) pushed there.
#   RALPH_IMAGE         Image name. Defaults to ralph-executor.
#
# Outputs:
#   The last line of stdout is the fully-qualified tag of the
#   image that was just built, so callers can pipe it:
#
#       IMG=$(scripts/build_image.sh --host github)
#       scripts/preflight.sh "$IMG"
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: build_image.sh --host github|ado [--push] [--help]

Required:
  --host github|ado   Which git host this image targets.

Optional:
  --push              Push to RALPH_REGISTRY after building.
  --help              Print this usage text.

Environment:
  RALPH_VERSION   Explicit version tag. Defaults to git short SHA.
  RALPH_REGISTRY  Registry prefix; if set, the image is also
                  tagged as $RALPH_REGISTRY/$RALPH_IMAGE:<ver>-<host>
                  and pushed when --push is given.
  RALPH_IMAGE     Image name. Defaults to ralph-executor.

Examples:
  bash scripts/build_image.sh --host github
  bash scripts/build_image.sh --host ado --push
  RALPH_VERSION=0.1.0 bash scripts/build_image.sh --host github
EOF
}

HOST=""
PUSH=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --host)
      if [[ $# -lt 2 ]]; then
        echo "ERROR: --host requires an argument (github|ado)" >&2
        usage >&2
        exit 2
      fi
      HOST="$2"
      shift 2
      ;;
    --host=*)
      HOST="${1#--host=}"
      shift
      ;;
    --push) PUSH=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ -z "${HOST}" ]]; then
  echo "ERROR: --host is required (github|ado)" >&2
  usage >&2
  exit 2
fi

if [[ "${HOST}" != "github" && "${HOST}" != "ado" ]]; then
  echo "ERROR: --host must be 'github' or 'ado', got '${HOST}'" >&2
  usage >&2
  exit 2
fi

RALPH_IMAGE="${RALPH_IMAGE:-ralph-executor}"
RALPH_VERSION="${RALPH_VERSION:-$(git rev-parse --short HEAD 2>/dev/null || echo "dev")}"
LOCAL_TAG="${RALPH_IMAGE}:${RALPH_VERSION}-${HOST}"
LATEST_TAG="${RALPH_IMAGE}:latest-${HOST}"

echo "Building ${LOCAL_TAG} (RALPH_GIT_HOST=${HOST})" >&2

DOCKER_BUILDKIT=1 docker build \
  --progress=plain \
  --build-arg "RALPH_GIT_HOST=${HOST}" \
  --tag "${LOCAL_TAG}" \
  --tag "${LATEST_TAG}" \
  --file Dockerfile \
  .

FINAL_TAG="${LOCAL_TAG}"
if [[ -n "${RALPH_REGISTRY:-}" ]]; then
  REMOTE_TAG="${RALPH_REGISTRY}/${RALPH_IMAGE}:${RALPH_VERSION}-${HOST}"
  REMOTE_LATEST="${RALPH_REGISTRY}/${RALPH_IMAGE}:latest-${HOST}"
  docker tag "${LOCAL_TAG}"  "${REMOTE_TAG}"
  docker tag "${LATEST_TAG}" "${REMOTE_LATEST}"
  if [[ "${PUSH}" -eq 1 ]]; then
    echo "Pushing ${REMOTE_TAG} and ${REMOTE_LATEST}" >&2
    docker push "${REMOTE_TAG}"
    docker push "${REMOTE_LATEST}"
  fi
  FINAL_TAG="${REMOTE_TAG}"
fi

# Last line of stdout is the tag callers should use.
echo "${FINAL_TAG}"
```

### `scripts/preflight.sh`

```bash
#!/usr/bin/env bash
# preflight.sh — run ralph-doctor inside a freshly built image to
# verify it is safe to deploy. The CI pipeline calls this between
# build_image.sh and the push step.
#
# The image's baked ENV RALPH_GIT_HOST tells ralph-doctor which
# host to probe — preflight does NOT pass a host argument; it
# trusts what was baked at build time.
#
# Usage: preflight.sh <image-tag>
#
# Exit codes:
#   0  doctor passed
#   2  doctor failed (image must NOT be promoted)
#   3  could not run the container (docker error)
#   4  doctor binary not present (image too old to gate)
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: preflight.sh <image-tag>" >&2
  exit 2
fi

IMAGE="$1"

echo "Preflight: running ralph-doctor against ${IMAGE}" >&2
echo "  (doctor probes the host baked into the image's RALPH_GIT_HOST env)" >&2

OUT_DIR="$(mktemp -d -t ralph-preflight-XXXXXX)"
trap 'rm -rf "${OUT_DIR}"' EXIT

set +e
docker run --rm --entrypoint ralph-executor "${IMAGE}" doctor --json \
  >"${OUT_DIR}/stdout.json" 2>"${OUT_DIR}/stderr.log"
DOCKER_EXIT=$?
set -e

if [[ "${DOCKER_EXIT}" -eq 127 ]]; then
  echo "preflight: ralph-executor doctor not present in image" >&2
  cat "${OUT_DIR}/stderr.log" >&2 || true
  exit 4
fi

if [[ "${DOCKER_EXIT}" -ne 0 ]]; then
  echo "preflight: doctor failed with exit ${DOCKER_EXIT}" >&2
  echo "--- doctor stdout ---" >&2
  cat "${OUT_DIR}/stdout.json" >&2 || true
  echo "--- doctor stderr ---" >&2
  cat "${OUT_DIR}/stderr.log" >&2 || true
  exit 2
fi

echo "preflight: doctor passed" >&2
echo "--- doctor report ---" >&2
cat "${OUT_DIR}/stdout.json" >&2 || true

exit 0
```

---

## CI workflow (full content the implementer writes)

### `.github/workflows/ralph-image.yml` (primary path)

```yaml
name: ralph-image

on:
  push:
    branches: [ "main" ]
    paths:
      - "Dockerfile"
      - ".dockerignore"
      - "pyproject.toml"
      - "uv.lock"
      - "ralph_executor/**"
      - "skills/**"
      - ".claude/settings.json"
      - "scripts/build_image.sh"
      - "scripts/preflight.sh"
      - ".github/workflows/ralph-image.yml"
  push:
    tags: [ "v*" ]
  workflow_dispatch:
    inputs:
      version:
        description: "Override version tag (defaults to git short SHA)"
        required: false
        type: string
      host:
        description: "Limit to a single host (github|ado|both)"
        required: false
        default: "both"
        type: choice
        options: [github, ado, both]

permissions:
  contents: read
  id-token: write
  packages: write

jobs:
  build-and-preflight:
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        host: [github, ado]
    env:
      RALPH_IMAGE: ralph-executor
      RALPH_REGISTRY: ${{ secrets.RALPH_REGISTRY }}
    steps:
      - name: Skip non-selected host
        if: ${{ github.event.inputs.host != '' && github.event.inputs.host != 'both' && github.event.inputs.host != matrix.host }}
        run: echo "Skipping host=${{ matrix.host }} per workflow_dispatch input"; exit 0

      - name: Checkout
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Compute version tag
        id: ver
        run: |
          if [[ -n "${{ github.event.inputs.version }}" ]]; then
            echo "tag=${{ github.event.inputs.version }}" >> "$GITHUB_OUTPUT"
          else
            echo "tag=$(git rev-parse --short HEAD)" >> "$GITHUB_OUTPUT"
          fi

      - name: Build image (${{ matrix.host }})
        id: build
        env:
          RALPH_VERSION: ${{ steps.ver.outputs.tag }}
        run: |
          IMG=$(bash scripts/build_image.sh --host ${{ matrix.host }})
          echo "image=${IMG}" >> "$GITHUB_OUTPUT"

      - name: Preflight (ralph-doctor inside the image)
        run: bash scripts/preflight.sh "${{ steps.build.outputs.image }}"

      - name: Registry login
        if: ${{ env.RALPH_REGISTRY != '' }}
        uses: docker/login-action@v3
        with:
          registry: ${{ env.RALPH_REGISTRY }}
          username: ${{ secrets.RALPH_REGISTRY_USER }}
          password: ${{ secrets.RALPH_REGISTRY_PASSWORD }}

      - name: Push image
        if: ${{ env.RALPH_REGISTRY != '' && (github.ref == 'refs/heads/main' || startsWith(github.ref, 'refs/tags/v')) }}
        env:
          RALPH_VERSION: ${{ steps.ver.outputs.tag }}
        run: bash scripts/build_image.sh --host ${{ matrix.host }} --push
```

The Azure Pipelines alternative is documented in `docs/deployment.md`.

---

## Tasks

The plan is split into tasks. Run them in order. Each task ends with an explicit verification command. Do not start a downstream task while an upstream task's verification is failing.

### Task 1 — Preconditions and scaffolding

**Files**
- Create: `tests/packaging/__init__.py`
- Modify: `pyproject.toml`

**Steps**

- [x] 1. Confirm the orchestrator's Wave 1 and Wave 2 outputs are present (or stubbed). Run:
  ```
  test -f pyproject.toml
  test -f uv.lock
  test -d ralph_executor
  ```
  Expected: every check exits 0. If `ralph_executor/` is absent, create a stub package containing only `ralph_executor/__init__.py` and `ralph_executor/cli.py` with the body documented in the "Cross-plan assumptions ledger" above.

- [x] 2. Open `pyproject.toml`. Confirm the `[project.scripts]` table exists and contains:
  ```toml
  [project.scripts]
  ralph-executor = "ralph_executor.cli:main"
  ```
  If absent, add it.

- [x] 3. Confirm the host-specific skill directories exist for the host(s) you intend to build. **For Phase 1 (github):**
  ```
  test -d skills/pr-github
  test -d skills/workitem-fetch-github
  ```
  Both MUST be present before `bash scripts/build_image.sh --host github` will succeed (the Dockerfile COPY step references them). If either is missing, the responsible plan is Plan 3 (`workitem-fetch-github`) or Plan 5 (`pr-github`).
  **For Phase 2 (ado):**
  ```
  test -d skills/pr-ado
  test -d skills/workitem-fetch-ado
  ```
  If either is missing, that's expected before Plans 3/5 Phase 2 lands — Phase 2 image builds will be blocked until those land. Phase 1 is unblocked.

- [x] 4. Create `tests/packaging/__init__.py`:
  ```python
  """Empty package marker."""
  ```

- [x] 5. Add the packaging-test dev dependencies to `pyproject.toml`:
  ```
  dockerfile-parse>=2.0
  pyyaml>=6.0
  ```
  Then `uv sync`. Expected: completes without error.

- [x] 6. Verify the toolchain:
  ```
  uv run mypy --version
  uv run pytest --collect-only -q
  ```

### Task 2 — Write the baked `.claude/settings.json`

**Files**
- Create: `.claude/settings.json`
- Create: `tests/packaging/test_settings_json.py`

**Steps**

- [x] 1. Write `.claude/settings.json` with the exact content listed in the ".claude/settings.json (full content the implementer writes)" section above. No edits.

- [x] 2. Confirm valid JSON:
  ```
  uv run python -c "import json; json.load(open('.claude/settings.json'))"
  ```

- [x] 3. Create `tests/packaging/test_settings_json.py`:
  ```python
  """Tests for the baked .claude/settings.json."""

  import json
  from pathlib import Path

  import pytest

  SETTINGS_PATH = Path(__file__).resolve().parents[2] / ".claude" / "settings.json"


  @pytest.fixture(scope="module")
  def settings() -> dict[str, object]:
      assert SETTINGS_PATH.exists(), f"missing {SETTINGS_PATH}"
      with SETTINGS_PATH.open("r", encoding="utf-8") as fh:
          return json.load(fh)


  def test_dangerously_skip_permissions_is_true(settings: dict[str, object]) -> None:
      assert settings.get("dangerouslySkipPermissions") is True


  def test_model_is_pinned(settings: dict[str, object]) -> None:
      model = settings.get("model")
      assert isinstance(model, str) and model.startswith("claude-")


  def test_permissions_allow_lists_every_required_tool(settings: dict[str, object]) -> None:
      perms = settings.get("permissions", {})
      assert isinstance(perms, dict)
      allow = perms.get("allow", [])
      assert isinstance(allow, list)
      required = {
          "Bash",
          "Edit",
          "Write",
          "Read",
          "Grep",
          "Glob",
          "Task",
          "TodoWrite",
          "Skill(pr)",
          "Skill(workitem-fetch)",
          "Skill(ralph-add)",
          "Skill(ralph-status)",
          "Skill(ralph-cancel)",
          "Skill(ralph-promote)",
          "Skill(ralph-triage)",
          "Skill(ralph-doctor)",
      }
      missing = required - set(allow)
      assert not missing, f"missing permissions.allow entries: {missing}"


  def test_pr_and_workitem_fetch_use_canonical_names(settings: dict[str, object]) -> None:
      """The image bakes the host-specific skills into canonical
      paths (skills/pr/, skills/workitem-fetch/), so settings.json
      MUST use the canonical names not the host-suffixed ones."""
      perms = settings.get("permissions", {})
      allow = set(perms.get("allow", []))
      assert "Skill(pr)" in allow
      assert "Skill(workitem-fetch)" in allow
      assert "Skill(pr-github)" not in allow
      assert "Skill(pr-ado)" not in allow


  def test_deny_list_blocks_obvious_footguns(settings: dict[str, object]) -> None:
      perms = settings.get("permissions", {})
      deny = perms.get("deny", [])
      assert "Bash(rm -rf /*)" in deny
      assert "Bash(sudo *)" in deny


  def test_v2_memory_permissions_present_but_commented(settings: dict[str, object]) -> None:
      reserved = settings.get("_v2_memory_mcp_permissions_commented_out")
      assert isinstance(reserved, list)
      assert any("memory_retrieve" in entry for entry in reserved)
  ```

- [x] 4. Run the new tests:
  ```
  uv run pytest tests/packaging/test_settings_json.py -v
  ```

### Task 3 — Write the Dockerfile and `.dockerignore`

**Files**
- Create: `Dockerfile`
- Create: `.dockerignore`
- Create: `tests/packaging/test_dockerfile.py`

**Steps**

- [x] 1. Write `Dockerfile` with the exact content listed in the "Dockerfile (full content the implementer writes)" section above. No edits.

- [x] 2. Write `.dockerignore`:
  ```gitignore
  # Source-control + build state
  .git
  .gitignore
  .gitattributes

  # Python build / cache artifacts
  __pycache__
  *.py[cod]
  *.egg-info
  .pytest_cache
  .ruff_cache
  .mypy_cache
  .coverage
  .tox
  build
  dist

  # uv / virtualenvs
  .venv
  uv-cache

  # Node / Claude Code dev artefacts
  node_modules

  # Tests, samples, docs
  tests
  samples
  docs
  README.md
  CONTRIBUTING.md
  CHANGELOG.md

  # Editor / OS junk
  .vscode
  .idea
  *.swp
  *.swo
  .DS_Store
  Thumbs.db

  # CI / k8s files
  .github
  manifests
  ```

- [x] 3. Create `tests/packaging/test_dockerfile.py`:
  ```python
  """Static tests for the Dockerfile.

  These do NOT build the image. They parse the Dockerfile and
  assert structural properties so failures show up in pytest
  rather than in a 5-minute CI build.
  """

  from pathlib import Path

  import pytest
  from dockerfile_parse import DockerfileParser

  DOCKERFILE = Path(__file__).resolve().parents[2] / "Dockerfile"


  @pytest.fixture(scope="module")
  def parser() -> DockerfileParser:
      assert DOCKERFILE.exists(), f"missing {DOCKERFILE}"
      dfp = DockerfileParser(fileobj=DOCKERFILE.open("rb"))
      return dfp


  def test_dockerfile_has_two_stages(parser: DockerfileParser) -> None:
      stages = [s for s in parser.structure if s["instruction"] == "FROM"]
      names = {s["value"].split(" AS ")[-1].strip() for s in stages if " AS " in s["value"]}
      assert "builder" in names, f"expected builder stage, got {names}"
      assert "runtime" in names, f"expected runtime stage, got {names}"


  def test_dockerfile_declares_ralph_git_host_arg(parser: DockerfileParser) -> None:
      args = [s for s in parser.structure if s["instruction"] == "ARG"]
      blob = " ".join(s["value"] for s in args)
      assert "RALPH_GIT_HOST" in blob, "expected ARG RALPH_GIT_HOST"


  def test_dockerfile_sets_ralph_git_host_env(parser: DockerfileParser) -> None:
      contents = DOCKERFILE.read_text(encoding="utf-8")
      assert "ENV RALPH_GIT_HOST=${RALPH_GIT_HOST}" in contents, (
          "expected ENV RALPH_GIT_HOST=${RALPH_GIT_HOST} so doctor sees the host"
      )


  def test_dockerfile_fails_on_empty_ralph_git_host(parser: DockerfileParser) -> None:
      """The Dockerfile MUST fail the build if RALPH_GIT_HOST is
      not provided. Look for the guard RUN line."""
      contents = DOCKERFILE.read_text(encoding="utf-8")
      assert 'test -n "${RALPH_GIT_HOST}"' in contents, (
          "expected guard `test -n` to fail builds without the build arg"
      )


  def test_dockerfile_copies_host_specific_skills(parser: DockerfileParser) -> None:
      contents = DOCKERFILE.read_text(encoding="utf-8")
      # Each host's skills must be copied twice — once to /opt/ralph/skills
      # (audit) and once to /root/.claude/skills (canonical name).
      assert "skills/pr-${RALPH_GIT_HOST}/" in contents
      assert "skills/workitem-fetch-${RALPH_GIT_HOST}/" in contents
      assert "/root/.claude/skills/pr/" in contents
      assert "/root/.claude/skills/workitem-fetch/" in contents


  def test_runtime_image_runs_as_non_root(parser: DockerfileParser) -> None:
      user_lines = [s for s in parser.structure if s["instruction"] == "USER"]
      assert user_lines, "no USER directive found"
      last = user_lines[-1]["value"].strip()
      assert last == "ralph", f"final USER must be ralph, got {last!r}"


  def test_entrypoint_is_ralph_executor(parser: DockerfileParser) -> None:
      ep_lines = [s for s in parser.structure if s["instruction"] == "ENTRYPOINT"]
      assert ep_lines, "no ENTRYPOINT"
      value = ep_lines[-1]["value"]
      assert "ralph-executor" in value


  def test_no_inline_secrets(parser: DockerfileParser) -> None:
      contents = DOCKERFILE.read_text(encoding="utf-8")
      for needle in ("ANTHROPIC_API_KEY=", "ADO_PAT=", "GH_TOKEN=", "AWS_SECRET_ACCESS_KEY="):
          assert needle not in contents, (
              f"found hard-coded secret marker {needle!r} in Dockerfile"
          )


  def test_baked_settings_copied(parser: DockerfileParser) -> None:
      contents = DOCKERFILE.read_text(encoding="utf-8")
      assert ".claude/settings.json /etc/ralph/.claude/settings.json" in contents


  def test_node_and_claude_code_installed(parser: DockerfileParser) -> None:
      contents = DOCKERFILE.read_text(encoding="utf-8")
      assert "nodejs" in contents
      assert "@anthropic-ai/claude-code" in contents


  def test_oci_labels_set_with_host_label(parser: DockerfileParser) -> None:
      labels = [s for s in parser.structure if s["instruction"] == "LABEL"]
      blob = " ".join(s["value"] for s in labels)
      assert "org.opencontainers.image.title" in blob
      assert "org.opencontainers.image.source" in blob
      assert "ralph.git-host" in blob, "expected ralph.git-host LABEL for host traceability"
  ```

- [x] 4. Run the Dockerfile tests:
  ```
  uv run pytest tests/packaging/test_dockerfile.py -v
  ```

- [ ] 5. Sanity-check that the Dockerfile FAILS when invoked without the build arg (this is the most important new property):
  ```
  set +e
  DOCKER_BUILDKIT=1 docker build --file Dockerfile --tag ralph-no-host:test . 2>&1 | tail -5
  echo "exit=$?"
  set -e
  ```
  Expected: non-zero exit, error message mentions "RALPH_GIT_HOST build arg is required". If the build succeeds with an empty arg, the Dockerfile guard is broken.

- [ ] 6. Sanity-check that the Dockerfile FAILS when invoked with an invalid host:
  ```
  set +e
  DOCKER_BUILDKIT=1 docker build --build-arg RALPH_GIT_HOST=gitlab --file Dockerfile --tag ralph-bad-host:test . 2>&1 | tail -5
  echo "exit=$?"
  set -e
  ```
  Expected: non-zero exit, error message mentions "must be 'github' or 'ado'".

### Task 4 — Write the k8s manifests

**Files**
- Create: `manifests/ralph-deployment.yaml`
- Create: `manifests/ralph-job.yaml`
- Create: `manifests/ralph-configmap.yaml`
- Create: `manifests/ralph-secrets.template.yaml`
- Create: `manifests/ralph-rbac.yaml`
- Create: `tests/packaging/test_manifests.py`

**Steps**

- [x] 1. Create each manifest file with the exact content listed in the "Manifests (full content the implementer writes)" section above. Use LF line endings.

- [x] 2. Validate each manifest's YAML syntax:
  ```
  uv run python -c "
  import yaml
  for path in [
      'manifests/ralph-deployment.yaml',
      'manifests/ralph-job.yaml',
      'manifests/ralph-configmap.yaml',
      'manifests/ralph-secrets.template.yaml',
      'manifests/ralph-rbac.yaml',
  ]:
      with open(path, 'r', encoding='utf-8') as f:
          docs = list(yaml.safe_load_all(f))
      assert docs, path
      print(path, len(docs), 'doc(s)')
  "
  ```

- [ ] 3. If `kubectl` is available, run client-side dry-run apply for the manifests that should validate (configmap, secrets template, rbac): [SKIPPED — kubectl not installed in executor environment]
  ```
  kubectl apply --dry-run=client -f manifests/ralph-configmap.yaml
  kubectl apply --dry-run=client -f manifests/ralph-rbac.yaml
  kubectl apply --dry-run=client -f manifests/ralph-secrets.template.yaml
  ```
  The deployment and job will fail dry-run because of `__IMAGE__` — that's expected.

- [x] 4. Create `tests/packaging/test_manifests.py`:
  ```python
  """Structural tests for k8s manifests."""

  from pathlib import Path

  import pytest
  import yaml

  MANIFESTS = Path(__file__).resolve().parents[2] / "manifests"


  def _load(path: Path) -> list[dict]:
      with path.open("r", encoding="utf-8") as fh:
          return [doc for doc in yaml.safe_load_all(fh) if doc is not None]


  def test_deployment_is_non_root_with_limits() -> None:
      docs = _load(MANIFESTS / "ralph-deployment.yaml")
      assert len(docs) == 1
      dep = docs[0]
      assert dep["kind"] == "Deployment"
      pod_spec = dep["spec"]["template"]["spec"]
      assert pod_spec["securityContext"]["runAsNonRoot"] is True
      assert pod_spec["securityContext"]["runAsUser"] == 10001
      assert pod_spec["serviceAccountName"] == "ralph"
      container = pod_spec["containers"][0]
      assert container["securityContext"]["allowPrivilegeEscalation"] is False
      assert container["securityContext"]["readOnlyRootFilesystem"] is True
      assert container["resources"]["limits"]["cpu"]
      assert container["resources"]["limits"]["memory"]
      assert container["resources"]["requests"]["cpu"]
      assert container["resources"]["requests"]["memory"]
      assert container["readinessProbe"]["exec"]["command"][0] == "ralph-executor"
      assert "ready" in " ".join(container["readinessProbe"]["exec"]["command"])
      assert container["livenessProbe"]["exec"]["command"][0] == "ralph-executor"
      ref_names = {ref["configMapRef"]["name"] for ref in container["envFrom"] if "configMapRef" in ref}
      assert "ralph-config" in ref_names
      ref_secret = {ref["secretRef"]["name"] for ref in container["envFrom"] if "secretRef" in ref}
      assert "ralph-secrets" in ref_secret


  def test_deployment_does_not_set_ralph_git_host_env() -> None:
      """RALPH_GIT_HOST is baked into the image, not set in the
      manifest. If the manifest tries to set it, ENV order rules
      mean envFrom would override it — but more importantly, the
      manifest setting it would imply runtime is the right place
      for the host decision, which contradicts the design."""
      docs = _load(MANIFESTS / "ralph-deployment.yaml")
      container = docs[0]["spec"]["template"]["spec"]["containers"][0]
      env_names = {e["name"] for e in container.get("env", [])}
      assert "RALPH_GIT_HOST" not in env_names, (
          "RALPH_GIT_HOST must not be set in the manifest; it is "
          "baked into the image at build time."
      )


  def test_deployment_image_tag_comment_present() -> None:
      raw = (MANIFESTS / "ralph-deployment.yaml").read_text(encoding="utf-8")
      assert "-github" in raw and "-ado" in raw, (
          "deployment manifest must document the host-tagged image naming"
      )


  def test_job_is_task_pod_shape() -> None:
      docs = _load(MANIFESTS / "ralph-job.yaml")
      assert len(docs) == 1
      job = docs[0]
      assert job["kind"] == "Job"
      spec = job["spec"]
      assert spec["backoffLimit"] == 0
      assert spec["ttlSecondsAfterFinished"] >= 1
      pod_spec = spec["template"]["spec"]
      assert pod_spec["restartPolicy"] == "Never"
      assert pod_spec["serviceAccountName"] == "ralph"
      container = pod_spec["containers"][0]
      env = {e["name"]: e["value"] for e in container.get("env", [])}
      assert env.get("RALPH_RUN_ONCE") == "true"
      assert "--once" in container["args"]


  def test_configmap_carries_expected_keys() -> None:
      docs = _load(MANIFESTS / "ralph-configmap.yaml")
      by_name = {d["metadata"]["name"]: d for d in docs}
      assert "ralph-config" in by_name
      assert "ralph-claude-settings" in by_name
      cfg = by_name["ralph-config"]["data"]
      for key in (
          "RALPH_REPO_URL",
          "RALPH_QUEUE_BRANCH",
          "RALPH_MAIN_BRANCH",
          "ANTHROPIC_MODEL",
          "RALPH_LOG_LEVEL",
      ):
          assert key in cfg, f"missing {key} in ralph-config"
      # RALPH_GIT_HOST must NOT live in the ConfigMap — it is
      # baked into the image.
      assert "RALPH_GIT_HOST" not in cfg


  def test_secrets_template_covers_both_hosts() -> None:
      path = MANIFESTS / "ralph-secrets.template.yaml"
      raw = path.read_text(encoding="utf-8")
      assert "RALPH-TEMPLATE-DO-NOT-APPLY-AS-IS" in raw
      docs = _load(path)
      assert len(docs) == 1
      sec = docs[0]
      assert sec["kind"] == "Secret"
      data = sec["stringData"]
      # Shared
      assert data["ANTHROPIC_API_KEY"].startswith("__REPLACE_WITH")
      # Phase 1 (github)
      assert data["GH_TOKEN"].startswith("__REPLACE_WITH")
      assert data["GH_OWNER"].startswith("__REPLACE_WITH")
      # Phase 2 (ado)
      assert data["ADO_PAT"].startswith("__REPLACE_WITH")
      assert data["ADO_ORG_URL"].startswith("__REPLACE_WITH")
      assert data["ADO_PROJECT"].startswith("__REPLACE_WITH")
      # Documentation comment
      assert "GitHub deployments" in raw
      assert "ADO deployments" in raw


  def test_rbac_binds_ralph_service_account() -> None:
      docs = _load(MANIFESTS / "ralph-rbac.yaml")
      kinds = {d["kind"]: d for d in docs}
      assert "ServiceAccount" in kinds
      assert "Role" in kinds
      assert "RoleBinding" in kinds
      sa = kinds["ServiceAccount"]
      assert sa["metadata"]["name"] == "ralph"
      binding = kinds["RoleBinding"]
      assert any(
          s.get("kind") == "ServiceAccount" and s.get("name") == "ralph"
          for s in binding["subjects"]
      )
  ```

- [x] 5. Run the manifest tests:
  ```
  uv run pytest tests/packaging/test_manifests.py -v
  ```

### Task 5 — Write the build and preflight scripts

**Files**
- Create: `scripts/build_image.sh`
- Create: `scripts/preflight.sh`
- Create: `tests/packaging/test_scripts.py`

**Steps**

- [ ] 1. Write `scripts/build_image.sh` and `scripts/preflight.sh` with the exact content from the "Build and preflight scripts" section above. LF line endings.

- [ ] 2. Mark both executable:
  ```
  chmod +x scripts/build_image.sh scripts/preflight.sh
  ```
  On Windows:
  ```
  git update-index --chmod=+x scripts/build_image.sh
  git update-index --chmod=+x scripts/preflight.sh
  ```

- [ ] 3. If `shellcheck` is installed, run it:
  ```
  shellcheck scripts/build_image.sh scripts/preflight.sh
  ```
  Fix any findings before continuing.

- [ ] 4. Smoke-run the build script's help and the missing-host error path:
  ```
  bash scripts/build_image.sh --help
  echo "help exit=$?"

  set +e
  bash scripts/build_image.sh 2>/dev/null
  NO_HOST_EXIT=$?
  set -e
  echo "no-host exit=${NO_HOST_EXIT}"

  set +e
  bash scripts/build_image.sh --host gitlab 2>/dev/null
  BAD_HOST_EXIT=$?
  set -e
  echo "bad-host exit=${BAD_HOST_EXIT}"
  ```
  Expected: help exit 0; no-host exit 2; bad-host exit 2.

- [ ] 5. Create `tests/packaging/test_scripts.py`:
  ```python
  """Tests for the bash helper scripts."""

  import os
  import shutil
  import stat
  import subprocess
  from pathlib import Path

  import pytest

  SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


  @pytest.mark.parametrize("name", ["build_image.sh", "preflight.sh"])
  def test_script_exists_and_is_executable(name: str) -> None:
      path = SCRIPTS / name
      assert path.exists(), f"missing {path}"
      if os.name != "nt":
          mode = path.stat().st_mode
          assert mode & stat.S_IXUSR, f"{path} not executable"


  @pytest.mark.parametrize("name", ["build_image.sh", "preflight.sh"])
  def test_script_passes_shellcheck(name: str) -> None:
      if shutil.which("shellcheck") is None:
          pytest.skip("shellcheck not installed")
      result = subprocess.run(
          ["shellcheck", str(SCRIPTS / name)],
          capture_output=True,
          text=True,
          check=False,
      )
      assert result.returncode == 0, f"shellcheck findings:\n{result.stdout}\n{result.stderr}"


  def test_build_script_help_exits_zero() -> None:
      result = subprocess.run(
          ["bash", str(SCRIPTS / "build_image.sh"), "--help"],
          capture_output=True,
          text=True,
          check=False,
      )
      assert result.returncode == 0
      assert "Usage" in result.stdout or "Usage" in result.stderr


  def test_build_script_requires_host() -> None:
      """No --host flag MUST be a usage error (exit 2)."""
      result = subprocess.run(
          ["bash", str(SCRIPTS / "build_image.sh")],
          capture_output=True,
          text=True,
          check=False,
      )
      assert result.returncode == 2, (
          f"expected exit 2 for missing --host, got {result.returncode}\n"
          f"stdout: {result.stdout}\nstderr: {result.stderr}"
      )
      assert "--host" in result.stderr or "host" in result.stderr.lower()


  def test_build_script_rejects_unknown_host() -> None:
      """--host gitlab (or any value other than github|ado) MUST exit 2."""
      result = subprocess.run(
          ["bash", str(SCRIPTS / "build_image.sh"), "--host", "gitlab"],
          capture_output=True,
          text=True,
          check=False,
      )
      assert result.returncode == 2
      assert "github" in result.stderr and "ado" in result.stderr


  @pytest.mark.parametrize("host", ["github", "ado"])
  def test_build_script_accepts_valid_hosts(host: str) -> None:
      """--host github and --host ado MUST be accepted by the parser.
      The actual docker build is not invoked here — we shim docker
      with a stub on PATH so the script returns immediately after
      argument parsing."""
      import os as _os
      import tempfile as _tempfile

      with _tempfile.TemporaryDirectory() as td:
          shim = Path(td) / "docker"
          shim.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
          shim.chmod(0o755)
          env = dict(_os.environ)
          env["PATH"] = f"{td}:{env.get('PATH', '')}"
          env.pop("RALPH_REGISTRY", None)
          result = subprocess.run(
              ["bash", str(SCRIPTS / "build_image.sh"), "--host", host],
              capture_output=True,
              text=True,
              check=False,
              env=env,
          )
          assert result.returncode == 0, (
              f"expected exit 0 for --host {host}, got {result.returncode}\n"
              f"stdout: {result.stdout}\nstderr: {result.stderr}"
          )
          # The last line of stdout is the tag, which must include
          # the host suffix.
          last_line = result.stdout.strip().splitlines()[-1]
          assert last_line.endswith(f"-{host}"), (
              f"expected tag suffix -{host}, got {last_line!r}"
          )
  ```

- [ ] 6. Run the script tests:
  ```
  uv run pytest tests/packaging/test_scripts.py -v
  ```

### Task 6 — End-to-end image smoke (Phase 1 verification — both host images build)

**Files**
- (no new files; this task exercises the artifacts built in Tasks 3 and 5)

**Steps**

This task is the Phase 1 verification gate: **prove both `--host github` and `--host ado` produce buildable images**. Phase 2 work (ado skills) must be present for the ado build; if it is not, document the gate as "Phase 1 verified for github, Phase 2 blocked on Plans 3/5".

- [ ] 1. Build the Phase 1 (github) image:
  ```
  IMG_GH=$(bash scripts/build_image.sh --host github)
  echo "Built ${IMG_GH}"
  ```
  Expected: build succeeds; tag ends in `-github`.

- [ ] 2. Build the Phase 2 (ado) image:
  ```
  IMG_ADO=$(bash scripts/build_image.sh --host ado)
  echo "Built ${IMG_ADO}"
  ```
  Expected: build succeeds; tag ends in `-ado`. **If this fails with "skills/pr-ado not found" or similar**, that's expected before Plans 3/5 Phase 2 lands. Note the gap in the PR description and proceed with Phase 1 only.

- [ ] 3. Verify both images carry the right `RALPH_GIT_HOST` env:
  ```
  docker inspect --format='{{range .Config.Env}}{{println .}}{{end}}' "${IMG_GH}" | grep RALPH_GIT_HOST
  docker inspect --format='{{range .Config.Env}}{{println .}}{{end}}' "${IMG_ADO}" | grep RALPH_GIT_HOST
  ```
  Expected: `RALPH_GIT_HOST=github` for the first; `RALPH_GIT_HOST=ado` for the second.

- [ ] 4. Verify both images carry the `ralph.git-host` OCI label:
  ```
  docker inspect --format='{{index .Config.Labels "ralph.git-host"}}' "${IMG_GH}"
  docker inspect --format='{{index .Config.Labels "ralph.git-host"}}' "${IMG_ADO}"
  ```
  Expected: prints `github` then `ado`.

- [ ] 5. Run preflight against each image. The doctor probes the host indicated by the baked env:
  ```
  bash scripts/preflight.sh "${IMG_GH}"
  bash scripts/preflight.sh "${IMG_ADO}"
  ```
  Expected (against Plan 11's real doctor): exit 0 for each.
  Expected (against the Task 1 stub doctor): exit 0 for each.

- [ ] 6. Confirm the runtime user is `ralph` (10001) inside each image:
  ```
  docker run --rm --entrypoint id "${IMG_GH}" -u
  docker run --rm --entrypoint id "${IMG_ADO}" -u
  ```
  Expected: prints `10001` for each.

- [ ] 7. Confirm the canonical skill paths exist inside each image:
  ```
  docker run --rm --entrypoint ls "${IMG_GH}" /root/.claude/skills/pr/
  docker run --rm --entrypoint ls "${IMG_GH}" /root/.claude/skills/workitem-fetch/
  docker run --rm --entrypoint ls "${IMG_ADO}" /root/.claude/skills/pr/
  docker run --rm --entrypoint ls "${IMG_ADO}" /root/.claude/skills/workitem-fetch/
  ```
  Expected: each path lists at least `SKILL.md` (the contents differ by host but both have the canonical filename).

- [ ] 8. Confirm `.claude/settings.json` is present:
  ```
  docker run --rm --entrypoint cat "${IMG_GH}" /etc/ralph/.claude/settings.json | head -5
  ```

- [ ] 9. Confirm Claude Code CLI is installed:
  ```
  docker run --rm --entrypoint claude "${IMG_GH}" --version || true
  ```

### Task 7 — Wire the GitHub Actions workflow

**Files**
- Create: `.github/workflows/ralph-image.yml`

**Steps**

- [ ] 1. Write `.github/workflows/ralph-image.yml` with the exact content from the "CI workflow" section above. The workflow uses a `matrix` over `[github, ado]` so each push builds both host images.

- [ ] 2. Parse the YAML to confirm validity:
  ```
  uv run python -c "
  import yaml
  with open('.github/workflows/ralph-image.yml') as f:
      docs = list(yaml.safe_load_all(f))
  assert docs, 'empty workflow'
  print('parsed', len(docs), 'doc(s)')
  "
  ```

- [ ] 3. Validate with `actionlint` if available:
  ```
  actionlint .github/workflows/ralph-image.yml 2>&1 || echo "actionlint not present; skipping"
  ```

- [ ] 4. The implementer SHALL list the GitHub secrets the workflow needs in `docs/deployment.md` (Task 9):
  - `RALPH_REGISTRY` — the registry prefix (e.g. an ECR URL)
  - `RALPH_REGISTRY_USER` — registry username
  - `RALPH_REGISTRY_PASSWORD` — registry password / token

### Task 8 — Validate the Azure Pipelines alternative

**Files**
- (no new files; the alternative is documented inline in `docs/deployment.md` in Task 9)

**Steps**

- [ ] 1. Confirm the Azure Pipelines snippet (which will be embedded in Task 9's runbook) is syntactically valid YAML. The exact snippet:
  ```yaml
  # Azure Pipelines alternative to .github/workflows/ralph-image.yml.
  # Builds both host images via a matrix-equivalent strategy
  # (two parallel jobs).
  trigger:
    branches:
      include:
        - main
    paths:
      include:
        - Dockerfile
        - pyproject.toml
        - ralph_executor/*
        - skills/*
        - .claude/settings.json
        - scripts/*

  pool:
    vmImage: 'ubuntu-latest'

  variables:
    RALPH_IMAGE: ralph-executor

  jobs:
    - job: build_github
      displayName: 'Build (github)'
      steps:
        - checkout: self
          fetchDepth: 0
        - bash: |
            export RALPH_VERSION="$(git rev-parse --short HEAD)"
            IMG=$(bash scripts/build_image.sh --host github)
            echo "##vso[task.setvariable variable=IMAGE]$IMG"
          displayName: 'Build image'
        - bash: bash scripts/preflight.sh "$(IMAGE)"
          displayName: 'Preflight (ralph-doctor)'
        - bash: bash scripts/build_image.sh --host github --push
          displayName: 'Push image'
          condition: succeeded()
          env:
            RALPH_REGISTRY: $(RALPH_REGISTRY)
            RALPH_VERSION: $(Build.SourceVersion)

    - job: build_ado
      displayName: 'Build (ado)'
      dependsOn: []
      steps:
        - checkout: self
          fetchDepth: 0
        - bash: |
            export RALPH_VERSION="$(git rev-parse --short HEAD)"
            IMG=$(bash scripts/build_image.sh --host ado)
            echo "##vso[task.setvariable variable=IMAGE]$IMG"
          displayName: 'Build image'
        - bash: bash scripts/preflight.sh "$(IMAGE)"
          displayName: 'Preflight (ralph-doctor)'
        - bash: bash scripts/build_image.sh --host ado --push
          displayName: 'Push image'
          condition: succeeded()
          env:
            RALPH_REGISTRY: $(RALPH_REGISTRY)
            RALPH_VERSION: $(Build.SourceVersion)
  ```
  Paste this snippet into the runbook in Task 9 verbatim.

- [ ] 2. The alternative is a NICE TO HAVE. Plan 12 does not commit `azure-pipelines.yml` itself.

### Task 9 — Write the deployment runbook (Phase 1 / Phase 2 branches)

**Files**
- Create: `docs/deployment.md`

**Steps**

- [ ] 1. Write `docs/deployment.md` with the structure below. The runbook is **branching** — it asks the reader which phase they are deploying and then takes them down a self-contained path. The implementer SHALL write the actual prose; the headings and the content checklist under each are non-negotiable.

  ```markdown
  # Ralph Deployment Runbook

  ## Which phase are you deploying?

  Ralph supports two git-host backends, and you must pick one before
  building or deploying. The image you build is host-specific —
  there is no host-agnostic image.

  **Phase 1 — GitHub (at home / dogfooding):**
    Use this if Ralph will operate on GitHub repos. The image tag
    will be `ralph-executor:<ver>-github`. The skills baked in are
    `pr-github` and `workitem-fetch-github`. The auth secrets you
    need are `ANTHROPIC_API_KEY`, `GH_TOKEN`, `GH_OWNER`. Continue
    to the [Phase 1 path](#phase-1--github-deployment) below.

  **Phase 2 — ADO (at work / production):**
    Use this if Ralph will operate on Azure DevOps repos. The image
    tag will be `ralph-executor:<ver>-ado`. The skills baked in are
    `pr-ado` and `workitem-fetch-ado`. The auth secrets you need
    are `ANTHROPIC_API_KEY`, `ADO_PAT`, `ADO_ORG_URL`, `ADO_PROJECT`.
    Continue to the [Phase 2 path](#phase-2--ado-deployment) below.

  Both phases share the SAME Dockerfile, manifests, and runbook
  steps — only the `--host` build arg and the auth secret keys
  differ. The host is baked into the image at build time via the
  Dockerfile's `ARG RALPH_GIT_HOST` and persisted as `ENV
  RALPH_GIT_HOST`; ralph-doctor and the executor read it from
  there. You CANNOT switch a deployment's host at runtime —
  redeploy with a host-matching image.

  ## Common prerequisites (both phases)

  - `docker` with BuildKit enabled.
  - `kubectl` 1.30+ (or `oc` for ROSA).
  - Access to the target cluster's `ralph` namespace.
  - For CI: the GitHub Actions secrets `RALPH_REGISTRY`,
    `RALPH_REGISTRY_USER`, `RALPH_REGISTRY_PASSWORD`.
  - Cluster preconditions: the `ralph` namespace exists; the
    cluster has PodSecurity restricted-or-baseline enforcement.

  Note any Plan 7 / Plan 11 stubs still in place — the preflight
  gate is meaningful only after both plans land.

  ## Choosing a mode (long-running Deployment vs Coder Job)

  Independent of the host choice, you also pick a deployment mode:

  | Question | Long-running Deployment | Coder task-pod Job |
  |---|---|---|
  | Are PBIs continuous or bursty? | continuous | bursty |
  | Does the org use Coder workspaces? | optional | required |
  | Idle compute tolerance | acceptable | unacceptable |
  | Need rolling restarts? | yes | no |

  Recommend Deployment for the first ROSA rollout.

  ---

  ## Phase 1 — GitHub deployment

  This is the "at home" path. It is the recommended first
  deployment because the Phase 1 skills (`pr-github`,
  `workitem-fetch-github`) are delivered by Plans 5 and 3 and
  are immediately available.

  ### Phase 1: building the image

  Local build (host suffix is required — the script exits 2 if
  you forget):

      RALPH_VERSION=$(git rev-parse --short HEAD) \
      bash scripts/build_image.sh --host github

  Build + push:

      RALPH_REGISTRY=123456789012.dkr.ecr.eu-west-2.amazonaws.com \
      RALPH_VERSION=$(git rev-parse --short HEAD) \
      bash scripts/build_image.sh --host github --push

  CI: `.github/workflows/ralph-image.yml` builds the github image
  automatically as part of its matrix.

  ### Phase 1: pre-deploy gate

      IMG=$(bash scripts/build_image.sh --host github)
      bash scripts/preflight.sh "$IMG"

  The doctor reads `RALPH_GIT_HOST` from the image's baked env
  and probes only the GitHub auth path (`GH_TOKEN`, `GH_OWNER`).
  Expected exit 0. If exit 2, ralph-doctor FAILED — DO NOT
  promote the image. Read the doctor JSON report on stderr.

  ### Phase 1: secrets handling

  The Secret carries shared and host-specific keys; for Phase 1
  populate ANTHROPIC_API_KEY, GH_TOKEN, GH_OWNER. Leave the
  ADO_* sentinels alone (they're documented as unused for github
  deployments but the manifest still carries them as placeholders).

      kubectl create secret generic ralph-secrets \
        --namespace ralph \
        --from-literal=ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
        --from-literal=GH_TOKEN="$GH_TOKEN" \
        --from-literal=GH_OWNER="myorg"

  Alternatively, use External Secrets Operator (preferred for
  production) backed by AWS Secrets Manager. Document the
  ExternalSecret manifest in your service overlay. SealedSecrets
  is also supported for self-hosted clusters.

  ### Phase 1: applying manifests

      kubectl apply -f manifests/ralph-rbac.yaml
      kubectl apply -f manifests/ralph-configmap.yaml
      sed "s|__IMAGE__|$IMG|" manifests/ralph-deployment.yaml \
        | kubectl apply -f -
      kubectl rollout status deploy/ralph-executor -n ralph

  Job mode:

      sed -e "s|__IMAGE__|$IMG|" \
          -e "s|__PBI_ID__|$PBI_ID|" \
          manifests/ralph-job.yaml | kubectl apply -f -

  ### Phase 1: verifying the pod

      kubectl get pods -n ralph
      kubectl logs -n ralph deploy/ralph-executor --tail=50
      kubectl exec -n ralph deploy/ralph-executor -- ralph-executor health --ready
      # Confirm the host is github:
      kubectl exec -n ralph deploy/ralph-executor -- env | grep RALPH_GIT_HOST
      # Expected: RALPH_GIT_HOST=github

  ### Phase 1: troubleshooting

  Common failures:

  - ImagePullBackOff   → check RALPH_REGISTRY auth; describe pod.
  - CrashLoopBackOff   → kubectl logs --previous.
  - "401 Unauthorized" in logs → GH_TOKEN missing or expired;
    re-create the Secret with a fresh PAT.
  - "GH_OWNER not set" → forgot to populate the Secret key.
  - claude -p prompted for permission → doctor drift; rerun
    preflight and fix .claude/settings.json.

  ---

  ## Phase 2 — ADO deployment

  This is the "at work" path. It requires Plans 2/3/5 Phase 2 to
  have completed (`pr-ado` and `workitem-fetch-ado` skill
  directories must exist on disk).

  ### Phase 2: building the image

  Local build:

      RALPH_VERSION=$(git rev-parse --short HEAD) \
      bash scripts/build_image.sh --host ado

  If you get `COPY failed: skills/pr-ado: no such file or
  directory`, Phase 2 skills have not landed yet — check Plan
  status in the orchestrator.

  Build + push:

      RALPH_REGISTRY=123456789012.dkr.ecr.eu-west-2.amazonaws.com \
      RALPH_VERSION=$(git rev-parse --short HEAD) \
      bash scripts/build_image.sh --host ado --push

  ### Phase 2: pre-deploy gate

      IMG=$(bash scripts/build_image.sh --host ado)
      bash scripts/preflight.sh "$IMG"

  The doctor reads `RALPH_GIT_HOST=ado` from the image's baked
  env and probes the ADO auth path (`ADO_PAT`, `ADO_ORG_URL`,
  `ADO_PROJECT`).

  ### Phase 2: secrets handling

      kubectl create secret generic ralph-secrets \
        --namespace ralph \
        --from-literal=ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
        --from-literal=ADO_PAT="$ADO_PAT" \
        --from-literal=ADO_ORG_URL="https://dev.azure.com/myorg" \
        --from-literal=ADO_PROJECT="myproject"

  ### Phase 2: applying manifests

  Identical to Phase 1, but substitute the ADO-tagged image:

      sed "s|__IMAGE__|$IMG|" manifests/ralph-deployment.yaml \
        | kubectl apply -f -

  ### Phase 2: verifying the pod

      kubectl exec -n ralph deploy/ralph-executor -- env | grep RALPH_GIT_HOST
      # Expected: RALPH_GIT_HOST=ado

  ### Phase 2: troubleshooting

  - "skills/pr-ado not found" at build time → Plans 3/5 Phase 2
    not yet complete.
  - "401 Unauthorized" calling ADO → ADO_PAT missing, expired,
    or wrong scope; regenerate with vso.code_full.
  - "Project not found" → ADO_PROJECT name mismatch.

  ---

  ## IAM and IRSA (both phases)

  The ralph ServiceAccount carries an annotation slot for IRSA.
  Step-by-step:

  1. Create the IAM role with a trust policy for the cluster's
     OIDC provider, namespace=ralph, sa=ralph.
  2. Attach a policy granting whatever AWS APIs Ralph needs
     (in v1 — none, unless using Secrets Manager).
  3. After applying manifests/ralph-rbac.yaml, annotate the SA:

      kubectl annotate sa ralph -n ralph \
        eks.amazonaws.com/role-arn=arn:aws:iam::123456789012:role/ralph-executor

  ## Alternative: Azure Pipelines

  The team's standard is GitHub Actions
  (.github/workflows/ralph-image.yml). For teams using ADO
  Pipelines instead, this YAML is functionally equivalent
  (builds BOTH host images as separate jobs):

      [paste the snippet from Task 8 here verbatim]

  ## Runtime-staging alternative (single image, host chosen at startup)

  An alternative deployment model — supported by Plan 7's
  `host_select.py` — is to ship a single host-agnostic image
  carrying BOTH `pr-github/` AND `pr-ado/` skill bundles, then
  at pod startup let `host_select.py` read `RALPH_GIT_HOST` from
  the ConfigMap and symlink the chosen skill into
  `~/.claude/skills/pr/`. This is supported but is NOT the
  default because:

  - it adds a startup step that can fail (the staging logic);
  - it weakens the image manifest (an image no longer documents
    exactly one host);
  - it requires the ConfigMap to set `RALPH_GIT_HOST` (the
    build-time model treats RALPH_GIT_HOST as immutable per image);
  - the auth-env-var contract becomes a runtime check rather
    than a build-time check.

  To opt into runtime staging, the operator:
  1. Modifies the Dockerfile to copy BOTH host bundles into
     /opt/ralph/skills/ without re-copying to /root/.claude/skills.
  2. Adds RALPH_GIT_HOST to the ConfigMap (not the image ENV).
  3. Configures host_select.py to run at startup via an entrypoint
     wrapper.

  Plan 7 documents the contract for `host_select.py`. Operators
  who want a single dual-host image should follow that path.

  ## Plan 7 / 11 stub assumptions

  Until Plan 7 ships the real ralph-executor health/run commands
  and Plan 11 ships the real doctor, the preflight gate is
  meaningful only after both plans land. Refer the operator to
  the orchestrator plan for status.
  ```

- [ ] 2. The runbook MUST cover (checklist):
  - "Which phase are you deploying?" intro with a clear Phase 1 vs Phase 2 branch
  - Common prerequisites
  - Choosing a mode (Deployment vs Job)
  - Phase 1: building, preflight, secrets, applying manifests, verifying, troubleshooting
  - Phase 2: same set, with ADO-specific commands and secrets
  - IAM and IRSA (shared)
  - Azure Pipelines alternative
  - Runtime-staging alternative (host_select.py)
  - Plan 7 / 11 stub callout

- [ ] 3. Re-render the runbook locally and confirm headings:
  ```
  uv run python -c "
  from pathlib import Path
  text = Path('docs/deployment.md').read_text(encoding='utf-8')
  required = [
      '## Which phase are you deploying?',
      '## Common prerequisites',
      '## Choosing a mode',
      '## Phase 1',
      '### Phase 1: building',
      '### Phase 1: pre-deploy gate',
      '### Phase 1: secrets handling',
      '### Phase 1: applying manifests',
      '### Phase 1: verifying',
      '### Phase 1: troubleshooting',
      '## Phase 2',
      '### Phase 2: building',
      '### Phase 2: pre-deploy gate',
      '### Phase 2: secrets handling',
      '### Phase 2: applying manifests',
      '### Phase 2: verifying',
      '### Phase 2: troubleshooting',
      '## IAM and IRSA',
      '## Alternative: Azure Pipelines',
      '## Runtime-staging alternative',
  ]
  missing = [h for h in required if h not in text]
  assert not missing, f'missing headings: {missing}'
  print('runbook OK')
  "
  ```

### Task 10 — Verification gate (full)

**Files**
- (no new files)

**Steps**

- [ ] 1. Run the full packaging test set:
  ```
  uv run pytest tests/packaging/ -v
  ```
  Expected: every test passes.

- [ ] 2. Run the host-specific Phase 1 verification (the new gate this plan adds):
  ```
  bash scripts/build_image.sh --host github
  ```
  Expected: build succeeds; image tag ends in `-github`.

- [ ] 3. Run the host-specific Phase 2 verification:
  ```
  bash scripts/build_image.sh --host ado
  ```
  Expected: build succeeds AND tag ends in `-ado` (if Phase 2 skills are present), OR fails cleanly at the COPY step with "skills/pr-ado: no such file or directory" (if Phase 2 skills have not landed yet — note this gap in the PR description rather than treating it as a Plan 12 failure).

- [ ] 4. Run the orchestrator's verification gate command. **Note:** the gate command in the orchestrator is `docker build -t ralph:test .` — this Plan 12 amendment REPLACES that simple gate with a host-aware version:
  ```
  bash scripts/build_image.sh --host github && \
    docker images | grep "ralph-executor.*-github"
  ```
  Expected: image builds, `docker images` shows a `-github`-tagged ralph row. This is the canonical Phase 1 verification gate.

- [ ] 5. Run preflight against the gate-built image. The doctor (real or stub) reads `RALPH_GIT_HOST` from the image's baked env:
  ```
  IMG=$(bash scripts/build_image.sh --host github)
  bash scripts/preflight.sh "${IMG}"
  ```
  Expected: exit 0.

- [ ] 6. Confirm the full repo gate is green:
  ```
  uv run ruff check . && uv run ruff format --check . && uv run mypy ralph_executor && uv run pytest
  ```
  Expected: every command exits 0.

- [ ] 7. Self-review checklist. The implementer SHALL confirm each line before declaring the task complete:
  - [ ] No `__IMAGE__` / `__PBI_ID__` placeholder appears outside the manifests where it is intended as a deploy-time substitution marker.
  - [ ] No secret value appears in any committed file (grep for `ANTHROPIC_API_KEY=` / `ADO_PAT=` / `GH_TOKEN=` returns only the template sentinels and the runbook references).
  - [ ] Every file listed in "File Structure" exists and is referenced by at least one test or by the runbook.
  - [ ] The runbook clearly branches at the top: a reader picks Phase 1 or Phase 2 and sees a self-contained path. The Phase headers are present in the rendered Markdown.
  - [ ] The Dockerfile's `ARG RALPH_GIT_HOST` has no default and the guard `RUN test -n` fires when the build arg is missing.
  - [ ] `scripts/build_image.sh --host` is REQUIRED — exit 2 when missing.
  - [ ] `scripts/build_image.sh --host gitlab` (or other invalid values) exits 2.
  - [ ] The image tag carries the host suffix in both local and registry forms.
  - [ ] The image's baked `ENV RALPH_GIT_HOST` matches the `--host` flag used to build it.
  - [ ] The `ralph.git-host` OCI label matches the host.
  - [ ] The runtime-staging alternative is documented in the runbook (referencing Plan 7's `host_select.py`).
  - [ ] The plan 7 / 11 stub assumption is called out in `docs/deployment.md`.

- [ ] 8. Commit each task's output as a separate conventional-commit. Commits this plan produces (in order):
  - `chore(packaging): scaffold packaging tests and console-script entry`
  - `feat(packaging): bake .claude/settings.json with canonical skill names`
  - `feat(packaging): multi-stage Dockerfile with RALPH_GIT_HOST build arg`
  - `feat(packaging): k8s manifests covering both host phases`
  - `feat(packaging): build_image.sh requires --host github|ado`
  - `ci(packaging): GitHub Actions matrix builds both host images`
  - `docs(packaging): deployment runbook with Phase 1 / Phase 2 branches`

  Do not combine commits across tasks.

---

## Verification gate (orchestrator-defined, amended)

The orchestrator's original gate command was:

```
docker build -t ralph:test . && docker images | grep ralph
```

This plan REPLACES that with a host-aware Phase 1 gate:

```
bash scripts/build_image.sh --host github && docker images | grep "ralph-executor.*-github"
```

Plus a Phase 2 gate when Plans 3/5 Phase 2 have landed:

```
bash scripts/build_image.sh --host ado && docker images | grep "ralph-executor.*-ado"
```

Expected: each host image builds; `docker images` shows a host-suffixed `ralph-executor` row. Task 10 runs these verbatim.

Additional gates this plan adds:

- `uv run pytest tests/packaging/ -v` — every packaging test passes.
- `bash scripts/preflight.sh <host-image>` — preflight exits 0 against the host-built image.
- The build script exits non-zero with usage error if `--host` is omitted.

---

## Open questions for the implementer (NOT blockers)

The implementer SHALL surface these in the PR description rather than guess silently:

1. **Registry choice.** The runbook assumes ECR. If the team has standardised on GHCR / ACR / a private registry, the runbook's "Phase 1 / Phase 2: building the image" sections need a thin overlay. The Dockerfile and manifests are registry-agnostic.
2. **Phase 2 skill readiness.** If `skills/pr-ado/` and `skills/workitem-fetch-ado/` are not present at the time this plan executes, the Phase 2 image build will fail at the COPY step. That's the right failure mode but the implementer SHALL note in the PR description that Phase 2 verification is pending Plans 3/5.
3. **PodSecurity mode.** The manifests assume PodSecurity restricted-or-baseline.
4. **Logging stack.** The manifests do not declare a logging sidecar.
5. **Claude Code CLI version pin.** The Dockerfile pins `@anthropic-ai/claude-code@1.0.0`. Replace with the team's current standard.
6. **`uv` version pin.** The Dockerfile copies from `ghcr.io/astral-sh/uv:0.4.30`.
7. **Runtime-staging opt-in.** Teams that want a single dual-host image can follow the alternative documented in the runbook and Plan 7's `host_select.py` contract. The implementer SHALL NOT switch this plan's default model without a follow-up plan documenting the tradeoff.
