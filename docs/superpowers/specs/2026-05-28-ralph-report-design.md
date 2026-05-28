# ralph-report — design

**Date:** 2026-05-28
**Author:** brainstorming session
**Status:** Draft (pre-plan)

## Purpose

`ralph-report` is a local, read-only HTML dashboard of activity in a single ralph-queue branch. It answers "what is ralph doing, and what happened in the last 24 hours?" without requiring the operator to read git log or walk `.ralph/` folders by hand.

It is a **separate** skill from `ralph-status`. `ralph-status` is a one-shot CLI table; `ralph-report` is a long-running local web server rendering a richer page in the browser.

## Non-goals

- Live updates (SSE / WebSocket / fetch polling). Refresh is browser F5 only.
- Multi-repo aggregation. Single repo per server instance.
- Writes, mutations, click-to-edit. Server is read-only.
- PR-check rollup / CI status. The queue folder a PBI sits in is the truth surface.
- Authentication. Server binds to `127.0.0.1` only.
- Persistence beyond the running server. State is always recomputed from git on each request.

## Skill location and invocation

The skill lives at `skills/ralph-report/` in the ralph repo, versioned with the code. It is invoked from the primary checkout:

```bash
# foreground (blocks terminal)
uv run python skills/ralph-report/scripts/report.py --repo <path-to-ralph-repo>

# background (mirrors brainstorm companion)
bash skills/ralph-report/scripts/start-server.sh --repo <path-to-ralph-repo>
# → URL, port, and pid written to <repo>/.ralph-work/report/server-info

# stop
bash skills/ralph-report/scripts/stop-server.sh <path-to-ralph-repo>
```

## File layout

```
skills/ralph-report/
├── SKILL.md
└── scripts/
    ├── report.py          # entry; parses args; starts server
    ├── server.py          # HTTPServer + RequestHandler + idle timer
    ├── render.py          # HTML generation (Bootstrap markup)
    ├── git_walker.py      # git log subprocess + commit-message parsing
    ├── snapshot.py        # walk .ralph/<state>/ at HEAD; parse PBI frontmatter
    ├── start-server.sh    # background launcher
    └── stop-server.sh     # PID kill via server-info
```

Tests live at `tests/skills/test_ralph_report.py`, mirroring the `ralph-status` test pattern: local bare + worktree git fixtures with scripted commits, no network, no real GitHub.

## Architecture

```
Browser (you)
   │ GET /
   ▼
http.server (stdlib) ──► render.py ──► snapshot.py ──► .ralph/* at ralph-queue HEAD
                                  └──► git_walker.py ─► git log --since 24h on ralph-queue

idle timer (30 min) ─resets on every request─► server.shutdown()
```

Single Python process, stdlib only. Subprocess to `git` for log walking. File reads for snapshot panels.

## Data sources

### Snapshot panels (always full)

The script reads from the **queue worktree** at `<repo>/.ralph-work/queue` if it exists (worktree mode) — straight `Path` reads against the working tree, no git needed. Otherwise it falls back to `git -C <repo> ls-tree origin/ralph-queue .ralph/<state>/` to enumerate PBI directories and `git -C <repo> show origin/ralph-queue:<path>` to read each entry file. No temp checkout. A `git fetch origin ralph-queue` runs once on server start and is refreshed via a 60-second cooldown when serving subsequent requests.

For each of `current/`, `inbox/`, `pending-pr/`, `blocked/` the script:

1. Lists `.ralph/<state>/*/` directories on `ralph-queue` HEAD.
2. Parses the entry file's YAML frontmatter using the same reader as `skills/ralph-status` (PBI.md / BUG.md / FEEDBACK.md).
3. For `blocked/`, also includes `META-cycle-*.md` sentinel files at the top level (no PBI dir) with a synthesised row (id from filename, reason from `meta_bug_kind` in frontmatter).
4. For `pending-pr/`, reads `PR-LINK.md` if present to surface the PR URL.

### Window panels (last 24 hours)

`git log` is the **sole source** of timing:

```
git log ralph-queue \
  --since=24.hours.ago \
  --pretty=format:'%H%x09%aI%x09%s' \
  --name-only
```

