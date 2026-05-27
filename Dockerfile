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
