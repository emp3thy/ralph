# SKILLS-QUEUE-CLONE-MIGRATION Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate all operator-facing skills (`ralph-add`, `ralph-cancel`, `ralph-promote`, `ralph-triage`, `ralph-status`) from the old "operate on a target repo's `ralph-queue` branch" model to the new "operate on the single queue clone at `$RALPH_WORKSPACE/queue/`" model. Rewrite `scripts/queue_writer.py` helper. Update README + add a new pod-deployment ops doc.

**Architecture:** Each skill currently calls `checkout_queue_branch(repo, branch)` from `scripts/queue_writer.py`. That helper is removed; skills instead call `ensure_queue_clone(workspace_root, queue_repo)` from `ralph_executor.queue_clone` (introduced in PBI 1). Each skill's `--repo` / `--branch` / `--repos-file` arguments are removed. `ralph-add` gains `--target-repo` to populate the PBI's `target_repo` frontmatter. `ralph-status` is rewritten around the unified queue + `target_repo` grouping.

**Tech Stack:** Python 3.12, existing dependencies. Reuses `ensure_queue_clone` from PBI 1.

**Spec reference:** `docs/superpowers/specs/2026-05-28-queue-repo-split-design.md`

**Depends on:** `EXECUTOR-QUEUE-REPO-SPLIT` must be merged first. The queue repo must already exist (operator ran `migrate-queue` after PBI 1 landed).

---

## File map

**Modify:**
- `scripts/queue_writer.py` — drop `checkout_queue_branch`; thin wrappers around the queue clone for skill use.
- `skills/ralph-add/scripts/add.py` + `skills/ralph-add/SKILL.md`
- `skills/ralph-cancel/scripts/cancel.py` + `skills/ralph-cancel/SKILL.md`
- `skills/ralph-promote/scripts/promote.py` + `skills/ralph-promote/SKILL.md`
- `skills/ralph-triage/scripts/triage.py` + `skills/ralph-triage/SKILL.md`
- `skills/ralph-status/scripts/status.py` + `skills/ralph-status/SKILL.md` (deeper rewrite)
- `README.md` — Install + new "Working the queue" section.
- `tests/skills/test_ralph_add.py`, `tests/skills/test_ralph_cancel.py`, `tests/skills/test_ralph_promote.py`, `tests/skills/test_ralph_triage.py`, `tests/skills/test_ralph_status.py`
- `tests/test_queue_writer.py`

**Create:**
- `docs/superpowers/ops/2026-05-28-pod-deployment.md` — runbook for running ralph in a container.

---

## Task 0: Spike — read each skill's current entry point

**Confidence: 100%** — non-edit step.

**Files:** (read-only)

- [ ] **Step 1: Read every `scripts/*.py` skill end-to-end**

```bash
for f in skills/ralph-add/scripts/add.py skills/ralph-cancel/scripts/cancel.py skills/ralph-promote/scripts/promote.py skills/ralph-triage/scripts/triage.py skills/ralph-status/scripts/status.py; do
  echo "===== $f ====="
  uv run python -c "print(open('$f').read())"
done
```

Note for each:
- Where `--repo` / `--branch` are parsed.
- Where `checkout_queue_branch` is called.
- Where `--repo` is used as a *target* identifier vs. where it's used as the *queue* location.
- For `ralph-status`: the `_extract_queue_snapshot` worktree-creation machinery (deletable).

- [ ] **Step 2: Skim `scripts/queue_writer.py`**

```bash
uv run python -c "print(open('scripts/queue_writer.py').read())"
```

Identify the public surface other than `checkout_queue_branch`: commit helpers, push helpers, idempotency wrappers. Most of those stay; the branch-checkout is the only thing that goes.

No commit for Task 0.

---

## Task 1: `scripts/queue_writer.py` — swap `checkout_queue_branch` for `acquire_queue_clone`

**Confidence: 95%** — thin re-export of PBI 1's `ensure_queue_clone`. No new logic.

**Files:**
- Modify: `scripts/queue_writer.py`
- Test: `tests/test_queue_writer.py`

- [x] **Step 1: Write the failing test**

Append to `tests/test_queue_writer.py`:

```python
def test_acquire_queue_clone_returns_path(tmp_path, monkeypatch):
    """acquire_queue_clone returns a Path to the queue clone."""
    from scripts.queue_writer import acquire_queue_clone

    seen: dict = {}

    def fake_ensure(workspace_root, queue_repo, *, timeout=120.0):
        seen["workspace"] = workspace_root
        seen["queue_repo"] = queue_repo
        return workspace_root / "queue"

    monkeypatch.setattr("scripts.queue_writer.ensure_queue_clone", fake_ensure)

    path = acquire_queue_clone(tmp_path, "https://github.com/example/q")
    assert path == tmp_path / "queue"
    assert seen == {"workspace": tmp_path, "queue_repo": "https://github.com/example/q"}


def test_checkout_queue_branch_is_removed():
    """The old helper must no longer exist (no compat shim)."""
    import scripts.queue_writer as qw
    assert not hasattr(qw, "checkout_queue_branch")
```

- [x] **Step 2: Run; expect FAIL**

- [x] **Step 3: Edit `scripts/queue_writer.py`**

- Delete the `checkout_queue_branch` function.
- Delete any module-level `DEFAULT_QUEUE_BRANCH` constant.
- Add at the top:

```python
from ralph_executor.queue_clone import ensure_queue_clone
```

- Add a public wrapper:

```python
def acquire_queue_clone(workspace_root, queue_repo, *, timeout=120.0):
    """Idempotent queue clone for operator skills.

    Thin wrapper around ralph_executor.queue_clone.ensure_queue_clone so
    skills can import a stable surface from this module without taking a
    dependency on the executor package layout directly.
    """
    return ensure_queue_clone(workspace_root, queue_repo, timeout=timeout)
```

- [x] **Step 4: Run the tests; expect PASS**

```bash
uv run pytest tests/test_queue_writer.py -v
```

- [x] **Step 5: Commit**

```bash
git add scripts/queue_writer.py tests/test_queue_writer.py
git commit -m "refactor(queue_writer): acquire_queue_clone replaces checkout_queue_branch"
```

---

## Task 2: `ralph-add` — `--target-repo` only, write to queue clone

**Confidence: 93%** — small interface change; templating/commit/push logic in `add.py` is unchanged. Only deletions + arg-parser edits.

**Reference (current `add.py` head, do not change behaviour of validators):**

```python
# skills/ralph-add/scripts/add.py
DEFAULT_QUEUE_BRANCH = "ralph-queue"           # delete this line
ATTACHMENT_SUBDIR = "attachments"               # keep
ALLOWED_SEVERITIES = ("critical", "high", "normal", "low")  # keep

from scripts.queue_writer import (
    QueueWriterError,
    checkout_queue_branch,                      # delete this import
    commit_paths,
    ensure_git_repo,                            # delete (no longer applicable — queue clone always exists after acquire)
    push,                                       # keep — pushes to origin/main now
)
# add: from scripts.queue_writer import acquire_queue_clone
```

The `validate` schema validator + `attachments` handling + commit message style are unchanged.

**Files:**
- Modify: `skills/ralph-add/scripts/add.py`, `skills/ralph-add/SKILL.md`
- Test: `tests/skills/test_ralph_add.py`

- [ ] **Step 1: Update the test fixture to use a fake remote queue repo**

In `tests/skills/test_ralph_add.py`, replace fixtures that built a target-repo-with-ralph-queue-branch with fixtures that build:
- A bare git repo (the fake queue remote).
- An empty workspace dir.
- Tests assert: after running `ralph-add --target-repo <fake-url> --pbi-id WI-1`, the fake bare remote has a commit on `main` containing `.ralph/inbox/WI-1/PBI.md`.

Example skeleton (full code in the file):

```python
def test_add_pbi_writes_to_queue_clone(tmp_path):
    queue_remote = _make_bare(tmp_path / "queue.git")
    workspace = tmp_path / "ws"
    result = run_ralph_add(
        ["--target-repo", "https://github.com/example/svc",
         "--pbi-id", "WI-1", "--title", "test"],
        env={"RALPH_WORKSPACE": str(workspace), "RALPH_QUEUE_REPO": f"file://{queue_remote}"},
    )
    assert result.returncode == 0
    verify = tmp_path / "verify"
    subprocess.run(["git", "clone", str(queue_remote), str(verify)], check=True, capture_output=True)
    pbi = verify / ".ralph" / "inbox" / "WI-1" / "PBI.md"
    assert pbi.exists()
    assert "target_repo: https://github.com/example/svc" in pbi.read_text(encoding="utf-8")
```