`git_walker.py` parses commits against a fixed grammar (the executor's commit subjects are deterministic):

| Commit subject regex | Event kind | Used in |
|---|---|---|
| `^chore\(queue\): add (?P<id>[A-Z][A-Z0-9-]+)` | added | timeline |
| `^chore\(ralph-queue\): move (?P<id>\S+) from inbox to current` | claimed | timeline |
| `^feat\(ralph-queue\): move (?P<id>\S+) from current to pending-pr` | PR opened | timeline |
| `^chore\(ralph-queue\): move (?P<id>\S+) from pending-pr to done` | shipped | **timeline + Done-24h** |
| `^chore\(ralph-queue\): move (?P<id>\S+) from current to blocked` | blocked | timeline |
| `^chore\(queue\): persist iteration writes for` | (filtered out) | — |
| Any commit that **adds** a path matching `.ralph/blocked/META-cycle-*\.md` | cycle-trip | timeline |

Any commit that does not match any pattern is silently dropped (e.g. `docs(spec):` commits the operator made to the queue branch).

"Done in last 24h" is exactly the set of `shipped` events. The timestamp shown is the commit's **author date** (`%aI`), not `updated_at` from frontmatter — the commit is the truthful moment ralph moved the PBI.

## Page layout

Five rows, top to bottom, in the chosen layout (see `.superpowers/brainstorm/<session>/content/ralph-report-chosen.html` for the mockup):

1. **Current** — full width. 1 PBI max. Green left-border. Shows id, severity badge, type badge, age since claim, attempt count, feature branch.
2. **Blocked** — full width. Red left-border. PBIs in `blocked/` + META-cycle sentinels. Each row shows reason and (for cycle sentinels) the file or thrash signal that tripped the detector.
3. **Inbox + Pending PR** — 50/50 side-by-side. Blue left-borders. Inbox is sorted by priority lane (severity, then created_at); each row shows depends_on chain when present. Pending PR shows PR URL.
4. **Done (last 24h)** — full width. Grey left-border. Rows sorted newest-first.
5. **Activity timeline (last 24h, state-changes only)** — full width. Grey left-border. One row per matched commit, formatted `HH:MM <event-kind> <PBI-id> <optional detail>`. Persist-iteration commits are filtered out.

Header shows the repo path, the last refresh timestamp, and an `F5 to update` hint.

Bootstrap 5 from CDN (`cdn.jsdelivr.net`). One CDN link in `<head>`. No JS — page is fully server-rendered. CSS is a small inline `<style>` block in `render.py` for the colour-coded panel borders and timeline rows.

## Server lifecycle

- **Background launcher** (`start-server.sh`) is the recommended invocation. It runs `report.py` detached, writes `server-info`, prints the URL, exits.
- **Auto-exit:** 30 minutes after the last HTTP request, the server self-terminates and writes `server-stopped` next to `server-info`. The idle timer resets on every request to `/` or `/health`.
- **State file:** `<repo>/.ralph-work/report/server-info` — JSON with `{port, url, pid, started_at}`. `stop-server.sh` reads `pid` and sends SIGTERM.
- **Port selection:** auto-pick a free port in the range `52340–52399` by binding `0` and reading back the chosen port. If the entire range is in use, fail with a clear error.
- **Bind host:** always `127.0.0.1`. Optional `--bind 0.0.0.0` flag for the remote-browser case; documented but not exposed in `start-server.sh` by default.
- **Routes:**
  - `GET /` — full report HTML. Resets idle timer.
  - `GET /health` — JSON `{ok: true, last_refresh: <iso>}`. Resets idle timer. Used by the stop script to confirm the server is up before sending SIGTERM.
  - All other routes — `404`.

## Error handling

- **Repo path missing or not a git repo** — `report.py` exits with a clear error before binding the port.
- **`ralph-queue` branch missing on remote AND no local queue worktree** — render a single error panel: "ralph-queue branch not found; is this a ralph repo?". HTTP 200, no exception.
- **PBI frontmatter parse error** — render the row with `?` in the id column and the parse error in the title column (same soft-fail behaviour as `ralph-status`). One bad PBI does not break the page.
- **`git log` subprocess failure** (e.g. corrupt repo) — render an error panel in the timeline + Done sections; snapshot panels still render from on-disk reads. Log the stderr to the server's stdout.
- **Subprocess encoding** — every `subprocess.run` / `subprocess.Popen` in this skill passes `encoding="utf-8", errors="replace"` to avoid the Windows-cp1252 trap fixed in `claude_spawn.py` on 2026-05-28.

## Testing strategy

- **Unit tests** (`tests/skills/test_ralph_report.py`):
  - `git_walker.parse_commit_subject` against a parametrised list of real subjects pulled from current `git reflog ralph-queue` output.
  - `snapshot.read_state` against a fixture `.ralph/` tree (reuse `ralph-status` fixtures where possible).
  - `render.render_page` returns valid HTML containing each expected section heading and PBI id.
- **Integration test:** spin up a fixture bare repo + queue worktree, scripted commits matching every commit-grammar row in the table above, then start `server.py` against `localhost:0`, fetch `GET /`, assert HTML content.
- **No browser tests.** No JS means no Selenium / Playwright needed.

## Open / deferred questions (post-MVP)

- Multi-repo aggregation. Would extend the `--repos-file` shape from `ralph-status`.
- Live updates. Most plausible add: a `Last-Modified` check against `<repo>/.ralph-work/queue/.git/HEAD` and `refs/heads/ralph-queue` with a `<meta http-equiv="refresh">` fallback.
- Per-PBI drill-down page (`GET /pbi/<id>`) showing full HISTORY.md and commit history for the feature branch.
- Filtering by severity, type, or repo-name (single-repo today makes the last one moot).

## References

- `skills/ralph-status/SKILL.md` — sibling skill, shares the frontmatter reader.
- `.superpowers/brainstorm/109138-1779996990/content/ralph-report-chosen.html` — approved layout mockup.
- `.superpowers/brainstorm/109138-1779996990/content/ralph-report-design.html` — architecture + assumptions mockup.
- `ralph_executor/loop.py` — source of the deterministic commit-message grammar parsed by `git_walker.py`.
