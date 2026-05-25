# ROSA Packaging Plan (Plan 12 of 13)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. This is a devops-flavoured plan — the TDD discipline is adapted: build the artifact, drive a deterministic smoke command against it, assert exit 0 / expected output. Every artifact (Dockerfile, manifest, CI workflow) has a corresponding verification step.

**Goal:** Package `ralph-executor` for deployment onto ROSA (Red Hat OpenShift on AWS), supporting BOTH operational models the spec admits — a long-running k8s Deployment that loops continuously over a service repo, and a Job-based Coder workspaces "task pod" that handles a single PBI per pod invocation. Produce a reproducible container image (multi-stage Dockerfile), the k8s manifests for both modes, a non-secret ConfigMap, a template Secret manifest, a minimal RBAC scaffold, build/push scripts, a pre-deploy CI gate that runs `ralph-doctor` against the just-built image, and a runbook (`docs/deployment.md`) that tells an operator how to choose a mode and how to deploy it. The image's baked `.claude/settings.json` enables `--dangerously-skip-permissions` AND explicitly allows every tool Ralph needs (per Spec "Local vs ROSA differences"). The pre-deploy gate is non-optional: Spec section "ralph-doctor checks" requires it.

**Architecture:** Multi-stage Dockerfile on `python:3.12-slim-bookworm` — a `builder` stage installs `uv`, syncs the locked Python dependencies from `pyproject.toml` / `uv.lock`, and builds the `ralph_executor` wheel; a `runtime` stage installs Node.js (for `@anthropic-ai/claude-code`), copies the virtualenv and wheel from `builder`, installs `claude-code` via `npm`, copies the on-repo `skills/` tree to `/opt/ralph/skills`, copies a baked `.claude/settings.json` to `/etc/ralph/.claude/settings.json`, creates a non-root `ralph` user (UID 10001), and sets `ENTRYPOINT ["ralph-executor"]`. The image is intentionally agnostic of operational mode — the k8s artifact selects mode. `manifests/ralph-deployment.yaml` is the long-running mode (replicas: 1, liveness/readiness on `ralph-executor health` — stubbed if Plan 7 hasn't exposed it yet; documented assumption). `manifests/ralph-job.yaml` is the Coder task-pod mode (`restartPolicy: Never`, `ttlSecondsAfterFinished: 3600`, a `RALPH_RUN_ONCE=true` env var documenting the executor switch that the Job mode relies on — also covered in the assumption ledger). `manifests/ralph-configmap.yaml` carries non-secret env (`RALPH_REPO_URL`, `RALPH_QUEUE_BRANCH`, `RALPH_MAIN_BRANCH`, `ANTHROPIC_MODEL`, `RALPH_LOG_LEVEL`). `manifests/ralph-secrets.template.yaml` is the TEMPLATE (with placeholder values that must fail apply if not substituted) for `anthropic-api-key`, `ado-pat`. `manifests/ralph-rbac.yaml` carries a `ServiceAccount` plus an optional `Role` / `RoleBinding` scaffold; IAM via IRSA is mentioned in the runbook but configured out-of-band. `scripts/build_image.sh` reads a version (git short SHA + a manual `RALPH_VERSION` env override) and runs `docker build` with the appropriate tags. `scripts/preflight.sh` starts a container from the just-built image, runs `ralph-doctor` inside it via `docker exec`, captures the exit code, and propagates it. The CI gate (chosen: GitHub Actions; Azure Pipelines documented as an alternative in the runbook) calls `scripts/build_image.sh` then `scripts/preflight.sh`.

**Tech Stack:** Docker (BuildKit), Python 3.12, `uv`, Node.js 22 LTS (for Claude Code CLI), `@anthropic-ai/claude-code` (npm), `kubectl` 1.30+ (or `oc` for ROSA), Kubernetes 1.30+, ROSA + Coder workspaces, GitHub Actions (primary CI path), Azure Pipelines (documented alternative), `shellcheck` (for the bash scripts).

---

## File Structure

| Path | Responsibility |
|---|---|
| `Dockerfile` | Multi-stage image build. Builder installs `uv` and Python deps; runtime image carries the venv, the `ralph_executor` wheel, the `skills/` tree, the baked `.claude/settings.json`, Node.js + Claude Code CLI, and a non-root `ralph` user. `ENTRYPOINT ["ralph-executor"]`. |
| `.dockerignore` | Excludes everything that is not needed in the build context: `.git/`, `.venv/`, `dist/`, `build/`, `__pycache__/`, `.pytest_cache/`, `.ruff_cache/`, `.mypy_cache/`, `node_modules/`, `tests/`, `docs/`, `samples/`, IDE files, OS junk. Keeps the build context small and deterministic. |
| `manifests/ralph-deployment.yaml` | k8s Deployment for long-running pod mode. `replicas: 1`. `securityContext` non-root, `runAsUser: 10001`, `readOnlyRootFilesystem: true`, `allowPrivilegeEscalation: false`. CPU/memory requests + limits. `readinessProbe` + `livenessProbe` invoke `ralph-executor health` (Plan 7 to expose; documented assumption). Mounts `ralph-config` ConfigMap and `ralph-secrets` Secret as env. `serviceAccountName: ralph`. |
| `manifests/ralph-job.yaml` | k8s Job for Coder task-pod mode. `restartPolicy: Never`. `backoffLimit: 0`. `ttlSecondsAfterFinished: 3600`. `RALPH_RUN_ONCE=true` env asserted. Same `securityContext`, ConfigMap, Secret, and ServiceAccount as the Deployment. |
| `manifests/ralph-rbac.yaml` | `ServiceAccount: ralph` + an opt-in `Role` and `RoleBinding` granting `get` / `list` on `configmaps`, `secrets` (scoped to `ralph-config` / `ralph-secrets` only). IAM (via IRSA — annotating the SA with `eks.amazonaws.com/role-arn`) is documented in the runbook but the annotation slot is present in the manifest as a `# TODO` comment. |
| `manifests/ralph-secrets.template.yaml` | TEMPLATE Secret manifest. `metadata.name: ralph-secrets`. Keys `ANTHROPIC_API_KEY` and `ADO_PAT` carry literal sentinel values (`__REPLACE_WITH_ANTHROPIC_API_KEY__` and `__REPLACE_WITH_ADO_PAT__`) and a leading `# RALPH-TEMPLATE-DO-NOT-APPLY-AS-IS` comment so a `kubectl apply` against an unsubstituted file fails the preflight script. |
| `manifests/ralph-configmap.yaml` | `metadata.name: ralph-config`. Non-secret env values: `RALPH_REPO_URL`, `RALPH_QUEUE_BRANCH=ralph-queue`, `RALPH_MAIN_BRANCH=main`, `ANTHROPIC_MODEL=claude-opus-4-7`, `RALPH_LOG_LEVEL=INFO`, `RALPH_RUN_ONCE=false` (overridden to `true` by the Job manifest). |
| `scripts/build_image.sh` | Bash script. Computes a tag from `RALPH_VERSION` (env override) or `git rev-parse --short HEAD`. Tags the image `ralph-executor:<tag>` and `ralph-executor:latest`. Honours `RALPH_REGISTRY` (e.g. `123.dkr.ecr.eu-west-2.amazonaws.com`) for the push path. Prints the final fully-qualified tag on the last line of stdout so callers can pipe it. |
| `scripts/preflight.sh` | Bash script. Takes one positional arg: the image tag to test. Runs `docker run --rm <tag> ralph-executor doctor --json`. Asserts exit code 0. On non-zero, dumps the captured stdout/stderr for diagnostics and exits with the same code. |
| `.claude/settings.json` | The settings.json baked into the image at `/etc/ralph/.claude/settings.json`. Lists `--dangerously-skip-permissions: true` and a `permissions.allow` entry for every tool Ralph might call. Has a `# COMMENT` field in description to make its origin obvious if cat'd inside the container. |
| `.github/workflows/ralph-image.yml` | GitHub Actions workflow. On push to `main` and on tag `v*`: builds the image, runs `scripts/preflight.sh` against it, pushes to the registry on success. Includes a manual-dispatch (`workflow_dispatch`) entry point so an operator can rebuild on demand. |
| `docs/deployment.md` | Operator runbook. Covers prerequisites, choosing between Deployment and Job mode, the build/push flow, applying manifests, verifying the pod starts, retrieving logs, common failure modes (image pull, missing secret, doctor failed, claude-p prompted for permission), and the Azure Pipelines alternative for the pre-deploy gate. |
| `pyproject.toml` | (Modify) Register a `ralph-executor` console script in `[project.scripts]` so the image's `ENTRYPOINT ["ralph-executor"]` resolves. Plan 7 owns the script itself; this plan asserts the registration. |
| `tests/packaging/__init__.py` | Empty marker. |
| `tests/packaging/test_dockerfile.py` | Pytest tests: Dockerfile parses (using `dockerfile-parse` library), declares both build stages, sets a non-root USER, declares ENTRYPOINT exactly as `["ralph-executor"]`, does not run as root, exposes no shell-form RUN that contains hard-coded secrets, COPYs the baked settings.json. |
| `tests/packaging/test_manifests.py` | Pytest tests: every YAML in `manifests/` parses; deployment.yaml's `securityContext` is non-root, has resource limits, and references the secrets/configmap names exactly; job.yaml has `restartPolicy: Never`, `ttlSecondsAfterFinished`, `backoffLimit: 0`, and the `RALPH_RUN_ONCE=true` env; the template secret carries the `RALPH-TEMPLATE-DO-NOT-APPLY-AS-IS` sentinel; RBAC references the `ralph` service account. |
| `tests/packaging/test_settings_json.py` | Pytest tests: the baked `.claude/settings.json` parses; `permissions.allow` includes Bash, Edit, Write, Read, Grep, Glob, Task, TodoWrite, the ado-pr skill, the supervisor skills, and the better-memory MCP tools list is present even though commented out (v2). `dangerously-skip-permissions` is `true`. |
| `tests/packaging/test_scripts.py` | Pytest tests: `scripts/build_image.sh` and `scripts/preflight.sh` exist, are executable, and pass `shellcheck` (subprocess-shelled; skipped with a clear message if shellcheck is not installed). The build script's `--help` exits 0. |

---

## Cross-plan assumptions ledger

This plan depends on capabilities introduced earlier in the orchestrator. Several of those plans are still in flight at the time of writing; the plan documents each assumption so the implementer can decide whether to stub or to wait.

| Assumption | Source | If not yet true |
|---|---|---|
| `ralph-executor` is installable as a wheel with a console-script entry point named `ralph-executor`. | Plan 7 | Implementer SHALL add a temporary stub `ralph_executor/cli.py` that prints "stub" and exits 0 — the Dockerfile builds against this until Plan 7 lands. Note the stub in `docs/deployment.md`. |
| `ralph-executor health` subcommand returns exit 0 + a JSON `{"ok": true}` blob when the executor is healthy. | Plan 7 (sub-task) | Same — temporary stub returning `{"ok": true}` is acceptable. The probe configs in the Deployment manifest already reference the subcommand; once Plan 7 ships the real health check, no manifest changes are required. |
| `ralph-executor doctor` subcommand runs `ralph-doctor` checks inline and exits 0 on pass. | Plan 11 | Same — temporary stub returning exit 0. The preflight script (`scripts/preflight.sh`) will succeed trivially against the stub; once Plan 11 ships real checks, the gate becomes meaningful. The runbook MUST flag this so operators don't ship while the stub is in place. |
| `ralph-executor` honours `RALPH_RUN_ONCE=true` to process one PBI and exit (used by Job mode). | Plan 7 | Implementer SHALL document this as a Plan 7 follow-up if not already present; the manifest still sets the env (so the contract is visible) but the runbook warns that Job mode is inert until Plan 7 honours the flag. |
| `skills/` tree exists at the repo root with `ado-pr/`, `ralph-add/`, `ralph-status/`, `ralph-cancel/`, `ralph-promote/`, `ralph-triage/`, `ralph-doctor/` subdirectories. | Plans 3, 4, 5, 10, 11 | If any are missing at image-build time, the Dockerfile's `COPY skills/ /opt/ralph/skills/` still succeeds (it copies whatever exists). The runbook explicitly lists which skills the image expects and instructs the operator to rebuild after the corresponding plan lands. |

The implementer SHOULD NOT block this plan on Plans 7 / 11 / 10 — the stubs and the assumption ledger let the packaging artifacts land and be verifiable in isolation.

---

## Dockerfile (full content the implementer writes)

The Dockerfile is multi-stage. Stage 1 builds the wheel; stage 2 is the runtime image. The exact contents are inlined here so the implementer does not have to invent them:

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
# --frozen ensures we don't drift from uv.lock.
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
# ralph_executor wheel, the skills tree, and the baked
# .claude/settings.json. Runs as a non-root user.
# ============================================================
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/ralph/venv/bin:/usr/local/bin:/usr/bin:/bin" \
    CLAUDE_CONFIG_DIR=/etc/ralph/.claude \
    RALPH_LOG_LEVEL=INFO

# Runtime system deps. git is required because the executor pulls
# the project repo at startup. ca-certificates is required for
# HTTPS to Anthropic / ADO. curl is for Node install.
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
# UID 10001 is well outside the OS-reserved range and stable for
# any IRSA / PodSecurity scrutiny.
RUN groupadd --system --gid 10001 ralph \
 && useradd  --system --uid 10001 --gid 10001 \
        --home-dir /home/ralph --create-home \
        --shell /usr/sbin/nologin ralph \
 && mkdir -p /opt/ralph /etc/ralph/.claude /var/ralph \
 && chown -R ralph:ralph /opt/ralph /etc/ralph /var/ralph

WORKDIR /opt/ralph

# Bring the virtualenv from the builder. The venv is self-contained;
# we don't need uv at runtime.
COPY --from=builder --chown=ralph:ralph /build/.venv /opt/ralph/venv

# Install the ralph_executor wheel into the venv.
COPY --from=builder /build/dist/*.whl /tmp/
RUN /opt/ralph/venv/bin/pip install --no-deps --no-cache-dir /tmp/*.whl \
 && rm /tmp/*.whl

# Copy the skills tree. The image is shipped with every Ralph skill
# baked in so claude -p can discover them without a network pull.
COPY --chown=ralph:ralph skills/ /opt/ralph/skills/

# Copy the baked .claude/settings.json. This is the contract:
# --dangerously-skip-permissions + an explicit allow list. See
# the file's leading comment for an explanation.
COPY --chown=ralph:ralph .claude/settings.json /etc/ralph/.claude/settings.json

# Re-assert non-root, declare the working directory the executor
# should use as its scratch / repo-clone area.
USER ralph
WORKDIR /var/ralph

# OCI labels for image traceability.
LABEL org.opencontainers.image.title="ralph-executor" \
      org.opencontainers.image.description="Ralph v1 per-repo executor" \
      org.opencontainers.image.source="https://github.com/emp3thy/ralph" \
      org.opencontainers.image.licenses="MIT"

# Default to the long-running executor entrypoint. The Job manifest
# overrides args (or sets RALPH_RUN_ONCE=true) for task-pod mode.
ENTRYPOINT ["ralph-executor"]
CMD ["run"]
```

The implementer SHALL write this file verbatim, only changing the `@anthropic-ai/claude-code@1.0.0` pin if a newer version is the team's current standard at build time.

---

## `.claude/settings.json` (full content the implementer writes)

The settings.json baked into the image is the load-bearing artifact for Spec section "Local vs ROSA differences" — Ralph cannot prompt for a permission in a pod. The implementer SHALL write this file verbatim:

```json
{
  "_comment": "Ralph v1 baked settings.json. DO NOT add interactive permissions here. dangerouslySkipPermissions is intentional and is paired with an explicit permissions.allow list for every tool Ralph might call. The corresponding ralph-doctor check enforces parity with this file.",
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
      "Skill(ado-pr)",
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
- `_comment` and `_v2_memory_mcp_permissions_commented_out` are NON-CANONICAL keys (leading underscore convention). Claude Code ignores them. They exist so a human cat'ing the file inside the container immediately understands its purpose and what is reserved for v2.
- The `deny` list is intentionally minimal. It catches catastrophic Bash invocations only; further deny entries belong in a service-specific overlay (out of scope for v1).

---

## Manifests (full content the implementer writes)

### `manifests/ralph-deployment.yaml`

```yaml
# Long-running pod mode.
# Use when the team's ROSA cluster is the durable home of Ralph
# and the executor loops continuously over a single service repo.
# See docs/deployment.md ("Choosing a mode") before applying.
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
          # Replaced at deploy time. CI substitutes the freshly
          # built tag; manual operators substitute via sed.
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

Note on the `claude-config` volume: the manifest mounts a `ConfigMap` over the image's `/etc/ralph/.claude`, allowing an operator to override the baked settings.json without rebuilding the image. The Task list below includes a step to create that ConfigMap from the baked file (so the default is byte-identical to the bake) — the override mechanism is a knob, not a divergence.

### `manifests/ralph-job.yaml`

```yaml
# Coder workspaces task-pod mode.
# Use when a separate scheduler (Coder workspaces task system)
# dispatches one pod per PBI and the executor processes the
# single PBI then exits. See docs/deployment.md ("Choosing a mode").
#
# The Job is INTENTIONALLY a template. A scheduler clones this
# YAML, fills in the `__PBI_ID__` placeholder (used in the Job
# name suffix and as an env var) and applies it. The manifest is
# therefore not directly applicable as-is; a leading sentinel
# comment marks it as a template so preflight catches accidental
# direct application.
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
apiVersion: v1
kind: ConfigMap
metadata:
  name: ralph-config
  namespace: ralph
  labels:
    app.kubernetes.io/name: ralph-executor
    app.kubernetes.io/part-of: ralph
data:
  RALPH_REPO_URL: "https://dev.azure.com/example-org/example-project/_git/example-service"
  RALPH_QUEUE_BRANCH: "ralph-queue"
  RALPH_MAIN_BRANCH: "main"
  ANTHROPIC_MODEL: "claude-opus-4-7"
  RALPH_LOG_LEVEL: "INFO"
  RALPH_RUN_ONCE: "false"
  ADO_ORG_URL: "https://dev.azure.com/example-org"
  ADO_PROJECT: "example-project"
---
# The baked .claude/settings.json projected as a ConfigMap so
# operators can override the image's defaults without rebuilding.
# The data MUST match /etc/ralph/.claude/settings.json in the
# image. Plan 12 task "Bake settings.json" creates both from the
# same source.
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
      "_comment": "Ralph v1 baked settings.json. Mirror of the image's /etc/ralph/.claude/settings.json. Override at deploy time only if you know exactly what you are doing.",
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
          "Skill(ado-pr)",
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
#     --from-literal=ANTHROPIC_API_KEY="$(read -s; echo $REPLY)" \
#     --from-literal=ADO_PAT="$(read -s; echo $REPLY)"
#
# OR via External Secrets Operator / SealedSecrets / etc. See
# docs/deployment.md ("Secrets handling") for the supported paths.
#
# scripts/preflight.sh refuses to proceed if these sentinels are
# present in a manifest about to be applied.
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
  ANTHROPIC_API_KEY: "__REPLACE_WITH_ANTHROPIC_API_KEY__"
  ADO_PAT: "__REPLACE_WITH_ADO_PAT__"
```

### `manifests/ralph-rbac.yaml`

```yaml
# Minimal RBAC. The executor itself does not call the kube API in
# v1, but the ServiceAccount is the IRSA anchor (annotate it with
# eks.amazonaws.com/role-arn at deploy time). The Role + Binding
# grant scoped read access to the executor's own ConfigMap and
# Secret as a defence against accidentally-broad bindings creeping
# in via Helm charts / overlays in future.
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
# build_image.sh — build the ralph-executor container image with a
# deterministic tag. Honours:
#   RALPH_VERSION  — explicit tag (defaults to `git rev-parse --short HEAD`)
#   RALPH_REGISTRY — fully-qualified registry prefix (e.g. an ECR URL)
#   RALPH_IMAGE    — image name (defaults to `ralph-executor`)
#
# On success the last line of stdout is the fully-qualified tag of
# the image that was just built, so callers can pipe it into the
# preflight script:
#
#   IMG=$(scripts/build_image.sh)
#   scripts/preflight.sh "$IMG"
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: build_image.sh [--push] [--help]

Environment:
  RALPH_VERSION   Explicit version tag. Defaults to git short SHA.
  RALPH_REGISTRY  Registry prefix; if set, the image is also
                  tagged as $RALPH_REGISTRY/$RALPH_IMAGE:$VERSION
                  and pushed when --push is given.
  RALPH_IMAGE     Image name. Defaults to ralph-executor.
EOF
}

PUSH=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --push) PUSH=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; usage >&2; exit 2 ;;
  esac
done

RALPH_IMAGE="${RALPH_IMAGE:-ralph-executor}"
RALPH_VERSION="${RALPH_VERSION:-$(git rev-parse --short HEAD 2>/dev/null || echo "dev")}"
LOCAL_TAG="${RALPH_IMAGE}:${RALPH_VERSION}"
LATEST_TAG="${RALPH_IMAGE}:latest"

echo "Building ${LOCAL_TAG}" >&2

DOCKER_BUILDKIT=1 docker build \
  --progress=plain \
  --tag "${LOCAL_TAG}" \
  --tag "${LATEST_TAG}" \
  --file Dockerfile \
  .

FINAL_TAG="${LOCAL_TAG}"
if [[ -n "${RALPH_REGISTRY:-}" ]]; then
  REMOTE_TAG="${RALPH_REGISTRY}/${RALPH_IMAGE}:${RALPH_VERSION}"
  REMOTE_LATEST="${RALPH_REGISTRY}/${RALPH_IMAGE}:latest"
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

# Capture both stdout (the doctor JSON report) and stderr (human
# log lines) so we can show diagnostics on failure.
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

permissions:
  contents: read
  id-token: write   # for OIDC-based registry login (e.g. ECR)
  packages: write

jobs:
  build-and-preflight:
    runs-on: ubuntu-latest
    env:
      RALPH_IMAGE: ralph-executor
      RALPH_REGISTRY: ${{ secrets.RALPH_REGISTRY }}
    steps:
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

      - name: Build image
        id: build
        env:
          RALPH_VERSION: ${{ steps.ver.outputs.tag }}
        run: |
          IMG=$(bash scripts/build_image.sh)
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
        run: bash scripts/build_image.sh --push
```

The Azure Pipelines alternative is documented in `docs/deployment.md` (a complete `azure-pipelines.yml` is included there as a copy-paste reference; the GitHub workflow above is the supported path in v1).

---

## Tasks

The plan is split into tasks. Run them in order. Each task ends with an explicit verification command. Do not start a downstream task while an upstream task's verification is failing.

### Task 1 — Preconditions and scaffolding

**Files**
- Create: `tests/packaging/__init__.py`
- Modify: `pyproject.toml`

**Steps**

- [ ] 1. Confirm the orchestrator's Wave 1 and Wave 2 outputs are present (or stubbed). Run:
  ```
  test -f pyproject.toml
  test -f uv.lock
  test -d ralph_executor
  ```
  Expected: every check exits 0. If `ralph_executor/` is absent, create a stub package containing only `ralph_executor/__init__.py` (file content: `"""Stub for Plan 7."""`) and `ralph_executor/cli.py` with the body documented in the "Cross-plan assumptions ledger" above. Note the stub in `docs/deployment.md` (Task 9).

- [ ] 2. Open `pyproject.toml`. Confirm the `[project.scripts]` table exists and contains exactly:
  ```toml
  [project.scripts]
  ralph-executor = "ralph_executor.cli:main"
  ```
  If absent, add it. This is the load-bearing entry point referenced by the Dockerfile's `ENTRYPOINT`.

- [ ] 3. Confirm `[tool.pytest.ini_options].testpaths` includes `tests` and that mypy's `files` includes `ralph_executor`. If not, the implementer SHALL stop and resolve before continuing — Plan 1's gates are a precondition.

- [ ] 4. Create `tests/packaging/__init__.py` with the single line:
  ```python
  """Empty package marker."""
  ```

- [ ] 5. Add the following packaging-test development dependencies to `pyproject.toml` `[dependency-groups]` (or `[project.optional-dependencies].dev`, matching the project's existing convention):
  ```
  dockerfile-parse>=2.0
  pyyaml>=6.0
  ```
  Then run:
  ```
  uv sync
  ```
  Expected: completes without error.

- [ ] 6. Verify the toolchain is intact:
  ```
  uv run ruff check pyproject.toml || true
  uv run mypy --version
  uv run pytest --collect-only -q
  ```
  Expected: ruff and mypy invocations don't error; pytest collects the existing tests without import failures. Stop and resolve if pytest cannot collect.

### Task 2 — Write the baked `.claude/settings.json`

**Files**
- Create: `.claude/settings.json`
- Create: `tests/packaging/test_settings_json.py`

**Steps**

- [ ] 1. Write `.claude/settings.json` with the exact content listed in the "[.claude/settings.json] (full content the implementer writes)" section above. No edits.

- [ ] 2. Confirm the file is valid JSON:
  ```
  uv run python -c "import json; json.load(open('.claude/settings.json'))"
  ```
  Expected: exits 0.

- [ ] 3. Create `tests/packaging/test_settings_json.py` with the following content:
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
          "Skill(ado-pr)",
          "Skill(ralph-add)",
          "Skill(ralph-status)",
          "Skill(ralph-cancel)",
          "Skill(ralph-promote)",
          "Skill(ralph-triage)",
          "Skill(ralph-doctor)",
      }
      missing = required - set(allow)
      assert not missing, f"missing permissions.allow entries: {missing}"


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

- [ ] 4. Run the new tests:
  ```
  uv run pytest tests/packaging/test_settings_json.py -v
  ```
  Expected: every test passes. Fix any drift between the JSON and the assertions before continuing.

### Task 3 — Write the Dockerfile and `.dockerignore`

**Files**
- Create: `Dockerfile`
- Create: `.dockerignore`
- Create: `tests/packaging/test_dockerfile.py`

**Steps**

- [ ] 1. Write `Dockerfile` with the exact content listed in the "Dockerfile (full content the implementer writes)" section above. No edits except (optionally) bumping the `@anthropic-ai/claude-code@1.0.0` pin to the team's current standard if the implementer has confirmed it.

- [ ] 2. Write `.dockerignore` with the following content:
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

  # Tests, samples, docs — not needed in the runtime image
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

  # CI / k8s files — copied separately or not at all
  .github
  manifests
  ```

- [ ] 3. Create `tests/packaging/test_dockerfile.py`:
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


  def test_runtime_image_runs_as_non_root(parser: DockerfileParser) -> None:
      user_lines = [s for s in parser.structure if s["instruction"] == "USER"]
      assert user_lines, "no USER directive found"
      last = user_lines[-1]["value"].strip()
      assert last == "ralph", f"final USER must be ralph, got {last!r}"


  def test_entrypoint_is_ralph_executor(parser: DockerfileParser) -> None:
      ep_lines = [s for s in parser.structure if s["instruction"] == "ENTRYPOINT"]
      assert ep_lines, "no ENTRYPOINT"
      value = ep_lines[-1]["value"]
      assert "ralph-executor" in value, f"entrypoint must invoke ralph-executor, got {value!r}"


  def test_no_inline_secrets(parser: DockerfileParser) -> None:
      contents = DOCKERFILE.read_text(encoding="utf-8")
      for needle in ("ANTHROPIC_API_KEY=", "ADO_PAT=", "AWS_SECRET_ACCESS_KEY="):
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


  def test_oci_labels_set(parser: DockerfileParser) -> None:
      labels = [s for s in parser.structure if s["instruction"] == "LABEL"]
      blob = " ".join(s["value"] for s in labels)
      assert "org.opencontainers.image.title" in blob
      assert "org.opencontainers.image.source" in blob
  ```

- [ ] 4. Run the Dockerfile tests:
  ```
  uv run pytest tests/packaging/test_dockerfile.py -v
  ```
  Expected: every test passes.

- [ ] 5. Build the image to confirm the Dockerfile is well-formed (this is the equivalent of "green" in the TDD analogue — the build is the smoke):
  ```
  bash scripts/build_image.sh
  ```
  (Skip this step if `scripts/build_image.sh` is not yet present — Task 5 creates it. In that case run a raw `docker build -t ralph-executor:test .` instead.) Expected: image builds cleanly. The first build will be slow due to apt + npm; subsequent builds benefit from the BuildKit cache mounts.

- [ ] 6. Smoke-run the image:
  ```
  docker run --rm --entrypoint ralph-executor ralph-executor:test --help
  ```
  Expected: exit code 0, help text mentions `ralph-executor`. If the CLI is the stub from Task 1, expect the stub's printed line and exit 0.

### Task 4 — Write the k8s manifests

**Files**
- Create: `manifests/ralph-deployment.yaml`
- Create: `manifests/ralph-job.yaml`
- Create: `manifests/ralph-configmap.yaml`
- Create: `manifests/ralph-secrets.template.yaml`
- Create: `manifests/ralph-rbac.yaml`
- Create: `tests/packaging/test_manifests.py`

**Steps**

- [ ] 1. Create each manifest file with the exact content listed in the "Manifests (full content the implementer writes)" section above. No edits. Use LF line endings (not CRLF) so `kubectl apply` on Linux is byte-clean.

- [ ] 2. Validate each manifest's YAML syntax:
  ```
  uv run python -c "
  import sys, yaml
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
  Expected: every line prints, no exception.

- [ ] 3. If `kubectl` is available locally, run a client-side dry-run apply for each manifest:
  ```
  kubectl apply --dry-run=client -f manifests/ralph-configmap.yaml
  kubectl apply --dry-run=client -f manifests/ralph-rbac.yaml
  kubectl apply --dry-run=client -f manifests/ralph-deployment.yaml || true
  kubectl apply --dry-run=client -f manifests/ralph-job.yaml          || true
  kubectl apply --dry-run=client -f manifests/ralph-secrets.template.yaml
  ```
  The Deployment and Job dry-runs will fail because of the `__IMAGE__` placeholder — that is expected. The configmap, secret-template, and rbac dry-runs MUST succeed. If kubectl is not present, skip this step (the pytest tests in step 5 cover the structural assertions).

- [ ] 4. Create `tests/packaging/test_manifests.py`:
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


  def test_secrets_template_is_marked_unsafe_to_apply() -> None:
      path = MANIFESTS / "ralph-secrets.template.yaml"
      raw = path.read_text(encoding="utf-8")
      assert "RALPH-TEMPLATE-DO-NOT-APPLY-AS-IS" in raw
      docs = _load(path)
      assert len(docs) == 1
      sec = docs[0]
      assert sec["kind"] == "Secret"
      data = sec["stringData"]
      assert data["ANTHROPIC_API_KEY"].startswith("__REPLACE_WITH")
      assert data["ADO_PAT"].startswith("__REPLACE_WITH")


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

- [ ] 5. Run the manifest tests:
  ```
  uv run pytest tests/packaging/test_manifests.py -v
  ```
  Expected: every test passes. If any assertion fails, the implementer SHALL update the YAML to match — the tests are the contract, not the YAML.

### Task 5 — Write the build and preflight scripts

**Files**
- Create: `scripts/build_image.sh`
- Create: `scripts/preflight.sh`
- Create: `tests/packaging/test_scripts.py`

**Steps**

- [ ] 1. Write `scripts/build_image.sh` and `scripts/preflight.sh` with the exact content from the "Build and preflight scripts" section above. Use LF line endings.

- [ ] 2. Mark both executable:
  ```
  chmod +x scripts/build_image.sh scripts/preflight.sh
  ```
  On Windows the `chmod` is a no-op; instead set the executable bit via:
  ```
  git update-index --chmod=+x scripts/build_image.sh
  git update-index --chmod=+x scripts/preflight.sh
  ```
  so the bit travels with the commit.

- [ ] 3. If `shellcheck` is installed (`shellcheck --version`), run it against both scripts:
  ```
  shellcheck scripts/build_image.sh scripts/preflight.sh
  ```
  Expected: no findings. If findings appear, fix them before continuing. If `shellcheck` is not installed, log "shellcheck not present; skipping" and continue — the pytest test in step 5 enforces the same rule when CI provides shellcheck.

- [ ] 4. Smoke-run the build script's help:
  ```
  bash scripts/build_image.sh --help
  ```
  Expected: exit 0, usage text printed.

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
  ```

- [ ] 6. Run the script tests:
  ```
  uv run pytest tests/packaging/test_scripts.py -v
  ```
  Expected: every test passes (shellcheck may skip — that's fine locally).

### Task 6 — End-to-end image smoke (build + preflight)

**Files**
- (no new files; this task exercises the artifacts built in Tasks 3 and 5)

**Steps**

- [ ] 1. Build the image and capture the tag:
  ```
  IMG=$(bash scripts/build_image.sh)
  echo "Built ${IMG}"
  ```
  Expected: build succeeds; `IMG` looks like `ralph-executor:<short-sha>` (or a registry-prefixed equivalent if `RALPH_REGISTRY` is set).

- [ ] 2. Run the preflight script against the image:
  ```
  bash scripts/preflight.sh "${IMG}"
  ```
  Expected (against Plan 11's real doctor): exit 0 and the doctor JSON report printed to stderr.
  Expected (against the Task 1 stub doctor): exit 0 (the stub returns 0) and a banner noting the stub was active.

- [ ] 3. Confirm the runtime user inside the container is `ralph`:
  ```
  docker run --rm "${IMG}" id -u 2>/dev/null || \
    docker run --rm --entrypoint id "${IMG}" -u
  ```
  Expected: prints `10001`.

- [ ] 4. Confirm `.claude/settings.json` is present inside the image:
  ```
  docker run --rm --entrypoint cat "${IMG}" /etc/ralph/.claude/settings.json | head -5
  ```
  Expected: prints the first lines of the baked JSON.

- [ ] 5. Confirm Claude Code CLI is installed:
  ```
  docker run --rm --entrypoint claude "${IMG}" --version || true
  ```
  Expected: claude reports its version. If it errors trying to call the network on startup, that's acceptable — what we need to know is that the binary is on `$PATH`.

### Task 7 — Wire the GitHub Actions workflow

**Files**
- Create: `.github/workflows/ralph-image.yml`

**Steps**

- [ ] 1. Write `.github/workflows/ralph-image.yml` with the exact content from the "CI workflow" section above. Do NOT add any other workflow file in this plan.

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
  Expected: exits 0.

- [ ] 3. Validate the workflow with `actionlint` if available:
  ```
  actionlint .github/workflows/ralph-image.yml 2>&1 || echo "actionlint not present; skipping"
  ```
  Expected: no findings (or skip message). If actionlint reports issues, fix them inline before continuing.

- [ ] 4. Document the GitHub secrets the workflow needs in `docs/deployment.md` (Task 9 creates the runbook). The implementer SHALL list at minimum:
  - `RALPH_REGISTRY` — the registry prefix (e.g. an ECR URL)
  - `RALPH_REGISTRY_USER` — registry username (or `AWS` for ECR)
  - `RALPH_REGISTRY_PASSWORD` — registry password (or an ephemeral ECR token)

### Task 8 — Validate the Azure Pipelines alternative

**Files**
- (no new files; the alternative is documented inline in `docs/deployment.md` in Task 9)

**Steps**

- [ ] 1. Confirm the Azure Pipelines snippet (which will be embedded in Task 9's runbook) is syntactically valid YAML. The exact snippet to embed:
  ```yaml
  # Azure Pipelines alternative to .github/workflows/ralph-image.yml.
  # Commit at azure-pipelines.yml if the team uses ADO Pipelines.
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

  steps:
    - checkout: self
      fetchDepth: 0

    - bash: |
        export RALPH_VERSION="$(git rev-parse --short HEAD)"
        IMG=$(bash scripts/build_image.sh)
        echo "##vso[task.setvariable variable=IMAGE]$IMG"
      displayName: 'Build image'

    - bash: |
        bash scripts/preflight.sh "$(IMAGE)"
      displayName: 'Preflight (ralph-doctor)'

    - task: Docker@2
      displayName: 'Login to registry'
      inputs:
        command: login
        containerRegistry: $(RALPH_REGISTRY_SERVICE_CONNECTION)

    - bash: |
        bash scripts/build_image.sh --push
      displayName: 'Push image'
      env:
        RALPH_REGISTRY: $(RALPH_REGISTRY)
        RALPH_VERSION: $(Build.SourceVersion)
  ```
  Paste this snippet into the runbook in Task 9 verbatim. The implementer SHALL parse-check it before pasting:
  ```
  uv run python -c "
  import yaml
  yaml.safe_load(open('/tmp/azure-pipelines-snippet.yml')) if False else None
  # (Operationally — drop the snippet into a tmp file then yaml.safe_load it.)
  "
  ```
  (Skip the parse check if reading from this plan rather than a tmp file — the snippet is already valid YAML in this document.)

- [ ] 2. The alternative is a NICE TO HAVE, not a primary deployment path. Plan 12 does not commit `azure-pipelines.yml` itself — operators who pick ADO Pipelines will copy the snippet from the runbook.

### Task 9 — Write the deployment runbook

**Files**
- Create: `docs/deployment.md`

**Steps**

- [ ] 1. Write `docs/deployment.md` with the structure below. The implementer SHALL write the actual prose; the headings and the content checklist under each are non-negotiable.

  ```markdown
  # Ralph Deployment Runbook

  ## Prerequisites
  - List the tools the operator must have: `docker` (BuildKit), `kubectl`
    1.30+ (or `oc` for ROSA), AWS CLI (for ECR push), access to the
    target cluster's `ralph` namespace, the required GitHub Actions
    secrets (listed below).
  - List the GitHub Actions secrets explicitly: `RALPH_REGISTRY`,
    `RALPH_REGISTRY_USER`, `RALPH_REGISTRY_PASSWORD`.
  - List the cluster preconditions: the `ralph` namespace exists; the
    cluster has PodSecurity restricted-or-baseline enforcement enabled.
  - Note the Plan 7 / 11 stub assumptions if either is still stubbed.

  ## Choosing a mode
  Side-by-side decision table:

  | Question | Long-running Deployment | Coder task-pod Job |
  |---|---|---|
  | Are PBIs continuous or bursty? | continuous | bursty |
  | Does the org use Coder workspaces? | optional | required |
  | Idle compute tolerance | acceptable | unacceptable |
  | Need rolling restarts? | yes | no — each pod is short-lived |
  | Where do logs go? | central log store via the Deployment | per-task pod logs; aggregate via Coder |

  Recommend Deployment for the first ROSA rollout. Move to Job once
  a scheduler exists.

  ## Building the image

  Local build:

      RALPH_VERSION=$(git rev-parse --short HEAD) \
      bash scripts/build_image.sh

  Build + push:

      RALPH_REGISTRY=123456789012.dkr.ecr.eu-west-2.amazonaws.com \
      RALPH_VERSION=$(git rev-parse --short HEAD) \
      bash scripts/build_image.sh --push

  The CI pipeline runs the same command. See
  `.github/workflows/ralph-image.yml`.

  ## Pre-deploy gate

  scripts/preflight.sh runs ralph-doctor inside the just-built
  image. CI invokes it automatically. To run manually:

      IMG=$(bash scripts/build_image.sh)
      bash scripts/preflight.sh "$IMG"

  Expected exit 0. If exit 2, ralph-doctor FAILED and the image
  MUST NOT be promoted. Read the doctor JSON report on stderr
  to see which check failed.

  ## Secrets handling

  ralph-secrets carries ANTHROPIC_API_KEY and ADO_PAT. The
  template at manifests/ralph-secrets.template.yaml is NOT safe
  to apply as-is. The supported substitution paths are:

  1. kubectl-managed:

      kubectl create secret generic ralph-secrets \
        --namespace ralph \
        --from-literal=ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
        --from-literal=ADO_PAT="$ADO_PAT"

  2. External Secrets Operator backed by AWS Secrets Manager
     (preferred for ROSA). Document the ExternalSecret manifest
     fields the operator needs (this runbook lists the secret
     names and keys only; the ESO manifest is operator-owned).

  3. SealedSecrets (alternative for self-hosted clusters).

  ## IAM and IRSA

  The ralph ServiceAccount carries an annotation slot for IRSA
  (eks.amazonaws.com/role-arn). The IAM role's trust policy
  scopes it to the ralph SA in the ralph namespace.

  Step-by-step:
  1. Create the IAM role in AWS Console with the trust policy
     for the cluster's OIDC provider, namespace=ralph, sa=ralph.
  2. Attach a policy that grants whatever AWS APIs Ralph needs
     (in v1 — none, unless the org uses Secrets Manager).
  3. After applying manifests/ralph-rbac.yaml, annotate the SA:

      kubectl annotate sa ralph -n ralph \
        eks.amazonaws.com/role-arn=arn:aws:iam::123456789012:role/ralph-executor

  ## Applying manifests

  Step-by-step (Deployment mode):

      kubectl apply -f manifests/ralph-rbac.yaml
      kubectl apply -f manifests/ralph-configmap.yaml
      # Substitute __IMAGE__ in the deployment manifest, e.g. via:
      sed "s|__IMAGE__|$IMG|" manifests/ralph-deployment.yaml | kubectl apply -f -
      # ralph-secrets is created out of band (see Secrets handling).
      kubectl rollout status deploy/ralph-executor -n ralph

  Step-by-step (Job mode, per PBI dispatch):

      sed -e "s|__IMAGE__|$IMG|" \
          -e "s|__PBI_ID__|$PBI_ID|" \
          manifests/ralph-job.yaml | kubectl apply -f -

  ## Verifying the pod

      kubectl get pods -n ralph
      kubectl logs -n ralph deploy/ralph-executor --tail=50
      kubectl exec -n ralph deploy/ralph-executor -- ralph-executor health --ready

  Expected: pod is Ready; logs show the iteration loop starting;
  health --ready exits 0.

  ## Troubleshooting

  Common failures and their first-look diagnostics:

  - ImagePullBackOff   → check RALPH_REGISTRY auth; describe pod.
  - CrashLoopBackOff   → kubectl logs --previous; check whether
    the doctor passed at preflight time.
  - claude -p prompted for permission inside the pod → the doctor
    drift detector should have caught this; rerun preflight and
    fix the .claude/settings.json before reshipping.
  - Missing secret      → kubectl get secret ralph-secrets -n ralph;
    if absent, follow Secrets handling above.

  ## Alternative: Azure Pipelines

  The team's standard is GitHub Actions
  (.github/workflows/ralph-image.yml). For teams using ADO
  Pipelines instead, this YAML is functionally equivalent:

      [paste the snippet from Task 8 here verbatim]

  ## Plan 7 / 11 stub assumptions

  Document that until Plan 7 ships the real ralph-executor
  health/run commands and Plan 11 ships the real doctor, the
  preflight gate is meaningful only after both plans land.
  Refer the operator to the orchestrator plan for status.
  ```

- [ ] 2. The runbook MUST cover (checklist):
  - Prerequisites (tools, secrets, cluster preconditions)
  - Choosing a mode (Deployment vs Job decision table)
  - Building the image (local + CI)
  - Pre-deploy gate (preflight.sh invocation and exit-code semantics)
  - Secrets handling (kubectl, ESO, SealedSecrets paths)
  - IAM and IRSA (annotation slot)
  - Applying manifests (sed substitution of `__IMAGE__`)
  - Verifying the pod (kubectl get/logs/exec)
  - Troubleshooting (4+ failure modes)
  - Azure Pipelines alternative (full inline snippet)
  - Plan 7 / 11 stub callout

- [ ] 3. Re-render the runbook locally and confirm headings render correctly:
  ```
  uv run python -c "
  from pathlib import Path
  text = Path('docs/deployment.md').read_text(encoding='utf-8')
  required = [
      '## Prerequisites',
      '## Choosing a mode',
      '## Building the image',
      '## Pre-deploy gate',
      '## Secrets handling',
      '## IAM and IRSA',
      '## Applying manifests',
      '## Verifying the pod',
      '## Troubleshooting',
      '## Alternative: Azure Pipelines',
  ]
  missing = [h for h in required if h not in text]
  assert not missing, f'missing headings: {missing}'
  print('runbook OK')
  "
  ```
  Expected: prints `runbook OK`.

### Task 10 — Verification gate (full)

**Files**
- (no new files)

**Steps**

- [ ] 1. Run the full packaging test set:
  ```
  uv run pytest tests/packaging/ -v
  ```
  Expected: every test passes.

- [ ] 2. Run the orchestrator's verification gate command for Plan 12 verbatim:
  ```
  docker build -t ralph:test . && docker images | grep ralph
  ```
  Expected: image builds, `docker images` shows `ralph` in the output.

- [ ] 3. Run preflight against the gate-built image:
  ```
  bash scripts/preflight.sh ralph:test
  ```
  Expected: exit 0.

- [ ] 4. Confirm the full repo gate is green (per the orchestrator's shared conventions section):
  ```
  uv run ruff check . && uv run ruff format --check . && uv run mypy ralph_executor && uv run pytest
  ```
  Expected: every command exits 0. If `uv run mypy ralph_executor` fails because the executor is still the Task 1 stub, the implementer SHALL ensure the stub itself type-checks (it should — it's three lines).

- [ ] 5. Self-review checklist. The implementer SHALL confirm each line before declaring the task complete:
  - [ ] No `__IMAGE__` / `__PBI_ID__` placeholder appears outside the manifests where it is intended as a deploy-time substitution marker.
  - [ ] No secret value appears in any committed file (grep for `ANTHROPIC_API_KEY=` / `ADO_PAT=` returns only the template sentinels and the runbook references).
  - [ ] Every file listed in "File Structure" exists and is referenced by at least one test or by the runbook.
  - [ ] The runbook covers both Deployment and Job modes with a clear "when to use" recommendation.
  - [ ] The preflight script's exit-code mapping (0 / 2 / 3 / 4) is documented in the runbook.
  - [ ] The plan 7 / 11 stub assumption is called out in `docs/deployment.md` so an operator can decide whether to ship now or wait.

- [ ] 6. Commit each task's output as a separate conventional-commit. Commits this plan produces (in order):
  - `chore(packaging): scaffold packaging tests and console-script entry`
  - `feat(packaging): bake .claude/settings.json for the ralph image`
  - `feat(packaging): add multi-stage Dockerfile for ralph-executor`
  - `feat(packaging): add k8s deployment, job, configmap, secrets template, rbac manifests`
  - `feat(packaging): add build_image.sh and preflight.sh helper scripts`
  - `ci(packaging): add ralph-image GitHub Actions workflow`
  - `docs(packaging): add ralph deployment runbook`

  Do not combine commits across tasks — small, reviewable diffs.

---

## Verification gate (orchestrator-defined)

Per `2026-05-24-00-orchestrator.md`, Plan 12's gate is:

```
docker build -t ralph:test . && docker images | grep ralph
```

Expected: image builds; `docker images` lists a `ralph` row. Task 10 step 2 runs this verbatim.

Additional gates this plan adds beyond the orchestrator default — these MUST also pass before Plan 13 starts:

- `uv run pytest tests/packaging/ -v` — every packaging test passes.
- `bash scripts/preflight.sh ralph:test` — preflight exits 0 against the gate-built image (or, if Plan 11 is still stubbed, exits 0 with a stub banner on stderr).

---

## Open questions for the implementer (NOT blockers)

The implementer SHALL surface these in the PR description rather than guess silently:

1. **Registry choice.** The runbook assumes ECR. If the team has standardised on GHCR, ACR, or a private registry, the runbook's "Building the image" and "IAM and IRSA" sections need a thin overlay. The Dockerfile and manifests are registry-agnostic.
2. **PodSecurity mode.** The manifests assume PodSecurity restricted-or-baseline. If the cluster enforces a non-default mode (e.g. fully baseline only), the `securityContext` may need adjustment. Confirm with the platform team.
3. **Logging stack.** The manifests do not declare a logging sidecar. The runbook assumes the cluster has a daemonset (Fluent Bit, Vector, etc.) that captures stdout. Confirm at deploy time.
4. **Claude Code CLI version pin.** The Dockerfile pins `@anthropic-ai/claude-code@1.0.0`. The implementer SHALL replace this with the team's current standard at the time of merge.
5. **`uv` version pin.** The Dockerfile copies from `ghcr.io/astral-sh/uv:0.4.30`. Bump deliberately if the locked dependencies require a newer uv.