Note: env-var routing in tests is fine for tests; production reads from `~/.ralph/config.toml`. Tests use a monkeypatch fixture that points the skill at the test workspace + queue. **No new env vars in production code** — tests are the one exception.

- [ ] **Step 2: Run; expect FAIL**

- [ ] **Step 3: Edit `skills/ralph-add/scripts/add.py`**

- Remove `--repo`, `--branch`, `DEFAULT_QUEUE_BRANCH`, and any worktree-creation helpers that exist only to swap branches.
- Add `--target-repo <url>` argument. Validate via `parse_target_repo`.
- At entry, resolve config:
  - `workspace_root` and `queue_repo` from `~/.ralph/config.toml` (reuse `ralph_executor.user_config` if available).
  - `--workspace` / `--queue-repo` flags override TOML.
- Call `acquire_queue_clone(workspace_root, queue_repo)` to materialise the clone.
- All file writes target `<clone>/.ralph/inbox/<id>/`.
- The PBI's frontmatter `target_repo` field comes from `--target-repo`.
- Commit message style unchanged: `chore(queue): add <id>`.
- Push to `origin/main`.

- [ ] **Step 4: Update `skills/ralph-add/SKILL.md`** to document the new `--target-repo` flag and remove `--repo` / `--branch`.

- [ ] **Step 5: Run; expect PASS**

```bash
uv run pytest tests/skills/test_ralph_add.py -v
```

- [ ] **Step 6: Commit**

```bash
git add skills/ralph-add/ tests/skills/test_ralph_add.py
git commit -m "feat(ralph-add): operate on queue clone; --target-repo replaces --repo/--branch"
```

---

## Task 3: `ralph-cancel` — `--pbi-id` only

**Confidence: 92%** — drops the target/branch plumbing; behaviour identical otherwise.

**Files:**
- Modify: `skills/ralph-cancel/scripts/cancel.py`, `skills/ralph-cancel/SKILL.md`
- Test: `tests/skills/test_ralph_cancel.py`

- [ ] **Step 1: Update tests to use the fake-queue-remote pattern**

Same shape as Task 2's test rewrite.

- [ ] **Step 2: Run; expect FAIL**

- [ ] **Step 3: Edit `skills/ralph-cancel/scripts/cancel.py`**

- Remove `--repo`, `--branch`.
- Resolve `workspace_root` + `queue_repo` from config.
- `acquire_queue_clone` to get the clone path.
- Operate on `<clone>/.ralph/current/<pbi-id>/` (write CANCEL sentinel, commit, push to main).

- [ ] **Step 4: Update SKILL.md.**

- [ ] **Step 5: Run; expect PASS**

```bash
uv run pytest tests/skills/test_ralph_cancel.py -v
```

- [ ] **Step 6: Commit**

```bash
git add skills/ralph-cancel/ tests/skills/test_ralph_cancel.py
git commit -m "feat(ralph-cancel): operate on queue clone; --pbi-id only"
```

---

## Task 4: `ralph-promote` — state args only

**Confidence: 92%** — drops the target/branch plumbing; behaviour identical otherwise.

**Files:**
- Modify: `skills/ralph-promote/scripts/promote.py`, `skills/ralph-promote/SKILL.md`
- Test: `tests/skills/test_ralph_promote.py`

- [ ] **Step 1: Update the test fixture to use a fake remote queue repo**

In `tests/skills/test_ralph_promote.py`, replace fixtures that built a target-repo-with-ralph-queue-branch with fixtures that build:
- A bare git repo (the fake queue remote) seeded with a PBI in the source state folder.
- An empty workspace dir.
- Test: after running `ralph-promote --pbi-id <id> --from inbox --to current`, the fake bare remote has a commit moving the PBI dir from `.ralph/inbox/<id>/` to `.ralph/current/<id>/` on `main`.

- [ ] **Step 2: Run; expect FAIL**

```bash
uv run pytest tests/skills/test_ralph_promote.py -v
```

- [ ] **Step 3: Edit `skills/ralph-promote/scripts/promote.py`**

- Remove `--repo`, `--branch`, `DEFAULT_QUEUE_BRANCH`, and any worktree-creation helpers.
- Keep `--pbi-id` and the `--from` / `--to` state arguments.
- At entry, resolve `workspace_root` + `queue_repo` from `~/.ralph/config.toml` (with `--workspace` / `--queue-repo` overrides).
- Call `acquire_queue_clone(workspace_root, queue_repo)`.
- `git mv` the PBI dir between state folders inside the clone, rewrite frontmatter `status:` and `updated_at:`, commit with the existing message style, push to `origin/main`.

