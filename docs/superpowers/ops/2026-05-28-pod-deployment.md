# Pod deployment runbook (2026-05-28)

> Audience: operator running `ralph-executor` unattended in a container.
> Last updated: 2026-05-28

## Image

Pull the ROSA Phase 1 image (GitHub variant). Build details live in
`docs/superpowers/plans/2026-05-24-12-rosa-packaging.md`.

```
<registry>/ralph-executor:<tag>
```

The image bakes the executor wheel, the host-agnostic skills tree,
the GitHub-flavoured skills, and a `.claude/settings.json` mounted at
`/etc/ralph/.claude/settings.json` (read via `CLAUDE_CONFIG_DIR`).

## Required config

The container expects `~/.ralph/config.toml` to be mounted in, with at
least:

```toml
workspace_root = "/var/lib/ralph/workspaces"
queue_repo = "https://github.com/emp3thy/ralph-queue"
```

Optional but typical:

```toml
auto_merge_clean_prs = true
stale_days = 3
bot_author_email = "ralph-bot@example.com"
```

## Required secrets

GitHub auth — either:
- a long-lived `GH_TOKEN` env var (the `gh` CLI inside the container reads it), OR
- a mounted `~/.config/gh/hosts.yml` with a stored OAuth token.

Claude Code auth — a mounted `~/.claude/.credentials.json` (the OAuth
token the host-side `claude` login produced). The image does not embed
any Anthropic credentials.

## Layout under workspace_root

After first iteration:

```
/var/lib/ralph/workspaces/
├── queue/                           # clone of queue_repo (always on main)
└── clones/
    └── emp3thy-svc-auth/            # per-target clone
        └── .ralph-work/
            └── WI-1234/             # per-PBI feature-branch worktree
```

A persistent volume mounted at `workspace_root` lets the pod survive
restarts without re-cloning. Without persistence, every pod start
re-clones everything — slow but functionally fine.

## Exit behaviour

By default the executor runs until the inbox is drained and no work
remains (PBI `EXECUTOR-EXIT-WHEN-IDLE-DEFAULT`). Pass `--watch` to keep
it polling forever. For pod deployment the default is intentional:
when the queue is empty the pod exits cleanly and the scheduler can
spin it down.

## Health and logs

- The executor logs to stderr at `INFO` by default. Capture stderr
  into your log aggregator.
- The cycle-detector's halt sentinel lives at
  `$workspace_root/queue/.ralph/state/halted`. On halt, the executor
  raises `HaltedError` and exits non-zero. Surfacing that as an alert
  is the operator's responsibility — the executor itself does not
  call back out.

## Updating the image

Pull a new image tag. The executor reads only `~/.ralph/config.toml`
and the workspace; no in-image state beyond the wheel + skills tree.
Updates are zero-downtime in a single-instance setup (running pod
finishes its iteration, exits idle, scheduler spins up the new image).

## Troubleshooting

- **"queue_repo not configured"** — `~/.ralph/config.toml` is missing
  or the key isn't there. Bind-mount your config correctly.
- **"git clone of queue repo … failed"** — usually auth. Confirm
  `gh auth status` inside the running container.
- **"halt sentinel is active"** — the cycle detector tripped.
  Inspect `$workspace_root/queue/.ralph/blocked/META-cycle-*.md`,
  acknowledge the sentinel by editing
  `$workspace_root/queue/.ralph/state/halted` (set `acknowledged_by`
  and `acknowledged_at`), restart the pod.