- [ ] **Step 4: Update `skills/ralph-promote/SKILL.md`** to reflect the new argument shape (`--pbi-id`, `--from`, `--to`).

- [ ] **Step 5: Run; expect PASS**

```bash
uv run pytest tests/skills/test_ralph_promote.py -v
```

- [ ] **Step 6: Commit**

```bash
git add skills/ralph-promote/ tests/skills/test_ralph_promote.py
git commit -m "feat(ralph-promote): operate on queue clone"
```

---

## Task 5: `ralph-triage` — routing args only

**Confidence: 92%** — drops the target/branch plumbing; behaviour identical otherwise.

**Files:**
- Modify: `skills/ralph-triage/scripts/triage.py`, `skills/ralph-triage/SKILL.md`
- Test: `tests/skills/test_ralph_triage.py`

- [ ] **Step 1: Update the test fixture to use a fake remote queue repo**

In `tests/skills/test_ralph_triage.py`, replace fixtures that built a target-repo-with-ralph-queue-branch with fixtures that build:
- A bare git repo (the fake queue remote) seeded with a PBI in `.ralph/blocked/<id>/`.
- An empty workspace dir.
- Test: after running `ralph-triage --pbi-id <id> --to inbox`, the fake bare remote has a commit moving the PBI dir from `.ralph/blocked/<id>/` to `.ralph/inbox/<id>/`, with `attempts: 0` rewritten in the entry-file frontmatter.

- [ ] **Step 2: Run; expect FAIL**

```bash
uv run pytest tests/skills/test_ralph_triage.py -v
```

- [ ] **Step 3: Edit `skills/ralph-triage/scripts/triage.py`**

- Remove `--repo`, `--branch`, `DEFAULT_QUEUE_BRANCH`, and any worktree-creation helpers.
- Keep `--pbi-id` and the `--to {inbox,archive}` routing argument.
- At entry, resolve `workspace_root` + `queue_repo` from `~/.ralph/config.toml` (with `--workspace` / `--queue-repo` overrides).
- Call `acquire_queue_clone(workspace_root, queue_repo)`.
- `git mv` the PBI dir from `<clone>/.ralph/blocked/<id>/` to either `<clone>/.ralph/inbox/<id>/` (with `attempts: 0` rewrite in the entry file) or `<clone>/.ralph/archive/<id>/` (creating the archive folder on demand). Commit and push to `origin/main`.

- [ ] **Step 4: Update `skills/ralph-triage/SKILL.md`** to reflect the new argument shape (`--pbi-id`, `--to`).

- [ ] **Step 5: Run; expect PASS**

```bash
uv run pytest tests/skills/test_ralph_triage.py -v
```

- [ ] **Step 6: Commit**

```bash
git add skills/ralph-triage/ tests/skills/test_ralph_triage.py
git commit -m "feat(ralph-triage): operate on queue clone"
```

---

## Task 6 (split into 6a–6d): `ralph-status` — full rewrite around the single queue clone

Original 80% confidence Task 6 split into four single-responsibility subtasks. Each subtask is small, testable in isolation, and lands as its own commit.

---

## Task 6a: Delete obsolete machinery in `ralph-status`

**Confidence: 95%** — pure deletion.

**Files:**
- Modify: `skills/ralph-status/scripts/status.py`

- [ ] **Step 1: Delete the following functions / dataclasses / constants:**

- `DEFAULT_QUEUE_BRANCH`
- `RepoConfig` dataclass
- `RepoSnapshot` dataclass
- `_load_repos_config`
- `_run_git`, `_is_git_repo`, `_ensure_branch_exists`, `_resolve_branch_ref`
- `_create_worktree`, `_remove_worktree`
- `_extract_queue_snapshot`

These are all the worktree-per-repo machinery that the new model doesn't need.

- [ ] **Step 2: Smoke check — imports should now be smaller**

```bash
grep -n "^import\|^from" skills/ralph-status/scripts/status.py
```

Expected: `argparse`, `json`, `sys`, `dataclasses`, `datetime`, `pathlib`, `STATE_FOLDERS` / `PBIRow` / `PBIRowError` / `enumerate_state` from `scripts.pbi_reader`. The `subprocess`, `shutil`, `tempfile`, `os`, `contextlib` imports drop.

- [ ] **Step 3: Commit**

```bash
git add skills/ralph-status/scripts/status.py
git commit -m "refactor(ralph-status): delete multi-repo worktree machinery"
```

Note: the file is in a broken state between 6a and 6b — argparse references the deleted helpers. **Do not run tests yet.** 6b restores buildability.

---

## Task 6b: New argument parser

**Confidence: 95%** — argparse is mechanical.

**Files:**
- Modify: `skills/ralph-status/scripts/status.py`

- [ ] **Step 1: Replace `_parse_args` with the new shape**

```python
def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ralph-status",
        description=(
            "Read-only view of the ralph queue. Reads "
            "$RALPH_WORKSPACE/queue/.ralph/ and groups output by target_repo."
        ),
    )
    parser.add_argument(
        "--state",
        choices=STATE_FOLDERS,
        help="Filter rows to a single state.",
    )
    parser.add_argument(
        "--target-repo",
        help="Filter rows to PBIs whose target_repo matches this URL.",
    )
    parser.add_argument(
        "--json",
        dest="emit_json",
        action="store_true",
        help="Emit JSON to stdout instead of a fixed-width table.",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        help="Override workspace_root from ~/.ralph/config.toml.",
    )
    parser.add_argument(
        "--queue-repo",
        help="Override queue_repo from ~/.ralph/config.toml.",
    )
    return parser.parse_args(argv)
```

- [ ] **Step 2: Update `_main` to resolve workspace_root and queue_repo from `~/.ralph/config.toml`** (use `ralph_executor.user_config.load_user_config` or equivalent; if a wrapper doesn't exist, read the TOML directly with `tomllib`).

- [ ] **Step 3: Smoke check the parser**

```bash
uv run python skills/ralph-status/scripts/status.py --help
```

Expected: help text matches the new shape (no `--repo` / `--repos-file` / `--branch` / `--no-cleanup`).

- [ ] **Step 4: Commit**

```bash
git add skills/ralph-status/scripts/status.py
git commit -m "feat(ralph-status): new argument parser; resolve queue from TOML"
```

---

## Task 6c: `target_repo` grouping + table renderer

**Confidence: 93%** — replaces existing renderer; algorithm is sort + group.

**Files:**
- Modify: `skills/ralph-status/scripts/status.py`

- [ ] **Step 1: Replace `_COLUMN_ORDER`**

```python
_COLUMN_ORDER: tuple[str, ...] = (
    "TARGET",
    "STATE",
    "ID",
    "TYPE",
    "SEVERITY",
    "AGE",
    "TITLE",
)
```

- [ ] **Step 2: Replace `_row_to_cells`**

```python
def _row_to_cells(row: PBIRow | PBIRowError) -> list[str]:
    target = (row.target_repo or "?") if isinstance(row, PBIRow) else "?"
    # Truncate target URL to a readable length in terminal output.
    target_display = target if len(target) <= 50 else target[:47] + "..."
    if isinstance(row, PBIRow):
        return [
            target_display,
            row.state,
            row.pbi_id,
            row.pbi_type,
            row.severity,
            _age_string(row.created_at),
            row.title,
        ]
    return [
        target_display,
        row.state,
        row.pbi_dir.name,
        "?", "?", "?",
        f"(parse error) {row.message}",
    ]
```

Note: `target_repo` was added to `PBIRow` by `RALPH-PBI-TARGET-REPO-FIELD` (#40). It is a string field on every PBI's frontmatter.

- [ ] **Step 3: Add `_group_and_sort` to order rows**

```python
def _group_and_sort(rows: list[PBIRow | PBIRowError]) -> list[PBIRow | PBIRowError]:
    """Stable sort by (target_repo, state, created_at) — preserves grouping in output."""
    def key(row: PBIRow | PBIRowError) -> tuple[str, str, str]:
        if isinstance(row, PBIRow):
            return (row.target_repo or "", row.state, row.created_at.isoformat() if row.created_at else "")
        return ("", row.state, "")
    return sorted(rows, key=key)
```

Call `_group_and_sort` before `_render_table`.

- [ ] **Step 4: Update `_main` to apply the `--target-repo` filter** after collecting rows, before grouping.

- [ ] **Step 5: Commit**

```bash
git add skills/ralph-status/scripts/status.py
git commit -m "feat(ralph-status): group by target_repo; new TARGET column"
```

---

## Task 6d: JSON shape + filter wiring + tests

**Confidence: 92%** — final assembly + test rewrites.

**Files:**
- Modify: `skills/ralph-status/scripts/status.py`
- Test: `tests/skills/test_ralph_status.py`

- [ ] **Step 1: Replace `_row_to_json`**

```python
def _row_to_json(row: PBIRow | PBIRowError) -> dict[str, object]:
    if isinstance(row, PBIRow):
        return {
            "target_repo": row.target_repo,
            "state": row.state,
            "id": row.pbi_id,
            "type": row.pbi_type,
            "severity": row.severity,
            "attempts": row.attempts,
            "created_at": _iso(row.created_at),
            "updated_at": _iso(row.updated_at),
            "title": row.title,
            "pbi_dir": row.relative_pbi_dir(),
            "error": None,
        }
    return {
        "target_repo": None,
        "state": row.state,
        "id": row.pbi_dir.name,
        "type": None,
        "severity": None,
        "attempts": None,
        "created_at": None,
        "updated_at": None,
        "title": None,
        "pbi_dir": str(row.pbi_dir),
        "error": row.message,
    }
```

Top-level JSON envelope: `{"rows": [...], "errors": []}`. No `repos` array (single queue).

- [ ] **Step 2: Rewrite `tests/skills/test_ralph_status.py`**

Replace all multi-repo fixtures with single-queue-clone fixtures:
- Bare git repo as the queue remote.
- Seeded with 3-4 PBIs across `inbox/`, `current/`, `pending-pr/`, each with distinct `target_repo` values.
- Tests:
  - Default: all PBIs listed, grouped by `target_repo`.
  - `--state current`: only current rows.
  - `--target-repo X`: only rows for X.
  - `--json`: new envelope shape; no `repos` key; rows have `target_repo`.

- [ ] **Step 3: Run; expect PASS**

```bash
uv run pytest tests/skills/test_ralph_status.py -v
```

- [ ] **Step 4: Commit**

```bash
git add skills/ralph-status/ tests/skills/test_ralph_status.py
git commit -m "feat(ralph-status): new JSON shape; filter wiring; rewrite tests"
```

**Files:**
- Modify: `skills/ralph-status/scripts/status.py`, `skills/ralph-status/SKILL.md`
- Test: `tests/skills/test_ralph_status.py`

**New argument shape:**

```
ralph-status [--state STATE] [--target-repo URL] [--json] [--workspace PATH] [--queue-repo URL]
```

- `--state`: unchanged. Filter rows.
- `--target-repo URL`: new optional filter. If set, only show PBIs whose `target_repo == URL`.
- `--json`: unchanged.
- `--workspace`, `--queue-repo`: config overrides; default to TOML.

Default output groups rows by `target_repo`:

```
TARGET                                          STATE       ID        TYPE     SEVERITY  AGE   TITLE
https://github.com/emp3thy/svc-auth             inbox       WI-1234   feature  normal    2h    Add /healthz
https://github.com/emp3thy/svc-auth             current     WI-1235   bug      critical  1h    Pod crashloops
https://github.com/emp3thy/svc-billing          pending-pr  WI-980    feature  high      6h    Migrate invoices
```

(Column 1 is the long URL; truncate to 50 chars max in the terminal renderer.)

- [ ] **Step 1: Replace tests**

In `tests/skills/test_ralph_status.py`:
- Delete fixtures that built target-repo-with-ralph-queue-branch.
- New fixture: a bare remote queue repo with `.ralph/{inbox,current,pending-pr,blocked}/` seeded with several PBIs whose `target_repo` fields differ.
- Tests:
  - Default invocation shows all PBIs grouped by `target_repo`.
  - `--state current` filters correctly.
  - `--target-repo X` filters correctly.
  - `--json` emits the new shape (no `repo` path key; `target_repo` present).

- [ ] **Step 2: Run; expect FAIL**

- [ ] **Step 3: Rewrite `skills/ralph-status/scripts/status.py`**

Delete:
- `DEFAULT_QUEUE_BRANCH`, `RepoConfig`, `RepoSnapshot`.
- `_load_repos_config`, `_run_git`, `_is_git_repo`, `_ensure_branch_exists`, `_resolve_branch_ref`, `_create_worktree`, `_remove_worktree`, `_extract_queue_snapshot`.
- All argparse handling of `--repo`, `--repos-file`, `--branch`, `--no-cleanup`.

Keep / rework:
- `_age_string`, `_iso`, `_row_to_cells`, `_row_to_json`, `_render_table` (drop the `REPO` column → `TARGET` column).
- `enumerate_state` from `scripts.pbi_reader` — call it once against `<queue-clone>/.ralph/`.

New entry-point flow:
1. Parse args.
2. Resolve `workspace_root` + `queue_repo` from `~/.ralph/config.toml` (with `--workspace` / `--queue-repo` overrides).
3. `queue_clone = acquire_queue_clone(workspace_root, queue_repo)`.
4. Walk `STATE_FOLDERS` against `queue_clone`. Build a single list of rows.
5. Filter by `--state` and `--target-repo` if set.
6. Group by `target_repo` (stable sort by `target_repo`, then `state`, then `created_at`).
7. Render table or JSON.

JSON shape:

```json
{
  "rows": [
    {
      "target_repo": "https://github.com/emp3thy/svc-auth",
      "state": "inbox",
      "id": "WI-1234",
      "type": "feature",
      "severity": "normal",
      "attempts": 0,
      "created_at": "2026-05-24T09:15:00+00:00",
      "updated_at": "2026-05-24T09:15:00+00:00",
      "title": "Add /healthz endpoint",
      "pbi_dir": ".ralph/inbox/WI-1234",
      "error": null
    }
  ],
  "errors": []
}
```

- [ ] **Step 4: Rewrite `skills/ralph-status/SKILL.md`**

Replace the "service repo + ralph-queue branch" model description with:
- "Reads the single queue clone at `$RALPH_WORKSPACE/queue/`."
- Updated input table (no `--repo`/`--repos-file`/`--branch`/`--no-cleanup`; add `--target-repo`).
- Example output with the new `TARGET` column.
- "When to use it" unchanged.

- [ ] **Step 5: Run; expect PASS**

```bash
uv run pytest tests/skills/test_ralph_status.py -v
```

- [ ] **Step 6: Commit**

```bash
git add skills/ralph-status/ tests/skills/test_ralph_status.py
git commit -m "feat(ralph-status): rewrite around single queue clone, group by target_repo"
```

---

## Task 7: SKILL.md sweep — consistency check

**Confidence: 95%** — content covered in Tasks 2–6; this is a pass to catch stragglers.

**Files:** every `skills/ralph-*/SKILL.md`

- [ ] **Step 1: Grep for stale model references**

```bash
grep -rn "ralph-queue branch\|--branch\|--repos-file\|service repo" skills/ralph-*/SKILL.md
```

- [ ] **Step 2: Fix any straggler**

- [ ] **Step 3: Commit (if anything changed)**

```bash
git add skills/ralph-*/SKILL.md
git commit -m "docs(skills): sweep stale 'ralph-queue branch' references"
```

---

## Task 8: README rewrite — Install + Working the queue

**Confidence: 85%** — substantial doc edit.

**Mitigation:** The exact new section content is included below as a verbatim template. The implementer does not draft the prose freehand.

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Replace the existing Install + One-time setup sections** with:

```markdown
## Install

You have two options.

### Workstation (recommended for development)

```bash
git clone https://github.com/emp3thy/ralph.git
cd ralph
uv sync
```

This creates the venv and installs `ralph-executor` into it. Run it
through `uv run`:

```bash
uv run ralph-executor --help
```

### Container (recommended for unattended pod deployment)

Pull the ROSA image (Phase 1 — GitHub variant):

```bash
docker pull <registry>/ralph-executor:<tag>
```

See `docs/superpowers/ops/2026-05-28-pod-deployment.md` for the full
pod runbook.

## One-time setup

Ralph reads its config from `~/.ralph/config.toml`. The `init`
subcommand creates it interactively:

```bash
uv run ralph-executor init
```

You will be prompted for three values:

| Key | What it is | Example |
|---|---|---|
| `ralph_home` | Root for legacy single-checkout state (kept for compatibility) | `~/dev/ralph` |
| `workspace_root` | Where ralph clones the queue and target repos | `~/ralph-workspaces` |
| `queue_repo` | HTTPS URL of the queue repo holding `.ralph/` state | `https://github.com/emp3thy/ralph-queue` |

For scripted setup:

```bash
uv run ralph-executor init \
  --ralph-home /opt/ralph \
  --workspace-root /opt/ralph/workspaces \
  --queue-repo https://github.com/emp3thy/ralph-queue \
  --yes
```

`init` smoke-tests the queue URL by attempting a clone. If your network
or auth is flaky it will print a warning but still write the config —
the executor will retry on its next iteration.
```

- [ ] **Step 2: Add a new section** after One-time setup:

```markdown
## Working the queue

Five skills cover the operator workflow. All read or write
`<workspace_root>/queue/.ralph/` (the queue clone) and never touch the
target repos themselves.

### See what's in flight: `ralph-status`

```bash
uv run python skills/ralph-status/scripts/status.py
```

Output (grouped by target repo):

```
TARGET                                  STATE       ID        TYPE     SEVERITY  AGE   TITLE
https://github.com/emp3thy/svc-auth     inbox       WI-1234   feature  normal    2h    Add /healthz
https://github.com/emp3thy/svc-auth     current     WI-1235   bug      critical  1h    Pod crashloops
https://github.com/emp3thy/svc-billing  pending-pr  WI-980    feature  high      6h    Migrate invoices
```

Filters:
- `--state {inbox,current,pending-pr,done,blocked}` — narrow to one state.
- `--target-repo <url>` — narrow to one target.
- `--json` — machine-readable.

### Add a PBI: `ralph-add`

```bash
uv run python skills/ralph-add/scripts/add.py \
  --target-repo https://github.com/emp3thy/svc-auth \
  --pbi-id WI-1234 \
  --title "Add /healthz endpoint" \
  --type feature \
  --severity normal
```

The skill writes the PBI to the queue clone's `inbox/` and pushes.

### Cancel a PBI: `ralph-cancel`

Writes a CANCEL sentinel into `current/<id>/`. The executor picks it up
on the next iteration and moves the PBI out.

```bash
uv run python skills/ralph-cancel/scripts/cancel.py --pbi-id WI-1234
```

### Promote a PBI: `ralph-promote`

Move a PBI between state folders (e.g. from `inbox/` to `current/` to
manually queue it for the next iteration).

```bash
uv run python skills/ralph-promote/scripts/promote.py \
  --pbi-id WI-1234 --from inbox --to current
```

### Triage a blocked PBI: `ralph-triage`

Route a `blocked/` PBI either back to `inbox/` (with attempts reset) or
out to `archive/`.

```bash
uv run python skills/ralph-triage/scripts/triage.py --pbi-id WI-1234 --to inbox
```
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs(readme): queue-repo install + working-the-queue section"
```

---

## Task 9: New ops doc — pod deployment

**Confidence: 85%** — full doc; skeleton below is verbatim.

**Files:**
- Create: `docs/superpowers/ops/2026-05-28-pod-deployment.md`

- [ ] **Step 1: Write the file**

```markdown
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
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/ops/2026-05-28-pod-deployment.md
git commit -m "docs(ops): pod deployment runbook for ralph-executor"
```

---

## Task 10: Lint, type, format, full suite

**Confidence: 95%** — gate.

- [ ] **Step 1: ruff check + format**

```bash
uv run ruff check .
uv run ruff format --check .
```

- [ ] **Step 2: mypy**

```bash
uv run mypy ralph_executor scripts skills tests
```

- [ ] **Step 3: Full pytest**

```bash
uv run pytest -q
```

- [ ] **Step 4: Open the PR**

```bash
git push -u origin ralph/SKILLS-QUEUE-CLONE-MIGRATION
```

Use the `pr` skill or `gh pr create`. PR description references this
plan and the spec.

---

## Done criteria

- `scripts/queue_writer.py` exposes `acquire_queue_clone` (no `checkout_queue_branch`).
- Each of the five skills no longer takes `--repo` / `--branch` / `--repos-file`. `ralph-add` takes `--target-repo`; `ralph-status` takes `--target-repo` as an optional filter.
- `ralph-status` table is grouped by `target_repo` column.
- README has a new "Working the queue" section covering all five skills.
- `docs/superpowers/ops/2026-05-28-pod-deployment.md` exists.
- Full lint + type + test suite green.
- PR opened from `ralph/SKILLS-QUEUE-CLONE-MIGRATION` against `main`.
