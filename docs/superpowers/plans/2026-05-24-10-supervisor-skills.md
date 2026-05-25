# Remaining Supervisor Skills Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the three remaining supervisor skills — `ralph-cancel`, `ralph-promote`, `ralph-triage` — that round out the BA / PM / triager surface. Each skill mutates the `ralph-queue` branch of a target service repo via a small Python entry script. `ralph-cancel` drops a `CANCEL` sentinel file into the PBI directory currently in `.ralph/current/` (the only mutation we allow on `current/`). `ralph-promote` bumps a PBI's `severity` frontmatter field on whichever queue folder the PBI sits in. `ralph-triage` walks the `blocked/` queue and routes a PBI either back to `inbox/` (with `attempts` reset to 0 and a triage note appended to `HISTORY.md`) or out to `archive/` (a new top-level folder under `.ralph/`, created on demand). All three skills share a small `scripts/queue_writer.py` module that wraps the common git dance (resolve repo, fetch, checkout `ralph-queue`, mutate, commit, push). The refactor pulls the equivalent helpers out of Plan 3's `ralph-add` script so the four supervisor skills stay consistent.

**Architecture:** A single shared module at `scripts/queue_writer.py` (sibling to `scripts/ado_client.py` and `scripts/validate_samples.py`) owns the queue-branch git workflow plus a minimal frontmatter read/update helper. The three skills follow Plan 3's layout exactly: each lives at `skills/ralph-<op>/`, ships a `SKILL.md` (frontmatter + body), and exposes one Python entry script at `skills/ralph-<op>/scripts/<op>.py`. The entry scripts are intentionally thin — they parse args, validate inputs, call into `scripts.queue_writer`, and print a stable JSON summary on stdout. Tests live at `tests/skills/test_ralph_cancel.py`, `tests/skills/test_ralph_promote.py`, `tests/skills/test_ralph_triage.py` and use the same `git_repo` pattern as Plan 3: a local bare repo + working clone built in `tmp_path` (no network, no real ADO). Plan 3's `add.py` is refactored at the end of this plan to consume `scripts.queue_writer`, removing the duplicated `_run_git`, `_ensure_git_repo`, `_checkout_queue_branch`, `_commit_pbi`, `_push` helpers it currently inlines.

**Tech Stack:** Python 3.12+, `uv`, `pyyaml`, `pytest`, ruff, mypy strict, `git` (called via `subprocess`).

---

## File Structure

| Path | Responsibility |
|---|---|
| `scripts/queue_writer.py` | Shared git + frontmatter helpers consumed by `ralph-add` (after the refactor), `ralph-cancel`, `ralph-promote`, `ralph-triage`. Public API: `ensure_git_repo`, `checkout_queue_branch`, `commit_paths`, `push`, `find_pbi_directory`, `read_frontmatter`, `write_frontmatter`, `append_history`, `QueueWriterError`. |
| `tests/test_queue_writer.py` | Unit tests for the shared helpers (frontmatter round-trip, history append, repo-not-a-repo error, PBI search across state folders). |
| `skills/ralph-cancel/SKILL.md` | Skill frontmatter + body documenting the cancel surface. |
| `skills/ralph-cancel/scripts/__init__.py` | Empty package marker so static tooling treats the directory as a package. |
| `skills/ralph-cancel/scripts/cancel.py` | Entry script: validates the PBI lives in `.ralph/current/<PBI-id>/`, writes an empty `CANCEL` file, commits + pushes. |
| `tests/skills/test_ralph_cancel.py` | Tests: cancel succeeds on a PBI in `current/`; refuses to cancel a PBI not in `current/`; refuses to cancel a missing PBI; `--no-push` does not push; `--dry-run` writes nothing; missing env (none required) is not a failure; the produced `CANCEL` is empty and the commit message matches. |
| `skills/ralph-promote/SKILL.md` | Skill frontmatter + body documenting the severity bump surface. |
| `skills/ralph-promote/scripts/__init__.py` | Empty package marker. |
| `skills/ralph-promote/scripts/promote.py` | Entry script: locates the PBI across `inbox/current/pending-pr/blocked/done/archive`, reads frontmatter, updates `severity` and `updated_at`, writes back, commits + pushes. |
| `tests/skills/test_ralph_promote.py` | Tests: promotes a PBI in `inbox/` from `normal` → `high`; rejects an unknown severity; rejects a missing PBI; preserves all other frontmatter keys; `updated_at` is refreshed; `--no-push` works; promotes a bug PBI (entry file is `BUG.md`). |
| `skills/ralph-triage/SKILL.md` | Skill frontmatter + body documenting the triage surface (`--to inbox` vs `--to archive`). |
| `skills/ralph-triage/scripts/__init__.py` | Empty package marker. |
| `skills/ralph-triage/scripts/triage.py` | Entry script: locates the PBI in `.ralph/blocked/<PBI-id>/`, moves it to `inbox/` (and resets `attempts`) or to `archive/` (created on demand), appends a HISTORY.md note containing the destination and the operator-supplied `--note`. |
| `tests/skills/test_ralph_triage.py` | Tests: routes a PBI from `blocked/` back to `inbox/` with `attempts` reset; routes a PBI from `blocked/` to `archive/` (creating the folder); rejects a destination other than `inbox`/`archive`; rejects a missing PBI; rejects a PBI not in `blocked/`; appends a structured HISTORY.md entry containing the note; `--note` is required. |
| `skills/ralph-add/scripts/add.py` | Modified to import the shared helpers from `scripts.queue_writer`, removing the inlined `_run_git` / `_ensure_git_repo` / `_checkout_queue_branch` / `_commit_pbi` / `_push` block. Public behaviour is unchanged. |

---

## Task 1 — Build the shared `scripts/queue_writer.py` module (TDD)

**Files**
- Create: `tests/test_queue_writer.py`
- Create: `scripts/queue_writer.py`

**Steps**

- [ ] 1. Confirm Plans 1, 2, and 3 are merged. Run:
  ```
  uv run pytest tests/test_workspace_samples.py tests/test_ado_client.py tests/test_setup_ralph_queue.py tests/skills/test_ralph_add.py -v
  ```
  Expected: every test passes. If any fail, STOP — Plan 10 depends on Plan 3's `git_repo` fixture conventions, Plan 1's frontmatter schema, and Plan 2's `ralph-queue` runbook.

- [ ] 2. Write the failing test file `tests/test_queue_writer.py` with the exact content below. The test imports `scripts.queue_writer`, which does not yet exist, so collection fails with `ModuleNotFoundError`:
  ```python
  """Unit tests for the shared queue-writer helpers.

  These helpers back the four supervisor skills (``ralph-add``,
  ``ralph-cancel``, ``ralph-promote``, ``ralph-triage``) and own all
  filesystem + git mutations on the ``ralph-queue`` branch.

  Every test builds a local bare repo + working clone in ``tmp_path`` so
  pushes are real (to a real local remote) and there is no network.
  """
  from __future__ import annotations

  import subprocess
  from collections.abc import Iterator
  from pathlib import Path

  import pytest

  from scripts.queue_writer import (
      QueueWriterError,
      append_history,
      checkout_queue_branch,
      commit_paths,
      ensure_git_repo,
      find_pbi_directory,
      push,
      read_frontmatter,
      write_frontmatter,
  )


  def _git(cwd: Path, *args: str) -> str:
      result = subprocess.run(
          ["git", *args],
          cwd=str(cwd),
          check=True,
          capture_output=True,
          text=True,
      )
      return result.stdout


  @pytest.fixture
  def git_repo(tmp_path: Path) -> Iterator[Path]:
      bare = tmp_path / "remote.git"
      work = tmp_path / "work"
      subprocess.run(["git", "init", "--bare", str(bare)], check=True)
      subprocess.run(["git", "init", str(work)], check=True)
      _git(work, "config", "user.email", "test@example.com")
      _git(work, "config", "user.name", "Test User")
      _git(work, "commit", "--allow-empty", "-m", "chore: initial main commit")
      _git(work, "branch", "-M", "main")
      _git(work, "remote", "add", "origin", str(bare))
      _git(work, "push", "-u", "origin", "main")
      _git(work, "checkout", "-b", "ralph-queue")
      _git(work, "commit", "--allow-empty", "-m", "chore(queue): bootstrap ralph-queue")
      _git(work, "push", "-u", "origin", "ralph-queue")
      _git(work, "checkout", "main")
      yield work


  def test_ensure_git_repo_accepts_real_repo(git_repo: Path) -> None:
      # Should not raise for a real .git directory.
      ensure_git_repo(git_repo)


  def test_ensure_git_repo_rejects_non_repo(tmp_path: Path) -> None:
      with pytest.raises(QueueWriterError) as exc:
          ensure_git_repo(tmp_path / "not_a_repo")
      assert "not a git repository" in str(exc.value).lower()


  def test_checkout_queue_branch_switches_to_existing_branch(
      git_repo: Path,
  ) -> None:
      checkout_queue_branch(git_repo, "ralph-queue")
      current = _git(git_repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
      assert current == "ralph-queue"


  def test_checkout_queue_branch_creates_local_tracking_when_only_remote_exists(
      git_repo: Path,
  ) -> None:
      # Delete the local ralph-queue ref to force the remote-tracking path.
      _git(git_repo, "checkout", "main")
      _git(git_repo, "branch", "-D", "ralph-queue")
      checkout_queue_branch(git_repo, "ralph-queue")
      current = _git(git_repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
      assert current == "ralph-queue"


  def test_checkout_queue_branch_errors_when_branch_absent(
      git_repo: Path,
  ) -> None:
      with pytest.raises(QueueWriterError) as exc:
          checkout_queue_branch(git_repo, "does-not-exist")
      msg = str(exc.value).lower()
      assert "does-not-exist" in msg


  def test_commit_paths_stages_and_records_commit(git_repo: Path) -> None:
      checkout_queue_branch(git_repo, "ralph-queue")
      pbi_dir = git_repo / ".ralph" / "inbox" / "WI-1"
      pbi_dir.mkdir(parents=True)
      (pbi_dir / "PBI.md").write_text("---\nid: WI-1\n---\n", encoding="utf-8")
      sha = commit_paths(
          git_repo,
          [pbi_dir],
          "feat(ralph-queue): add WI-1",
      )
      assert sha
      log = _git(git_repo, "log", "-1", "--pretty=%s").strip()
      assert log == "feat(ralph-queue): add WI-1"


  def test_push_advances_remote(git_repo: Path) -> None:
      checkout_queue_branch(git_repo, "ralph-queue")
      before = _git(git_repo, "ls-remote", "origin", "ralph-queue").strip()
      pbi_dir = git_repo / ".ralph" / "inbox" / "WI-2"
      pbi_dir.mkdir(parents=True)
      (pbi_dir / "PBI.md").write_text("---\nid: WI-2\n---\n", encoding="utf-8")
      commit_paths(git_repo, [pbi_dir], "feat(ralph-queue): add WI-2")
      push(git_repo, "ralph-queue")
      after = _git(git_repo, "ls-remote", "origin", "ralph-queue").strip()
      assert before != after


  def test_find_pbi_directory_scans_state_folders(git_repo: Path) -> None:
      checkout_queue_branch(git_repo, "ralph-queue")
      target = git_repo / ".ralph" / "blocked" / "WI-3"
      target.mkdir(parents=True)
      (target / "PBI.md").write_text("---\nid: WI-3\n---\n", encoding="utf-8")
      found = find_pbi_directory(git_repo, "WI-3")
      assert found == target


  def test_find_pbi_directory_returns_none_when_missing(git_repo: Path) -> None:
      checkout_queue_branch(git_repo, "ralph-queue")
      (git_repo / ".ralph" / "inbox").mkdir(parents=True, exist_ok=True)
      assert find_pbi_directory(git_repo, "WI-NOPE") is None


  def test_find_pbi_directory_prefers_current_over_other_states(
      git_repo: Path,
  ) -> None:
      checkout_queue_branch(git_repo, "ralph-queue")
      inbox = git_repo / ".ralph" / "inbox" / "WI-4"
      inbox.mkdir(parents=True)
      (inbox / "PBI.md").write_text("---\nid: WI-4\n---\n", encoding="utf-8")
      current = git_repo / ".ralph" / "current" / "WI-4"
      current.mkdir(parents=True)
      (current / "PBI.md").write_text("---\nid: WI-4\n---\n", encoding="utf-8")
      # current/ wins the search precedence.
      found = find_pbi_directory(git_repo, "WI-4")
      assert found == current


  def test_read_frontmatter_round_trips_through_write(tmp_path: Path) -> None:
      entry = tmp_path / "PBI.md"
      entry.write_text(
          "---\n"
          "id: WI-9\n"
          "type: feature\n"
          "status: inbox\n"
          "severity: normal\n"
          "attempts: 0\n"
          "created_at: 2026-05-24T10:00:00+00:00\n"
          "updated_at: 2026-05-24T10:00:00+00:00\n"
          "---\n"
          "\n"
          "# Body text\n",
          encoding="utf-8",
      )
      fm, body = read_frontmatter(entry)
      assert fm["id"] == "WI-9"
      assert fm["severity"] == "normal"
      assert body.startswith("\n# Body text")

      fm["severity"] = "high"
      fm["updated_at"] = "2026-05-24T11:00:00+00:00"
      write_frontmatter(entry, fm, body)

      fm2, body2 = read_frontmatter(entry)
      assert fm2["severity"] == "high"
      assert fm2["updated_at"] == "2026-05-24T11:00:00+00:00"
      assert fm2["id"] == "WI-9"
      assert body2.startswith("\n# Body text")


  def test_read_frontmatter_rejects_file_without_fence(tmp_path: Path) -> None:
      entry = tmp_path / "PBI.md"
      entry.write_text("no frontmatter here\n", encoding="utf-8")
      with pytest.raises(QueueWriterError) as exc:
          read_frontmatter(entry)
      assert "frontmatter" in str(exc.value).lower()


  def test_append_history_creates_or_extends_history_md(tmp_path: Path) -> None:
      pbi_dir = tmp_path / "WI-5"
      pbi_dir.mkdir()
      # First call creates the file.
      append_history(
          pbi_dir,
          actor="ralph-promote",
          action="promote",
          detail="severity: normal -> high",
      )
      text1 = (pbi_dir / "HISTORY.md").read_text(encoding="utf-8")
      assert "ralph-promote" in text1
      assert "severity: normal -> high" in text1
      # Second call appends rather than overwriting.
      append_history(
          pbi_dir,
          actor="ralph-triage",
          action="return-to-inbox",
          detail="reset attempts to 0",
      )
      text2 = (pbi_dir / "HISTORY.md").read_text(encoding="utf-8")
      assert "ralph-promote" in text2
      assert "ralph-triage" in text2
      assert text2.count("---") >= 2  # one separator per entry
  ```

- [ ] 3. Run the test file to confirm the expected `ModuleNotFoundError`:
  ```
  uv run pytest tests/test_queue_writer.py -v
  ```
  Expected: collection fails with `ModuleNotFoundError: No module named 'scripts.queue_writer'`. This is the red step.

- [ ] 4. Write `scripts/queue_writer.py` with the exact content below. It owns every git command and the frontmatter read/write logic used by the supervisor skills:
  ```python
  """Shared helpers for skills that mutate the ``ralph-queue`` branch.

  The four supervisor skills (`ralph-add`, `ralph-cancel`, `ralph-promote`,
  `ralph-triage`) all follow the same pattern: validate the target repo,
  switch onto `ralph-queue`, mutate one or more PBI directories, commit,
  optionally push. This module is the single source of truth for that
  pattern so the skills stay consistent and the git-related test surface
  has a single home.
  """
  from __future__ import annotations

  import subprocess
  from collections.abc import Iterable, Mapping, MutableMapping
  from datetime import datetime, timezone
  from pathlib import Path
  from typing import Any

  import yaml


  QUEUE_STATE_FOLDERS: tuple[str, ...] = (
      "current",
      "inbox",
      "pending-pr",
      "blocked",
      "done",
      "archive",
  )

  RALPH_DIR_NAME = ".ralph"


  class QueueWriterError(RuntimeError):
      """Raised when a queue-writer operation can't proceed.

      Skills catch this exception, print the message to stderr, and exit
      with code 2.
      """


  # ----------------------------------------------------------------------
  # Git helpers
  # ----------------------------------------------------------------------

  def _run_git(repo: Path, *args: str) -> str:
      try:
          result = subprocess.run(
              ["git", *args],
              cwd=str(repo),
              check=True,
              capture_output=True,
              text=True,
          )
      except subprocess.CalledProcessError as exc:
          stderr = (exc.stderr or "").strip()
          raise QueueWriterError(
              f"git {' '.join(args)} failed ({exc.returncode}): {stderr}"
          ) from exc
      return result.stdout.strip()


  def ensure_git_repo(repo: Path) -> None:
      """Raise ``QueueWriterError`` if ``repo`` is not a git repository."""
      if not repo.exists():
          raise QueueWriterError(f"{repo} is not a git repository (path missing)")
      if not (repo / ".git").exists():
          raise QueueWriterError(
              f"{repo} is not a git repository (no .git/ directory)"
          )


  def checkout_queue_branch(repo: Path, branch: str) -> None:
      """Switch the working tree onto ``branch``.

      If the branch exists locally, ``git checkout <branch>``. Otherwise, if
      ``origin/<branch>`` exists, create a tracking branch. Otherwise raise
      ``QueueWriterError`` — the caller must run the ``ralph-queue`` setup
      runbook first (Plan 2).
      """
      ensure_git_repo(repo)
      local = _run_git(repo, "branch", "--list", branch)
      if local.strip():
          _run_git(repo, "checkout", branch)
          return
      remote = _run_git(repo, "branch", "-r", "--list", f"origin/{branch}")
      if remote.strip():
          _run_git(repo, "checkout", "-b", branch, f"origin/{branch}")
          return
      raise QueueWriterError(
          f"branch {branch!r} not found locally or as origin/{branch}; "
          "run docs/runbooks/ralph-queue-setup.md first"
      )


  def commit_paths(
      repo: Path,
      paths: Iterable[Path],
      message: str,
  ) -> str:
      """Stage every path in ``paths`` and create a commit with ``message``.

      Returns the new HEAD SHA. Paths are taken relative to ``repo`` for
      ``git add``. Missing paths raise ``QueueWriterError`` before any git
      command runs.
      """
      ensure_git_repo(repo)
      rel_paths: list[str] = []
      for raw in paths:
          path = raw if raw.is_absolute() else (repo / raw)
          try:
              rel = path.resolve().relative_to(repo.resolve())
          except ValueError as exc:
              raise QueueWriterError(
                  f"path {path} is not inside repo {repo}"
              ) from exc
          rel_paths.append(str(rel).replace("\\", "/"))
      if not rel_paths:
          raise QueueWriterError("commit_paths called with no paths to stage")
      _run_git(repo, "add", "--", *rel_paths)
      _run_git(repo, "commit", "-m", message)
      return _run_git(repo, "rev-parse", "HEAD")


  def push(repo: Path, branch: str) -> None:
      """Push ``branch`` to ``origin``."""
      _run_git(repo, "push", "origin", branch)


  # ----------------------------------------------------------------------
  # PBI discovery
  # ----------------------------------------------------------------------

  def find_pbi_directory(repo: Path, pbi_id: str) -> Path | None:
      """Return the absolute path of ``pbi_id`` under ``.ralph/``, or None.

      Search order matches the Ralph lifecycle precedence (an actively-
      worked PBI in ``current/`` should win over a stale duplicate in
      ``inbox/``):

      1. current/
      2. inbox/
      3. pending-pr/
      4. blocked/
      5. done/
      6. archive/
      """
      base = repo / RALPH_DIR_NAME
      for folder in QUEUE_STATE_FOLDERS:
          candidate = base / folder / pbi_id
          if candidate.is_dir():
              return candidate
      return None


  # ----------------------------------------------------------------------
  # Frontmatter helpers
  # ----------------------------------------------------------------------

  def _split_frontmatter(text: str) -> tuple[str, str]:
      lines = text.splitlines(keepends=True)
      if not lines or lines[0].rstrip("\n") != "---":
          raise QueueWriterError(
              "file does not start with a YAML frontmatter fence ('---')"
          )
      for idx in range(1, len(lines)):
          if lines[idx].rstrip("\n") == "---":
              frontmatter = "".join(lines[1:idx])
              body = "".join(lines[idx + 1 :])
              return frontmatter, body
      raise QueueWriterError("frontmatter block is not closed by a second '---'")


  def read_frontmatter(entry_file: Path) -> tuple[dict[str, Any], str]:
      """Read ``entry_file`` and return ``(frontmatter_dict, body_text)``.

      Raises ``QueueWriterError`` if the file lacks a YAML frontmatter
      block or the YAML does not parse as a mapping.
      """
      if not entry_file.is_file():
          raise QueueWriterError(f"entry file does not exist: {entry_file}")
      text = entry_file.read_text(encoding="utf-8")
      frontmatter_text, body = _split_frontmatter(text)
      try:
          parsed: Any = yaml.safe_load(frontmatter_text)
      except yaml.YAMLError as exc:
          raise QueueWriterError(
              f"frontmatter in {entry_file.name} is not valid YAML: {exc}"
          ) from exc
      if not isinstance(parsed, Mapping):
          raise QueueWriterError(
              f"frontmatter in {entry_file.name} must be a YAML mapping, "
              f"got {type(parsed).__name__}"
          )
      return dict(parsed), body


  def write_frontmatter(
      entry_file: Path,
      frontmatter: Mapping[str, Any],
      body: str,
  ) -> None:
      """Write ``frontmatter`` + ``body`` back to ``entry_file``.

      The frontmatter is rendered with ``yaml.safe_dump`` using
      ``sort_keys=False`` so the key order matches the input order as much
      as possible.
      """
      rendered = yaml.safe_dump(
          dict(frontmatter),
          sort_keys=False,
          allow_unicode=True,
          default_flow_style=False,
      )
      # ``yaml.safe_dump`` ends with a newline; rstrip the trailing one so
      # the fence line is exactly '---\n' and matches the original layout.
      rendered = rendered.rstrip("\n")
      output = f"---\n{rendered}\n---\n{body}"
      entry_file.write_text(output, encoding="utf-8")


  # ----------------------------------------------------------------------
  # HISTORY.md
  # ----------------------------------------------------------------------

  def _now_iso() -> str:
      return (
          datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()
      )


  def append_history(
      pbi_dir: Path,
      *,
      actor: str,
      action: str,
      detail: str,
  ) -> None:
      """Append a structured entry to ``<pbi_dir>/HISTORY.md``.

      The entry has the form::

          ---
          - timestamp: <iso8601>
          - actor: <actor>
          - action: <action>
          - detail: <detail>

      Each call adds a leading ``---`` separator so entries are visually
      distinct and machine-parseable.
      """
      history = pbi_dir / "HISTORY.md"
      separator = "---\n" if history.exists() and history.read_text(
          encoding="utf-8"
      ).strip() else ""
      entry = (
          f"{separator}- timestamp: {_now_iso()}\n"
          f"- actor: {actor}\n"
          f"- action: {action}\n"
          f"- detail: {detail}\n"
      )
      with history.open("a", encoding="utf-8") as fh:
          fh.write(entry)


  # ----------------------------------------------------------------------
  # Frontmatter merge convenience
  # ----------------------------------------------------------------------

  def update_frontmatter_fields(
      entry_file: Path,
      updates: MutableMapping[str, Any],
  ) -> dict[str, Any]:
      """Read ``entry_file``, apply ``updates`` to its frontmatter, write back.

      Returns the merged frontmatter dict after the write. ``updated_at`` is
      stamped automatically if ``updates`` does not already set it.
      """
      frontmatter, body = read_frontmatter(entry_file)
      frontmatter.update(updates)
      frontmatter.setdefault("updated_at", _now_iso())
      frontmatter["updated_at"] = updates.get("updated_at", _now_iso())
      write_frontmatter(entry_file, frontmatter, body)
      return frontmatter
  ```

- [ ] 5. Run the test file again — every test must pass:
  ```
  uv run pytest tests/test_queue_writer.py -v
  ```
  Expected: thirteen tests pass. Exit code 0.

- [ ] 6. Run ruff and mypy:
  ```
  uv run ruff check scripts/queue_writer.py tests/test_queue_writer.py
  uv run mypy scripts/queue_writer.py tests/test_queue_writer.py
  ```
  Expected: both report success.

- [ ] 7. Stage and commit:
  ```
  git add scripts/queue_writer.py tests/test_queue_writer.py
  git commit -m "feat(queue-writer): add shared git + frontmatter helpers for supervisor skills"
  ```
  Expected: commit succeeds.

---

## Task 2 — Write the failing test for `ralph-cancel`

**Files**
- Create: `tests/skills/test_ralph_cancel.py`

**Steps**

- [ ] 1. Write `tests/skills/test_ralph_cancel.py` with the exact content below. The test imports the entry script via `importlib` (same pattern as Plan 3) and fails because the script does not yet exist:
  ```python
  """Tests for the ``ralph-cancel`` skill's entry script.

  ``ralph-cancel`` is the only mutation we allow on ``.ralph/current/``:
  it drops an empty ``CANCEL`` sentinel file inside the directory of the
  PBI Ralph is actively working on, then commits + pushes to
  ``ralph-queue``. Ralph notices the sentinel on its next iteration and
  abandons the PBI.

  The script lives at ``skills/ralph-cancel/scripts/cancel.py``. Tests
  build a fake ``ralph-queue`` clone in ``tmp_path`` and verify each
  behaviour without any network or real ADO.
  """
  from __future__ import annotations

  import importlib.util
  import json
  import subprocess
  from collections.abc import Iterator
  from pathlib import Path
  from types import ModuleType

  import pytest


  REPO_ROOT = Path(__file__).resolve().parents[2]
  SCRIPT_PATH = REPO_ROOT / "skills" / "ralph-cancel" / "scripts" / "cancel.py"


  @pytest.fixture(scope="module")
  def cancel_module() -> ModuleType:
      assert SCRIPT_PATH.is_file(), f"missing entry script at {SCRIPT_PATH}"
      spec = importlib.util.spec_from_file_location(
          "ralph_cancel_script", SCRIPT_PATH
      )
      assert spec is not None and spec.loader is not None
      module = importlib.util.module_from_spec(spec)
      spec.loader.exec_module(module)
      return module


  def _git(cwd: Path, *args: str) -> str:
      result = subprocess.run(
          ["git", *args],
          cwd=str(cwd),
          check=True,
          capture_output=True,
          text=True,
      )
      return result.stdout


  @pytest.fixture
  def git_repo(tmp_path: Path) -> Iterator[Path]:
      bare = tmp_path / "remote.git"
      work = tmp_path / "work"
      subprocess.run(["git", "init", "--bare", str(bare)], check=True)
      subprocess.run(["git", "init", str(work)], check=True)
      _git(work, "config", "user.email", "test@example.com")
      _git(work, "config", "user.name", "Test User")
      _git(work, "commit", "--allow-empty", "-m", "chore: initial main commit")
      _git(work, "branch", "-M", "main")
      _git(work, "remote", "add", "origin", str(bare))
      _git(work, "push", "-u", "origin", "main")
      _git(work, "checkout", "-b", "ralph-queue")
      _git(work, "commit", "--allow-empty", "-m", "chore(queue): bootstrap")
      _git(work, "push", "-u", "origin", "ralph-queue")
      _git(work, "checkout", "main")
      yield work


  def _seed_pbi(
      repo: Path,
      state_folder: str,
      pbi_id: str,
      entry_file: str = "PBI.md",
  ) -> Path:
      """Create a PBI directory on the ralph-queue branch and commit it."""
      _git(repo, "checkout", "ralph-queue")
      pbi_dir = repo / ".ralph" / state_folder / pbi_id
      pbi_dir.mkdir(parents=True)
      (pbi_dir / entry_file).write_text(
          "---\n"
          f"id: {pbi_id}\n"
          "type: feature\n"
          f"status: {state_folder.replace('-pr', '-pr') if state_folder == 'pending-pr' else state_folder}\n"
          "severity: normal\n"
          "attempts: 0\n"
          "created_at: 2026-05-24T10:00:00+00:00\n"
          "updated_at: 2026-05-24T10:00:00+00:00\n"
          "---\n"
          "\n"
          "# body\n",
          encoding="utf-8",
      )
      (pbi_dir / "HISTORY.md").write_text("", encoding="utf-8")
      _git(repo, "add", f".ralph/{state_folder}/{pbi_id}")
      _git(repo, "commit", "-m", f"chore(test): seed {pbi_id} in {state_folder}")
      _git(repo, "push", "origin", "ralph-queue")
      _git(repo, "checkout", "main")
      return pbi_dir


  # ----------------------------------------------------------------------
  # Tests
  # ----------------------------------------------------------------------

  def test_cancel_drops_sentinel_in_current(
      git_repo: Path,
      cancel_module: ModuleType,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      _seed_pbi(git_repo, "current", "WI-1234")

      exit_code = cancel_module.main(
          [
              "--pbi-id",
              "WI-1234",
              "--repo",
              str(git_repo),
          ]
      )
      assert exit_code == 0
      payload = json.loads(capsys.readouterr().out)
      assert payload["pbi_id"] == "WI-1234"
      assert payload["sentinel_path"].endswith("WI-1234/CANCEL")
      assert payload["pushed"] is True
      assert payload["commit_sha"]

      _git(git_repo, "checkout", "ralph-queue")
      cancel_file = git_repo / ".ralph" / "current" / "WI-1234" / "CANCEL"
      assert cancel_file.is_file()
      assert cancel_file.read_bytes() == b""

      latest = _git(git_repo, "log", "-1", "--pretty=%s").strip()
      assert latest == "chore(queue): cancel WI-1234"


  def test_cancel_refuses_pbi_outside_current(
      git_repo: Path,
      cancel_module: ModuleType,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      _seed_pbi(git_repo, "inbox", "WI-2000")

      exit_code = cancel_module.main(
          [
              "--pbi-id",
              "WI-2000",
              "--repo",
              str(git_repo),
          ]
      )
      assert exit_code == 2
      stderr = capsys.readouterr().err.lower()
      assert "current" in stderr  # explains the constraint


  def test_cancel_errors_on_missing_pbi(
      git_repo: Path,
      cancel_module: ModuleType,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      exit_code = cancel_module.main(
          [
              "--pbi-id",
              "WI-DOES-NOT-EXIST",
              "--repo",
              str(git_repo),
          ]
      )
      assert exit_code == 2
      stderr = capsys.readouterr().err.lower()
      assert "not found" in stderr or "no such" in stderr or "missing" in stderr


  def test_cancel_no_push_keeps_remote_at_previous_sha(
      git_repo: Path,
      cancel_module: ModuleType,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      _seed_pbi(git_repo, "current", "WI-3000")
      before = _git(git_repo, "ls-remote", "origin", "ralph-queue").strip()

      exit_code = cancel_module.main(
          [
              "--pbi-id",
              "WI-3000",
              "--repo",
              str(git_repo),
              "--no-push",
          ]
      )
      assert exit_code == 0
      payload = json.loads(capsys.readouterr().out)
      assert payload["pushed"] is False
      assert payload["commit_sha"]  # commit still happened locally
      after = _git(git_repo, "ls-remote", "origin", "ralph-queue").strip()
      assert before == after


  def test_cancel_dry_run_writes_nothing(
      git_repo: Path,
      cancel_module: ModuleType,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      _seed_pbi(git_repo, "current", "WI-4000")
      before = _git(git_repo, "ls-remote", "origin", "ralph-queue").strip()

      exit_code = cancel_module.main(
          [
              "--pbi-id",
              "WI-4000",
              "--repo",
              str(git_repo),
              "--dry-run",
          ]
      )
      assert exit_code == 0
      payload = json.loads(capsys.readouterr().out)
      assert payload["dry_run"] is True
      assert payload["pushed"] is False
      assert payload["commit_sha"] == ""

      _git(git_repo, "checkout", "ralph-queue")
      cancel_file = git_repo / ".ralph" / "current" / "WI-4000" / "CANCEL"
      assert not cancel_file.exists()
      after = _git(git_repo, "ls-remote", "origin", "ralph-queue").strip()
      assert before == after


  def test_cancel_idempotent_when_sentinel_already_present(
      git_repo: Path,
      cancel_module: ModuleType,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      pbi_dir = _seed_pbi(git_repo, "current", "WI-5000")
      # Pre-seed the CANCEL file on the queue branch.
      _git(git_repo, "checkout", "ralph-queue")
      (pbi_dir / "CANCEL").write_text("", encoding="utf-8")
      _git(git_repo, "add", ".ralph/current/WI-5000/CANCEL")
      _git(git_repo, "commit", "-m", "chore(test): pre-existing cancel")
      _git(git_repo, "push", "origin", "ralph-queue")
      _git(git_repo, "checkout", "main")

      exit_code = cancel_module.main(
          [
              "--pbi-id",
              "WI-5000",
              "--repo",
              str(git_repo),
          ]
      )
      # Idempotent: nothing changes, exit code 0, JSON shows
      # already_cancelled=True.
      assert exit_code == 0
      payload = json.loads(capsys.readouterr().out)
      assert payload["already_cancelled"] is True
      assert payload["commit_sha"] == ""
      assert payload["pushed"] is False


  def test_cancel_repo_not_a_repo_errors(
      tmp_path: Path,
      cancel_module: ModuleType,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      not_a_repo = tmp_path / "not_a_repo"
      not_a_repo.mkdir()
      exit_code = cancel_module.main(
          [
              "--pbi-id",
              "WI-1",
              "--repo",
              str(not_a_repo),
          ]
      )
      assert exit_code == 2
      assert "not a git repository" in capsys.readouterr().err.lower()
  ```

- [ ] 2. Run the test file; it must fail because the script does not yet exist:
  ```
  uv run pytest tests/skills/test_ralph_cancel.py -v
  ```
  Expected: the fixture loader raises `AssertionError: missing entry script at <SCRIPT_PATH>`. This is the red step.

- [ ] 3. Do NOT commit yet — the failing tests are consumed in Task 3.

---

## Task 3 — Implement `ralph-cancel`

**Files**
- Create: `skills/ralph-cancel/SKILL.md`
- Create: `skills/ralph-cancel/scripts/__init__.py`
- Create: `skills/ralph-cancel/scripts/cancel.py`

**Steps**

- [ ] 1. Create the empty package marker. Write `skills/ralph-cancel/scripts/__init__.py` containing exactly:
  ```python
  """Empty package marker."""
  ```

- [ ] 2. Write `skills/ralph-cancel/SKILL.md` with the exact content below:
  ```markdown
  ---
  name: ralph-cancel
  description: Cancel a PBI Ralph is actively working on. Drops an empty CANCEL sentinel file into the PBI directory under .ralph/current/, commits it to the ralph-queue branch, and pushes. Ralph notices the sentinel on its next iteration and abandons the PBI. The sentinel is the only allowed mutation on .ralph/current/; PBIs in other state folders (inbox, blocked, pending-pr, done, archive) cannot be cancelled this way.
  ---

  # ralph-cancel

  ## What this skill does

  `ralph-cancel` is how a human aborts a PBI Ralph is in the middle of
  working on. It writes an empty file named `CANCEL` inside the PBI's
  directory under `.ralph/current/`, commits to `ralph-queue`, and pushes.
  Ralph's loop reads `CANCEL` at the top of every iteration and, if it
  finds one, stops work on the PBI and moves it to `.ralph/blocked/` with
  a cancellation note in `HISTORY.md`.

  ## When to use it

  - The PBI's scope changed and Ralph should stop immediately.
  - Ralph is thrashing on the PBI and a human wants to pull it back into
    the human triage loop.
  - The PBI was submitted by mistake and the BA wants to clear the slot.

  Do NOT use `ralph-cancel` for PBIs that aren't in `current/`. Use
  `ralph-triage` (for blocked PBIs) or simply delete the inbox entry via
  the queue branch directly (for inbox PBIs that never started).

  ## Inputs

  | Flag | Required | Description |
  |---|---|---|
  | `--pbi-id <id>` | yes | The PBI identifier (e.g. `WI-1234` or `BUG-deploy-rosa-irsa-2026-05-23`). Matches the directory name under `.ralph/current/`. |
  | `--repo <path>` | yes | Absolute path to an existing checkout of the target service repo. The skill switches that checkout onto `ralph-queue`, writes the sentinel, commits, and pushes. |
  | `--branch <name>` | no | Queue branch name. Default: `ralph-queue` (overridable via `RALPH_QUEUE_BRANCH`). |
  | `--no-push` | no | Commit the sentinel locally but do not push. Useful for inspecting the commit before it lands. |
  | `--dry-run` | no | Compute and log without writing the sentinel, committing, or pushing. Prints the JSON summary describing what would have happened. |

  ## Environment variables

  | Variable | Purpose |
  |---|---|
  | `RALPH_QUEUE_BRANCH` | Default queue branch name; overridden by `--branch`. Optional; defaults to `ralph-queue`. |

  No ADO credentials are required — this skill is purely a git mutation.

  ## Output

  Prints a JSON summary to stdout on success. Example:

  ```json
  {
    "pbi_id": "WI-1234",
    "sentinel_path": ".ralph/current/WI-1234/CANCEL",
    "repo_path": "/home/dev/service-auth",
    "branch": "ralph-queue",
    "commit_sha": "abcdef0123456789",
    "pushed": true,
    "dry_run": false,
    "already_cancelled": false
  }
  ```

  Progress messages go to stderr.

  ## How it is invoked

  ```bash
  uv run python skills/ralph-cancel/scripts/cancel.py \
      --pbi-id WI-1234 \
      --repo /path/to/service-auth
  ```

  ## What this skill does NOT do

  - It does not move the PBI directory out of `.ralph/current/` — that
    is the executor's job. The sentinel is a signal; the move is the
    response.
  - It does not delete any PBI files. The cancellation is recorded via
    the empty `CANCEL` file plus a commit on `ralph-queue`.
  - It does not affect PBIs in `.ralph/inbox/`, `.ralph/blocked/`,
    `.ralph/pending-pr/`, `.ralph/done/`, or `.ralph/archive/`. Cancellation
    is current-state-only.
  ```

- [ ] 3. Write `skills/ralph-cancel/scripts/cancel.py` with the exact content below:
  ```python
  """``ralph-cancel`` skill entry point.

  Drops an empty ``CANCEL`` sentinel file into the PBI directory under
  ``.ralph/current/`` and pushes the result to ``ralph-queue``. Ralph
  reads the sentinel on its next iteration and abandons the PBI.
  """
  from __future__ import annotations

  import argparse
  import json
  import os
  import sys
  from dataclasses import asdict, dataclass
  from pathlib import Path

  # Make ``scripts.queue_writer`` importable when this script is invoked
  # directly with ``uv run python skills/ralph-cancel/scripts/cancel.py``.
  _REPO_ROOT = Path(__file__).resolve().parents[3]
  if str(_REPO_ROOT) not in sys.path:
      sys.path.insert(0, str(_REPO_ROOT))

  from scripts.queue_writer import (  # noqa: E402
      QueueWriterError,
      checkout_queue_branch,
      commit_paths,
      ensure_git_repo,
      push,
  )

  DEFAULT_QUEUE_BRANCH = "ralph-queue"
  CURRENT_FOLDER = "current"
  CANCEL_FILE_NAME = "CANCEL"


  @dataclass
  class CancelResult:
      pbi_id: str
      sentinel_path: str
      repo_path: str
      branch: str
      commit_sha: str
      pushed: bool
      dry_run: bool
      already_cancelled: bool


  def _parse_args(argv: list[str]) -> argparse.Namespace:
      parser = argparse.ArgumentParser(
          prog="ralph-cancel",
          description=(
              "Cancel a PBI that Ralph is actively working on by dropping "
              "an empty CANCEL sentinel into .ralph/current/<pbi-id>/."
          ),
      )
      parser.add_argument(
          "--pbi-id",
          required=True,
          help="PBI identifier matching the directory name under .ralph/current/.",
      )
      parser.add_argument(
          "--repo",
          required=True,
          help="Absolute path to the target service repo checkout.",
      )
      parser.add_argument(
          "--branch",
          default=os.environ.get("RALPH_QUEUE_BRANCH", DEFAULT_QUEUE_BRANCH),
          help=f"Queue branch name (default: {DEFAULT_QUEUE_BRANCH}).",
      )
      parser.add_argument(
          "--no-push",
          action="store_true",
          help="Commit the sentinel locally but do not push.",
      )
      parser.add_argument(
          "--dry-run",
          action="store_true",
          help="Compute and log without writing, committing, or pushing.",
      )
      return parser.parse_args(argv)


  def _resolve_current_pbi(repo: Path, pbi_id: str) -> Path:
      current_dir = repo / ".ralph" / CURRENT_FOLDER / pbi_id
      if not current_dir.is_dir():
          # Distinguish "in another state folder" from "doesn't exist".
          base = repo / ".ralph"
          for folder in ("inbox", "pending-pr", "blocked", "done", "archive"):
              if (base / folder / pbi_id).is_dir():
                  raise QueueWriterError(
                      f"PBI {pbi_id!r} is in .ralph/{folder}/, not in "
                      f".ralph/{CURRENT_FOLDER}/. ralph-cancel only operates "
                      f"on the active PBI."
                  )
          raise QueueWriterError(
              f"PBI {pbi_id!r} not found under any .ralph/ state folder"
          )
      return current_dir


  def main(argv: list[str] | None = None) -> int:
      args = _parse_args(argv if argv is not None else sys.argv[1:])

      try:
          repo = Path(args.repo).resolve()
          ensure_git_repo(repo)

          if not args.dry_run:
              print(f"switching to {args.branch}...", file=sys.stderr)
              checkout_queue_branch(repo, args.branch)

          pbi_dir = _resolve_current_pbi(repo, args.pbi_id)
          sentinel = pbi_dir / CANCEL_FILE_NAME
          rel_sentinel = (
              sentinel.relative_to(repo).as_posix()
              if sentinel.is_absolute()
              else str(sentinel)
          )

          if sentinel.exists():
              print(
                  f"CANCEL sentinel already present at {rel_sentinel}; "
                  "nothing to do.",
                  file=sys.stderr,
              )
              result = CancelResult(
                  pbi_id=args.pbi_id,
                  sentinel_path=rel_sentinel,
                  repo_path=str(repo),
                  branch=args.branch,
                  commit_sha="",
                  pushed=False,
                  dry_run=args.dry_run,
                  already_cancelled=True,
              )
              print(json.dumps(asdict(result), indent=2, sort_keys=True))
              return 0

          if args.dry_run:
              print(
                  f"dry-run: would write {rel_sentinel} and commit "
                  f"'chore(queue): cancel {args.pbi_id}'.",
                  file=sys.stderr,
              )
              result = CancelResult(
                  pbi_id=args.pbi_id,
                  sentinel_path=rel_sentinel,
                  repo_path=str(repo),
                  branch=args.branch,
                  commit_sha="",
                  pushed=False,
                  dry_run=True,
                  already_cancelled=False,
              )
              print(json.dumps(asdict(result), indent=2, sort_keys=True))
              return 0

          print(f"writing sentinel at {rel_sentinel}...", file=sys.stderr)
          sentinel.write_bytes(b"")

          commit_sha = commit_paths(
              repo,
              [sentinel],
              f"chore(queue): cancel {args.pbi_id}",
          )

          pushed = False
          if not args.no_push:
              print(f"pushing {args.branch}...", file=sys.stderr)
              push(repo, args.branch)
              pushed = True

          result = CancelResult(
              pbi_id=args.pbi_id,
              sentinel_path=rel_sentinel,
              repo_path=str(repo),
              branch=args.branch,
              commit_sha=commit_sha,
              pushed=pushed,
              dry_run=False,
              already_cancelled=False,
          )
          print(json.dumps(asdict(result), indent=2, sort_keys=True))
          return 0

      except QueueWriterError as exc:
          print(f"error: {exc}", file=sys.stderr)
          return 2


  if __name__ == "__main__":
      raise SystemExit(main())
  ```

- [ ] 4. Run the test file; every test must now pass:
  ```
  uv run pytest tests/skills/test_ralph_cancel.py -v
  ```
  Expected: seven tests pass. Exit code 0.

- [ ] 5. Run ruff and mypy:
  ```
  uv run ruff check skills/ralph-cancel/ tests/skills/test_ralph_cancel.py
  uv run mypy skills/ralph-cancel/scripts/cancel.py tests/skills/test_ralph_cancel.py
  ```
  Expected: both report success.

- [ ] 6. Stage and commit:
  ```
  git add skills/ralph-cancel/ tests/skills/test_ralph_cancel.py
  git commit -m "feat(ralph-cancel): drop CANCEL sentinel into current/ and push to ralph-queue"
  ```
  Expected: commit succeeds.

---

## Task 4 — Write the failing test for `ralph-promote`

**Files**
- Create: `tests/skills/test_ralph_promote.py`

**Steps**

- [ ] 1. Write `tests/skills/test_ralph_promote.py` with the exact content below:
  ```python
  """Tests for the ``ralph-promote`` skill's entry script.

  ``ralph-promote`` bumps a PBI's ``severity`` frontmatter field. The PBI
  may live in any state folder under ``.ralph/`` — promote is purely a
  metadata update, not a state change. The skill commits and pushes to
  ``ralph-queue``.

  Tests use a local bare repo + working clone in ``tmp_path`` and never
  touch the network.
  """
  from __future__ import annotations

  import importlib.util
  import json
  import subprocess
  from collections.abc import Iterator
  from pathlib import Path
  from types import ModuleType

  import pytest
  import yaml


  REPO_ROOT = Path(__file__).resolve().parents[2]
  SCRIPT_PATH = REPO_ROOT / "skills" / "ralph-promote" / "scripts" / "promote.py"


  @pytest.fixture(scope="module")
  def promote_module() -> ModuleType:
      assert SCRIPT_PATH.is_file(), f"missing entry script at {SCRIPT_PATH}"
      spec = importlib.util.spec_from_file_location(
          "ralph_promote_script", SCRIPT_PATH
      )
      assert spec is not None and spec.loader is not None
      module = importlib.util.module_from_spec(spec)
      spec.loader.exec_module(module)
      return module


  def _git(cwd: Path, *args: str) -> str:
      result = subprocess.run(
          ["git", *args],
          cwd=str(cwd),
          check=True,
          capture_output=True,
          text=True,
      )
      return result.stdout


  @pytest.fixture
  def git_repo(tmp_path: Path) -> Iterator[Path]:
      bare = tmp_path / "remote.git"
      work = tmp_path / "work"
      subprocess.run(["git", "init", "--bare", str(bare)], check=True)
      subprocess.run(["git", "init", str(work)], check=True)
      _git(work, "config", "user.email", "test@example.com")
      _git(work, "config", "user.name", "Test User")
      _git(work, "commit", "--allow-empty", "-m", "chore: initial main commit")
      _git(work, "branch", "-M", "main")
      _git(work, "remote", "add", "origin", str(bare))
      _git(work, "push", "-u", "origin", "main")
      _git(work, "checkout", "-b", "ralph-queue")
      _git(work, "commit", "--allow-empty", "-m", "chore(queue): bootstrap")
      _git(work, "push", "-u", "origin", "ralph-queue")
      _git(work, "checkout", "main")
      yield work


  def _seed_pbi(
      repo: Path,
      state_folder: str,
      pbi_id: str,
      pbi_type: str = "feature",
      severity: str = "normal",
  ) -> Path:
      _git(repo, "checkout", "ralph-queue")
      pbi_dir = repo / ".ralph" / state_folder / pbi_id
      pbi_dir.mkdir(parents=True)
      entry_name = {
          "feature": "PBI.md",
          "bug": "BUG.md",
          "pr-feedback": "FEEDBACK.md",
      }[pbi_type]
      (pbi_dir / entry_name).write_text(
          "---\n"
          f"id: {pbi_id}\n"
          f"type: {pbi_type}\n"
          f"status: {state_folder.replace('-pr', '-pr')}\n"
          f"severity: {severity}\n"
          "attempts: 2\n"
          "created_at: 2026-05-20T08:00:00+00:00\n"
          "updated_at: 2026-05-22T09:30:00+00:00\n"
          "---\n"
          "\n"
          "# body text\n"
          "\n"
          "paragraph two\n",
          encoding="utf-8",
      )
      (pbi_dir / "HISTORY.md").write_text("", encoding="utf-8")
      _git(repo, "add", f".ralph/{state_folder}/{pbi_id}")
      _git(repo, "commit", "-m", f"chore(test): seed {pbi_id}")
      _git(repo, "push", "origin", "ralph-queue")
      _git(repo, "checkout", "main")
      return pbi_dir


  def _read_frontmatter(entry_file: Path) -> dict[str, object]:
      text = entry_file.read_text(encoding="utf-8")
      lines = text.splitlines()
      assert lines[0] == "---"
      end = next(i for i, line in enumerate(lines[1:], start=1) if line == "---")
      result = yaml.safe_load("\n".join(lines[1:end]))
      assert isinstance(result, dict)
      return result


  # ----------------------------------------------------------------------
  # Tests
  # ----------------------------------------------------------------------

  def test_promote_feature_pbi_in_inbox(
      git_repo: Path,
      promote_module: ModuleType,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      _seed_pbi(git_repo, "inbox", "WI-100", pbi_type="feature", severity="normal")

      exit_code = promote_module.main(
          [
              "--pbi-id",
              "WI-100",
              "--severity",
              "high",
              "--repo",
              str(git_repo),
          ]
      )
      assert exit_code == 0
      payload = json.loads(capsys.readouterr().out)
      assert payload["pbi_id"] == "WI-100"
      assert payload["previous_severity"] == "normal"
      assert payload["new_severity"] == "high"
      assert payload["state_folder"] == "inbox"
      assert payload["pushed"] is True
      assert payload["commit_sha"]

      _git(git_repo, "checkout", "ralph-queue")
      entry = git_repo / ".ralph" / "inbox" / "WI-100" / "PBI.md"
      fm = _read_frontmatter(entry)
      assert fm["severity"] == "high"
      assert fm["id"] == "WI-100"
      assert fm["type"] == "feature"
      assert fm["attempts"] == 2  # unchanged
      assert fm["created_at"] == "2026-05-20T08:00:00+00:00"  # unchanged
      assert fm["updated_at"] != "2026-05-22T09:30:00+00:00"  # refreshed

      latest = _git(git_repo, "log", "-1", "--pretty=%s").strip()
      assert latest == "chore(queue): promote WI-100 (normal -> high)"


  def test_promote_bug_pbi_uses_bug_md(
      git_repo: Path,
      promote_module: ModuleType,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      _seed_pbi(git_repo, "blocked", "BUG-X", pbi_type="bug", severity="high")

      exit_code = promote_module.main(
          [
              "--pbi-id",
              "BUG-X",
              "--severity",
              "critical",
              "--repo",
              str(git_repo),
          ]
      )
      assert exit_code == 0

      _git(git_repo, "checkout", "ralph-queue")
      entry = git_repo / ".ralph" / "blocked" / "BUG-X" / "BUG.md"
      fm = _read_frontmatter(entry)
      assert fm["severity"] == "critical"
      assert fm["type"] == "bug"


  def test_promote_rejects_unknown_severity(
      git_repo: Path,
      promote_module: ModuleType,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      _seed_pbi(git_repo, "inbox", "WI-200", severity="normal")

      with pytest.raises(SystemExit) as exc:
          promote_module.main(
              [
                  "--pbi-id",
                  "WI-200",
                  "--severity",
                  "urgent",
                  "--repo",
                  str(git_repo),
              ]
          )
      # argparse raises SystemExit on choice violations.
      assert exc.value.code == 2


  def test_promote_errors_on_missing_pbi(
      git_repo: Path,
      promote_module: ModuleType,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      exit_code = promote_module.main(
          [
              "--pbi-id",
              "WI-NOPE",
              "--severity",
              "high",
              "--repo",
              str(git_repo),
          ]
      )
      assert exit_code == 2
      stderr = capsys.readouterr().err.lower()
      assert "not found" in stderr or "no such" in stderr or "missing" in stderr


  def test_promote_preserves_body_content(
      git_repo: Path,
      promote_module: ModuleType,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      _seed_pbi(git_repo, "inbox", "WI-300", severity="low")

      exit_code = promote_module.main(
          [
              "--pbi-id",
              "WI-300",
              "--severity",
              "normal",
              "--repo",
              str(git_repo),
          ]
      )
      assert exit_code == 0

      _git(git_repo, "checkout", "ralph-queue")
      text = (git_repo / ".ralph" / "inbox" / "WI-300" / "PBI.md").read_text(
          encoding="utf-8"
      )
      assert "# body text" in text
      assert "paragraph two" in text
      # Body must follow the frontmatter fence exactly once.
      assert text.count("---") == 2


  def test_promote_no_push_leaves_remote_unchanged(
      git_repo: Path,
      promote_module: ModuleType,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      _seed_pbi(git_repo, "inbox", "WI-400", severity="normal")
      before = _git(git_repo, "ls-remote", "origin", "ralph-queue").strip()

      exit_code = promote_module.main(
          [
              "--pbi-id",
              "WI-400",
              "--severity",
              "high",
              "--repo",
              str(git_repo),
              "--no-push",
          ]
      )
      assert exit_code == 0
      payload = json.loads(capsys.readouterr().out)
      assert payload["pushed"] is False
      assert payload["commit_sha"]
      after = _git(git_repo, "ls-remote", "origin", "ralph-queue").strip()
      assert before == after


  def test_promote_dry_run_writes_nothing(
      git_repo: Path,
      promote_module: ModuleType,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      _seed_pbi(git_repo, "inbox", "WI-500", severity="normal")
      before = _git(git_repo, "ls-remote", "origin", "ralph-queue").strip()

      exit_code = promote_module.main(
          [
              "--pbi-id",
              "WI-500",
              "--severity",
              "critical",
              "--repo",
              str(git_repo),
              "--dry-run",
          ]
      )
      assert exit_code == 0
      payload = json.loads(capsys.readouterr().out)
      assert payload["dry_run"] is True
      assert payload["pushed"] is False
      assert payload["commit_sha"] == ""

      _git(git_repo, "checkout", "ralph-queue")
      fm = _read_frontmatter(
          git_repo / ".ralph" / "inbox" / "WI-500" / "PBI.md"
      )
      assert fm["severity"] == "normal"  # untouched
      after = _git(git_repo, "ls-remote", "origin", "ralph-queue").strip()
      assert before == after


  def test_promote_records_history_entry(
      git_repo: Path,
      promote_module: ModuleType,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      _seed_pbi(git_repo, "inbox", "WI-600", severity="normal")

      exit_code = promote_module.main(
          [
              "--pbi-id",
              "WI-600",
              "--severity",
              "critical",
              "--repo",
              str(git_repo),
          ]
      )
      assert exit_code == 0

      _git(git_repo, "checkout", "ralph-queue")
      history = (git_repo / ".ralph" / "inbox" / "WI-600" / "HISTORY.md").read_text(
          encoding="utf-8"
      )
      assert "ralph-promote" in history
      assert "normal -> critical" in history
  ```

- [ ] 2. Run the new test file; it must fail because the script does not yet exist:
  ```
  uv run pytest tests/skills/test_ralph_promote.py -v
  ```
  Expected: the fixture loader raises `AssertionError: missing entry script at <SCRIPT_PATH>`. Red.

- [ ] 3. Do NOT commit yet — the failing tests are consumed in Task 5.

---

## Task 5 — Implement `ralph-promote`

**Files**
- Create: `skills/ralph-promote/SKILL.md`
- Create: `skills/ralph-promote/scripts/__init__.py`
- Create: `skills/ralph-promote/scripts/promote.py`

**Steps**

- [ ] 1. Write `skills/ralph-promote/scripts/__init__.py` containing exactly:
  ```python
  """Empty package marker."""
  ```

- [ ] 2. Write `skills/ralph-promote/SKILL.md` with the exact content below:
  ```markdown
  ---
  name: ralph-promote
  description: Bump a PBI's severity. Locates the PBI in any state folder under .ralph/ (inbox, current, pending-pr, blocked, done, archive), updates the severity field in its frontmatter (and refreshes updated_at), commits to ralph-queue, and pushes. Use when "normal" became "urgent" without changing the work item itself. Severity must be one of critical, high, normal, low.
  ---

  # ralph-promote

  ## What this skill does

  `ralph-promote` is the human knob for severity. It does not change the
  PBI's body, type, or state folder — it only edits the `severity` field
  on the PBI's entry file frontmatter (`PBI.md` for features and PR
  feedback, `BUG.md` for bugs) and refreshes `updated_at`. It also
  appends a single line to `HISTORY.md` so the change is auditable. The
  result is committed and pushed to `ralph-queue`.

  ## When to use it

  - A normal-priority bug has been open long enough to need escalation.
  - A feature originally tagged `low` has become a blocker for another
    team's work.
  - A critical bug was mis-classified as high during submission.

  ## Inputs

  | Flag | Required | Description |
  |---|---|---|
  | `--pbi-id <id>` | yes | PBI identifier matching the directory name under any `.ralph/<state>/` folder. |
  | `--severity <level>` | yes | New severity. One of `critical`, `high`, `normal`, `low`. |
  | `--repo <path>` | yes | Absolute path to an existing checkout of the target service repo. |
  | `--branch <name>` | no | Queue branch name. Default: `ralph-queue` (overridable via `RALPH_QUEUE_BRANCH`). |
  | `--no-push` | no | Commit locally but do not push. |
  | `--dry-run` | no | Compute and log without writing, committing, or pushing. |

  ## Environment variables

  | Variable | Purpose |
  |---|---|
  | `RALPH_QUEUE_BRANCH` | Default queue branch name; overridden by `--branch`. Optional; defaults to `ralph-queue`. |

  ## Output

  Prints a JSON summary to stdout on success. Example:

  ```json
  {
    "pbi_id": "WI-1234",
    "previous_severity": "normal",
    "new_severity": "high",
    "state_folder": "inbox",
    "entry_file": ".ralph/inbox/WI-1234/PBI.md",
    "repo_path": "/home/dev/service-auth",
    "branch": "ralph-queue",
    "commit_sha": "abcdef0123456789",
    "pushed": true,
    "dry_run": false
  }
  ```

  ## How it is invoked

  ```bash
  uv run python skills/ralph-promote/scripts/promote.py \
      --pbi-id WI-1234 \
      --severity high \
      --repo /path/to/service-auth
  ```

  ## What this skill does NOT do

  - It does not move the PBI between state folders. A PBI stays where it
    was; only the severity changes.
  - It does not change other frontmatter fields (`status`, `attempts`,
    `type`, `id`, `created_at`). Those are owned by the executor or are
    set once at submission.
  - It does not require ADO credentials — this is purely a git mutation.
  ```

- [ ] 3. Write `skills/ralph-promote/scripts/promote.py` with the exact content below:
  ```python
  """``ralph-promote`` skill entry point.

  Bumps the ``severity`` frontmatter field of an existing PBI and pushes
  the result to ``ralph-queue``. The PBI may live in any ``.ralph/``
  state folder; this skill is purely a metadata update.
  """
  from __future__ import annotations

  import argparse
  import json
  import os
  import sys
  from dataclasses import asdict, dataclass
  from datetime import datetime, timezone
  from pathlib import Path

  _REPO_ROOT = Path(__file__).resolve().parents[3]
  if str(_REPO_ROOT) not in sys.path:
      sys.path.insert(0, str(_REPO_ROOT))

  from scripts.queue_writer import (  # noqa: E402
      QueueWriterError,
      append_history,
      checkout_queue_branch,
      commit_paths,
      ensure_git_repo,
      find_pbi_directory,
      push,
      read_frontmatter,
      write_frontmatter,
  )


  DEFAULT_QUEUE_BRANCH = "ralph-queue"
  ALLOWED_SEVERITIES = ("critical", "high", "normal", "low")
  ENTRY_FILE_BY_TYPE = {
      "feature": "PBI.md",
      "bug": "BUG.md",
      "pr-feedback": "FEEDBACK.md",
  }


  @dataclass
  class PromoteResult:
      pbi_id: str
      previous_severity: str
      new_severity: str
      state_folder: str
      entry_file: str
      repo_path: str
      branch: str
      commit_sha: str
      pushed: bool
      dry_run: bool


  def _parse_args(argv: list[str]) -> argparse.Namespace:
      parser = argparse.ArgumentParser(
          prog="ralph-promote",
          description=(
              "Bump a PBI's severity. Locates the PBI under any .ralph/ "
              "state folder, updates the severity frontmatter field, and "
              "pushes the change to ralph-queue."
          ),
      )
      parser.add_argument(
          "--pbi-id",
          required=True,
          help="PBI identifier matching the directory name under .ralph/.",
      )
      parser.add_argument(
          "--severity",
          required=True,
          choices=ALLOWED_SEVERITIES,
          help="New severity. Must be one of critical, high, normal, low.",
      )
      parser.add_argument(
          "--repo",
          required=True,
          help="Absolute path to the target service repo checkout.",
      )
      parser.add_argument(
          "--branch",
          default=os.environ.get("RALPH_QUEUE_BRANCH", DEFAULT_QUEUE_BRANCH),
          help=f"Queue branch name (default: {DEFAULT_QUEUE_BRANCH}).",
      )
      parser.add_argument(
          "--no-push",
          action="store_true",
          help="Commit locally but do not push.",
      )
      parser.add_argument(
          "--dry-run",
          action="store_true",
          help="Compute and log without writing, committing, or pushing.",
      )
      return parser.parse_args(argv)


  def _now_iso() -> str:
      return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()


  def _resolve_entry_file(pbi_dir: Path) -> Path:
      """Return the per-type entry file inside ``pbi_dir``.

      The directory may contain ``PBI.md`` (feature / pr-feedback) or
      ``BUG.md`` (bug). The choice is driven by which file is present —
      a PBI always has exactly one entry file per its type.
      """
      for candidate in ENTRY_FILE_BY_TYPE.values():
          path = pbi_dir / candidate
          if path.is_file():
              return path
      raise QueueWriterError(
          f"no entry file (PBI.md, BUG.md, or FEEDBACK.md) found in {pbi_dir}"
      )


  def _state_folder_for(pbi_dir: Path, repo: Path) -> str:
      try:
          rel = pbi_dir.relative_to(repo / ".ralph")
      except ValueError as exc:
          raise QueueWriterError(
              f"PBI directory {pbi_dir} is not inside {repo / '.ralph'}"
          ) from exc
      return rel.parts[0]


  def main(argv: list[str] | None = None) -> int:
      args = _parse_args(argv if argv is not None else sys.argv[1:])

      try:
          repo = Path(args.repo).resolve()
          ensure_git_repo(repo)

          if not args.dry_run:
              print(f"switching to {args.branch}...", file=sys.stderr)
              checkout_queue_branch(repo, args.branch)

          pbi_dir = find_pbi_directory(repo, args.pbi_id)
          if pbi_dir is None:
              raise QueueWriterError(
                  f"PBI {args.pbi_id!r} not found in any .ralph/ state folder"
              )
          state_folder = _state_folder_for(pbi_dir, repo)
          entry_file = _resolve_entry_file(pbi_dir)

          frontmatter, body = read_frontmatter(entry_file)
          previous_severity = str(frontmatter.get("severity", ""))

          if previous_severity == args.severity:
              print(
                  f"PBI {args.pbi_id} already has severity={args.severity!r}; "
                  "nothing to do.",
                  file=sys.stderr,
              )
              result = PromoteResult(
                  pbi_id=args.pbi_id,
                  previous_severity=previous_severity,
                  new_severity=args.severity,
                  state_folder=state_folder,
                  entry_file=str(entry_file.relative_to(repo)).replace(
                      "\\", "/"
                  ),
                  repo_path=str(repo),
                  branch=args.branch,
                  commit_sha="",
                  pushed=False,
                  dry_run=args.dry_run,
              )
              print(json.dumps(asdict(result), indent=2, sort_keys=True))
              return 0

          if args.dry_run:
              print(
                  f"dry-run: would update severity from "
                  f"{previous_severity!r} to {args.severity!r} on "
                  f"{entry_file.relative_to(repo).as_posix()}.",
                  file=sys.stderr,
              )
              result = PromoteResult(
                  pbi_id=args.pbi_id,
                  previous_severity=previous_severity,
                  new_severity=args.severity,
                  state_folder=state_folder,
                  entry_file=str(entry_file.relative_to(repo)).replace(
                      "\\", "/"
                  ),
                  repo_path=str(repo),
                  branch=args.branch,
                  commit_sha="",
                  pushed=False,
                  dry_run=True,
              )
              print(json.dumps(asdict(result), indent=2, sort_keys=True))
              return 0

          # Mutate frontmatter in place, preserving every other key.
          frontmatter["severity"] = args.severity
          frontmatter["updated_at"] = _now_iso()
          write_frontmatter(entry_file, frontmatter, body)

          append_history(
              pbi_dir,
              actor="ralph-promote",
              action="promote",
              detail=f"severity: {previous_severity} -> {args.severity}",
          )

          history_file = pbi_dir / "HISTORY.md"
          commit_sha = commit_paths(
              repo,
              [entry_file, history_file],
              f"chore(queue): promote {args.pbi_id} "
              f"({previous_severity} -> {args.severity})",
          )

          pushed = False
          if not args.no_push:
              print(f"pushing {args.branch}...", file=sys.stderr)
              push(repo, args.branch)
              pushed = True

          result = PromoteResult(
              pbi_id=args.pbi_id,
              previous_severity=previous_severity,
              new_severity=args.severity,
              state_folder=state_folder,
              entry_file=str(entry_file.relative_to(repo)).replace("\\", "/"),
              repo_path=str(repo),
              branch=args.branch,
              commit_sha=commit_sha,
              pushed=pushed,
              dry_run=False,
          )
          print(json.dumps(asdict(result), indent=2, sort_keys=True))
          return 0

      except QueueWriterError as exc:
          print(f"error: {exc}", file=sys.stderr)
          return 2


  if __name__ == "__main__":
      raise SystemExit(main())
  ```

- [ ] 4. Run the test file; every test must now pass:
  ```
  uv run pytest tests/skills/test_ralph_promote.py -v
  ```
  Expected: eight tests pass. Exit code 0.

- [ ] 5. Run ruff and mypy:
  ```
  uv run ruff check skills/ralph-promote/ tests/skills/test_ralph_promote.py
  uv run mypy skills/ralph-promote/scripts/promote.py tests/skills/test_ralph_promote.py
  ```
  Expected: both report success.

- [ ] 6. Stage and commit:
  ```
  git add skills/ralph-promote/ tests/skills/test_ralph_promote.py
  git commit -m "feat(ralph-promote): bump PBI severity on ralph-queue with history audit"
  ```
  Expected: commit succeeds.

---

## Task 6 — Write the failing test for `ralph-triage`

**Files**
- Create: `tests/skills/test_ralph_triage.py`

**Steps**

- [ ] 1. Write `tests/skills/test_ralph_triage.py` with the exact content below:
  ```python
  """Tests for the ``ralph-triage`` skill's entry script.

  ``ralph-triage`` walks the ``.ralph/blocked/`` queue. For each PBI the
  human decides whether to:

  - return it to ``.ralph/inbox/`` (with ``attempts`` reset to 0 and a
    triage note appended to HISTORY.md), or
  - close it out by moving it to ``.ralph/archive/`` (the archive folder
    is created on demand) with a final HISTORY.md entry.

  Tests cover both destinations, the validation of ``--to``, and the
  resilience to missing inputs.
  """
  from __future__ import annotations

  import importlib.util
  import json
  import subprocess
  from collections.abc import Iterator
  from pathlib import Path
  from types import ModuleType

  import pytest
  import yaml


  REPO_ROOT = Path(__file__).resolve().parents[2]
  SCRIPT_PATH = REPO_ROOT / "skills" / "ralph-triage" / "scripts" / "triage.py"


  @pytest.fixture(scope="module")
  def triage_module() -> ModuleType:
      assert SCRIPT_PATH.is_file(), f"missing entry script at {SCRIPT_PATH}"
      spec = importlib.util.spec_from_file_location(
          "ralph_triage_script", SCRIPT_PATH
      )
      assert spec is not None and spec.loader is not None
      module = importlib.util.module_from_spec(spec)
      spec.loader.exec_module(module)
      return module


  def _git(cwd: Path, *args: str) -> str:
      result = subprocess.run(
          ["git", *args],
          cwd=str(cwd),
          check=True,
          capture_output=True,
          text=True,
      )
      return result.stdout


  @pytest.fixture
  def git_repo(tmp_path: Path) -> Iterator[Path]:
      bare = tmp_path / "remote.git"
      work = tmp_path / "work"
      subprocess.run(["git", "init", "--bare", str(bare)], check=True)
      subprocess.run(["git", "init", str(work)], check=True)
      _git(work, "config", "user.email", "test@example.com")
      _git(work, "config", "user.name", "Test User")
      _git(work, "commit", "--allow-empty", "-m", "chore: initial main commit")
      _git(work, "branch", "-M", "main")
      _git(work, "remote", "add", "origin", str(bare))
      _git(work, "push", "-u", "origin", "main")
      _git(work, "checkout", "-b", "ralph-queue")
      _git(work, "commit", "--allow-empty", "-m", "chore(queue): bootstrap")
      _git(work, "push", "-u", "origin", "ralph-queue")
      _git(work, "checkout", "main")
      yield work


  def _seed_blocked_pbi(
      repo: Path,
      pbi_id: str,
      pbi_type: str = "feature",
      attempts: int = 3,
      with_stuck: bool = True,
  ) -> Path:
      _git(repo, "checkout", "ralph-queue")
      pbi_dir = repo / ".ralph" / "blocked" / pbi_id
      pbi_dir.mkdir(parents=True)
      entry_name = {
          "feature": "PBI.md",
          "bug": "BUG.md",
          "pr-feedback": "FEEDBACK.md",
      }[pbi_type]
      (pbi_dir / entry_name).write_text(
          "---\n"
          f"id: {pbi_id}\n"
          f"type: {pbi_type}\n"
          "status: blocked\n"
          "severity: normal\n"
          f"attempts: {attempts}\n"
          "created_at: 2026-05-18T08:00:00+00:00\n"
          "updated_at: 2026-05-22T09:30:00+00:00\n"
          "---\n"
          "\n"
          "# body text\n",
          encoding="utf-8",
      )
      (pbi_dir / "HISTORY.md").write_text(
          "- timestamp: 2026-05-22T09:30:00+00:00\n"
          "- actor: ralph-executor\n"
          "- action: blocked\n"
          "- detail: attempt 3 exhausted\n",
          encoding="utf-8",
      )
      if with_stuck:
          (pbi_dir / "STUCK.md").write_text(
              "# Stuck\n\n"
              "what I tried: rebuilt the index three times.\n"
              "what would unblock me: a working repro of the failure.\n",
              encoding="utf-8",
          )
      _git(repo, "add", f".ralph/blocked/{pbi_id}")
      _git(repo, "commit", "-m", f"chore(test): seed blocked {pbi_id}")
      _git(repo, "push", "origin", "ralph-queue")
      _git(repo, "checkout", "main")
      return pbi_dir


  def _read_frontmatter(entry_file: Path) -> dict[str, object]:
      text = entry_file.read_text(encoding="utf-8")
      lines = text.splitlines()
      assert lines[0] == "---"
      end = next(i for i, line in enumerate(lines[1:], start=1) if line == "---")
      result = yaml.safe_load("\n".join(lines[1:end]))
      assert isinstance(result, dict)
      return result


  # ----------------------------------------------------------------------
  # Tests
  # ----------------------------------------------------------------------

  def test_triage_to_inbox_resets_attempts_and_moves(
      git_repo: Path,
      triage_module: ModuleType,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      _seed_blocked_pbi(git_repo, "WI-700", attempts=3)

      exit_code = triage_module.main(
          [
              "--pbi-id",
              "WI-700",
              "--to",
              "inbox",
              "--note",
              "tried a fresh REPRODUCE.md from QA; reset and retry",
              "--repo",
              str(git_repo),
          ]
      )
      assert exit_code == 0
      payload = json.loads(capsys.readouterr().out)
      assert payload["pbi_id"] == "WI-700"
      assert payload["destination"] == "inbox"
      assert payload["previous_state_folder"] == "blocked"
      assert payload["pushed"] is True
      assert payload["commit_sha"]

      _git(git_repo, "checkout", "ralph-queue")
      old_path = git_repo / ".ralph" / "blocked" / "WI-700"
      new_path = git_repo / ".ralph" / "inbox" / "WI-700"
      assert not old_path.exists(), "blocked/ directory should be gone"
      assert new_path.is_dir()

      fm = _read_frontmatter(new_path / "PBI.md")
      assert fm["attempts"] == 0
      assert fm["status"] == "inbox"
      assert fm["updated_at"] != "2026-05-22T09:30:00+00:00"

      history = (new_path / "HISTORY.md").read_text(encoding="utf-8")
      assert "ralph-triage" in history
      assert "return-to-inbox" in history
      assert "tried a fresh REPRODUCE.md from QA" in history

      latest = _git(git_repo, "log", "-1", "--pretty=%s").strip()
      assert latest == "chore(queue): triage WI-700 (blocked -> inbox)"


  def test_triage_to_archive_creates_archive_folder(
      git_repo: Path,
      triage_module: ModuleType,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      _seed_blocked_pbi(git_repo, "WI-800", attempts=4)

      # Pre-condition: archive/ does not yet exist on the queue branch.
      _git(git_repo, "checkout", "ralph-queue")
      assert not (git_repo / ".ralph" / "archive").exists()
      _git(git_repo, "checkout", "main")

      exit_code = triage_module.main(
          [
              "--pbi-id",
              "WI-800",
              "--to",
              "archive",
              "--note",
              "ambiguous scope; closing out, BA will re-submit if needed",
              "--repo",
              str(git_repo),
          ]
      )
      assert exit_code == 0
      payload = json.loads(capsys.readouterr().out)
      assert payload["destination"] == "archive"
      assert payload["archive_created"] is True

      _git(git_repo, "checkout", "ralph-queue")
      assert (git_repo / ".ralph" / "archive").is_dir()
      old_path = git_repo / ".ralph" / "blocked" / "WI-800"
      new_path = git_repo / ".ralph" / "archive" / "WI-800"
      assert not old_path.exists()
      assert new_path.is_dir()

      fm = _read_frontmatter(new_path / "PBI.md")
      assert fm["status"] == "archive"
      # attempts is preserved on archive (record of how hard we tried).
      assert fm["attempts"] == 4

      history = (new_path / "HISTORY.md").read_text(encoding="utf-8")
      assert "ralph-triage" in history
      assert "archive" in history
      assert "ambiguous scope" in history


  def test_triage_rejects_unknown_destination(
      git_repo: Path,
      triage_module: ModuleType,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      _seed_blocked_pbi(git_repo, "WI-900")

      with pytest.raises(SystemExit) as exc:
          triage_module.main(
              [
                  "--pbi-id",
                  "WI-900",
                  "--to",
                  "trash",
                  "--note",
                  "no",
                  "--repo",
                  str(git_repo),
              ]
          )
      assert exc.value.code == 2


  def test_triage_errors_on_missing_pbi(
      git_repo: Path,
      triage_module: ModuleType,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      exit_code = triage_module.main(
          [
              "--pbi-id",
              "WI-NOPE",
              "--to",
              "inbox",
              "--note",
              "missing",
              "--repo",
              str(git_repo),
          ]
      )
      assert exit_code == 2
      stderr = capsys.readouterr().err.lower()
      assert "not found" in stderr or "no such" in stderr or "blocked" in stderr


  def test_triage_rejects_pbi_outside_blocked(
      git_repo: Path,
      triage_module: ModuleType,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      # Seed in inbox/ rather than blocked/.
      _git(git_repo, "checkout", "ralph-queue")
      pbi_dir = git_repo / ".ralph" / "inbox" / "WI-1000"
      pbi_dir.mkdir(parents=True)
      (pbi_dir / "PBI.md").write_text(
          "---\n"
          "id: WI-1000\n"
          "type: feature\n"
          "status: inbox\n"
          "severity: normal\n"
          "attempts: 0\n"
          "created_at: 2026-05-24T10:00:00+00:00\n"
          "updated_at: 2026-05-24T10:00:00+00:00\n"
          "---\n"
          "\n"
          "# body\n",
          encoding="utf-8",
      )
      (pbi_dir / "HISTORY.md").write_text("", encoding="utf-8")
      _git(git_repo, "add", ".ralph/inbox/WI-1000")
      _git(git_repo, "commit", "-m", "chore(test): seed inbox WI-1000")
      _git(git_repo, "push", "origin", "ralph-queue")
      _git(git_repo, "checkout", "main")

      exit_code = triage_module.main(
          [
              "--pbi-id",
              "WI-1000",
              "--to",
              "inbox",
              "--note",
              "won't work",
              "--repo",
              str(git_repo),
          ]
      )
      assert exit_code == 2
      stderr = capsys.readouterr().err.lower()
      assert "blocked" in stderr


  def test_triage_requires_note(
      git_repo: Path,
      triage_module: ModuleType,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      _seed_blocked_pbi(git_repo, "WI-1100")

      with pytest.raises(SystemExit) as exc:
          triage_module.main(
              [
                  "--pbi-id",
                  "WI-1100",
                  "--to",
                  "inbox",
                  "--repo",
                  str(git_repo),
              ]
          )
      # argparse exits with 2 when a required argument is missing.
      assert exc.value.code == 2


  def test_triage_dry_run_leaves_files_in_place(
      git_repo: Path,
      triage_module: ModuleType,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      _seed_blocked_pbi(git_repo, "WI-1200")
      before = _git(git_repo, "ls-remote", "origin", "ralph-queue").strip()

      exit_code = triage_module.main(
          [
              "--pbi-id",
              "WI-1200",
              "--to",
              "inbox",
              "--note",
              "would-be note",
              "--repo",
              str(git_repo),
              "--dry-run",
          ]
      )
      assert exit_code == 0
      payload = json.loads(capsys.readouterr().out)
      assert payload["dry_run"] is True
      assert payload["pushed"] is False
      assert payload["commit_sha"] == ""

      _git(git_repo, "checkout", "ralph-queue")
      assert (git_repo / ".ralph" / "blocked" / "WI-1200").is_dir()
      assert not (git_repo / ".ralph" / "inbox" / "WI-1200").exists()
      after = _git(git_repo, "ls-remote", "origin", "ralph-queue").strip()
      assert before == after


  def test_triage_no_push_keeps_remote_unchanged(
      git_repo: Path,
      triage_module: ModuleType,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      _seed_blocked_pbi(git_repo, "WI-1300")
      before = _git(git_repo, "ls-remote", "origin", "ralph-queue").strip()

      exit_code = triage_module.main(
          [
              "--pbi-id",
              "WI-1300",
              "--to",
              "archive",
              "--note",
              "closing out",
              "--repo",
              str(git_repo),
              "--no-push",
          ]
      )
      assert exit_code == 0
      payload = json.loads(capsys.readouterr().out)
      assert payload["pushed"] is False
      assert payload["commit_sha"]
      after = _git(git_repo, "ls-remote", "origin", "ralph-queue").strip()
      assert before == after
  ```

- [ ] 2. Run the new test file; it must fail because the script does not yet exist:
  ```
  uv run pytest tests/skills/test_ralph_triage.py -v
  ```
  Expected: the fixture loader raises `AssertionError: missing entry script at <SCRIPT_PATH>`. Red.

- [ ] 3. Do NOT commit yet — the failing tests are consumed in Task 7.

---

## Task 7 — Implement `ralph-triage`

**Files**
- Create: `skills/ralph-triage/SKILL.md`
- Create: `skills/ralph-triage/scripts/__init__.py`
- Create: `skills/ralph-triage/scripts/triage.py`

**Steps**

- [ ] 1. Write `skills/ralph-triage/scripts/__init__.py` containing exactly:
  ```python
  """Empty package marker."""
  ```

- [ ] 2. Write `skills/ralph-triage/SKILL.md` with the exact content below:
  ```markdown
  ---
  name: ralph-triage
  description: Walk the .ralph/blocked/ queue and route a stuck PBI to its next state. Two destinations are allowed - inbox (return for retry, with attempts reset to 0) or archive (close out, creating .ralph/archive/ on demand). Every triage decision requires a --note explaining the operator's reasoning; the note is appended to the PBI's HISTORY.md so the audit trail is preserved.
  ---

  # ralph-triage

  ## What this skill does

  `ralph-triage` is the on-call / tech-lead surface for the blocked queue.
  When Ralph has marked a PBI blocked (because it self-halted via
  STUCK.md or because the executor's cycle detector tripped on it), the
  PBI sits in `.ralph/blocked/<pbi-id>/`. A human reads the STUCK.md and
  HISTORY.md and chooses one of two paths:

  - **`--to inbox`** — return the PBI to the inbox queue. The skill
    resets `attempts` to 0, updates `status` to `inbox`, refreshes
    `updated_at`, moves the directory to `.ralph/inbox/<pbi-id>/`, and
    appends a structured HISTORY.md entry containing the human's
    `--note`. The PBI re-enters the priority lanes on the next executor
    iteration.
  - **`--to archive`** — close the PBI out. The skill updates `status`
    to `archive`, refreshes `updated_at`, moves the directory to
    `.ralph/archive/<pbi-id>/` (creating the archive folder on demand
    via `git add` semantics), and appends a final HISTORY.md entry.
    `attempts` is preserved on archive — the record of how hard Ralph
    tried is part of the audit trail.

  ## When to use it

  - Daily blocked-queue review: walk every PBI in `blocked/`, decide
    inbox vs archive.
  - Reactive triage: a STUCK.md from Ralph mentions an ambiguity that
    the on-call engineer can resolve; return to inbox after adding
    clarifying context to the PBI itself (separately).
  - Cleanup: a PBI is obsolete (the feature was descoped, the bug was
    fixed elsewhere). Archive it with a note.

  ## Inputs

  | Flag | Required | Description |
  |---|---|---|
  | `--pbi-id <id>` | yes | PBI identifier matching the directory name under `.ralph/blocked/`. |
  | `--to <destination>` | yes | Where to route the PBI. One of `inbox`, `archive`. |
  | `--note <text>` | yes | Operator's reasoning for the decision. Appended verbatim to HISTORY.md. |
  | `--repo <path>` | yes | Absolute path to an existing checkout of the target service repo. |
  | `--branch <name>` | no | Queue branch name. Default: `ralph-queue` (overridable via `RALPH_QUEUE_BRANCH`). |
  | `--no-push` | no | Commit locally but do not push. |
  | `--dry-run` | no | Compute and log without moving, committing, or pushing. |

  ## Environment variables

  | Variable | Purpose |
  |---|---|
  | `RALPH_QUEUE_BRANCH` | Default queue branch name; overridden by `--branch`. Optional; defaults to `ralph-queue`. |

  ## Output

  Prints a JSON summary to stdout on success. Example for `--to inbox`:

  ```json
  {
    "pbi_id": "WI-1234",
    "destination": "inbox",
    "previous_state_folder": "blocked",
    "old_path": ".ralph/blocked/WI-1234",
    "new_path": ".ralph/inbox/WI-1234",
    "attempts_reset_to_zero": true,
    "archive_created": false,
    "repo_path": "/home/dev/service-auth",
    "branch": "ralph-queue",
    "commit_sha": "abcdef0123456789",
    "pushed": true,
    "dry_run": false
  }
  ```

  ## How it is invoked

  ```bash
  uv run python skills/ralph-triage/scripts/triage.py \
      --pbi-id WI-1234 \
      --to inbox \
      --note "QA produced a working repro; resetting for retry" \
      --repo /path/to/service-auth
  ```

  ## What this skill does NOT do

  - It does not touch any PBI outside `.ralph/blocked/`. PBIs in
    `inbox/`, `current/`, `pending-pr/`, `done/`, or `archive/` cannot
    be triaged — only the blocked queue is the human-triage surface.
  - It does not edit the PBI's body content. The only mutations are to
    the frontmatter (`status`, `attempts`, `updated_at`) and HISTORY.md.
  - It does not delete the STUCK.md file when returning to inbox. The
    STUCK.md remains as historical context for the next attempt; Ralph's
    PROMPT.md tells it to read HISTORY.md and treat any present
    STUCK.md as prior diagnosis, not a current halt.
  - It does not require ADO credentials — this is purely a git mutation.
  ```

- [ ] 3. Write `skills/ralph-triage/scripts/triage.py` with the exact content below:
  ```python
  """``ralph-triage`` skill entry point.

  Walks the ``.ralph/blocked/`` queue. Routes a blocked PBI either back
  to ``.ralph/inbox/`` (with attempts reset to 0) or out to
  ``.ralph/archive/`` (creating the folder on demand). The operator's
  reasoning is captured in HISTORY.md so every triage decision leaves an
  audit trail.
  """
  from __future__ import annotations

  import argparse
  import json
  import os
  import shutil
  import sys
  from dataclasses import asdict, dataclass
  from datetime import datetime, timezone
  from pathlib import Path

  _REPO_ROOT = Path(__file__).resolve().parents[3]
  if str(_REPO_ROOT) not in sys.path:
      sys.path.insert(0, str(_REPO_ROOT))

  from scripts.queue_writer import (  # noqa: E402
      QueueWriterError,
      append_history,
      checkout_queue_branch,
      commit_paths,
      ensure_git_repo,
      push,
      read_frontmatter,
      write_frontmatter,
  )


  DEFAULT_QUEUE_BRANCH = "ralph-queue"
  ALLOWED_DESTINATIONS = ("inbox", "archive")
  ENTRY_FILE_BY_TYPE = {
      "feature": "PBI.md",
      "bug": "BUG.md",
      "pr-feedback": "FEEDBACK.md",
  }


  @dataclass
  class TriageResult:
      pbi_id: str
      destination: str
      previous_state_folder: str
      old_path: str
      new_path: str
      attempts_reset_to_zero: bool
      archive_created: bool
      repo_path: str
      branch: str
      commit_sha: str
      pushed: bool
      dry_run: bool


  def _parse_args(argv: list[str]) -> argparse.Namespace:
      parser = argparse.ArgumentParser(
          prog="ralph-triage",
          description=(
              "Triage a PBI in .ralph/blocked/. Routes the PBI either back "
              "to inbox/ (for retry, attempts reset to 0) or to archive/ "
              "(closed out). A note explaining the decision is required."
          ),
      )
      parser.add_argument(
          "--pbi-id",
          required=True,
          help="PBI identifier matching the directory name under .ralph/blocked/.",
      )
      parser.add_argument(
          "--to",
          dest="destination",
          required=True,
          choices=ALLOWED_DESTINATIONS,
          help="Destination state folder. One of inbox, archive.",
      )
      parser.add_argument(
          "--note",
          required=True,
          help="Operator's reasoning, appended to HISTORY.md.",
      )
      parser.add_argument(
          "--repo",
          required=True,
          help="Absolute path to the target service repo checkout.",
      )
      parser.add_argument(
          "--branch",
          default=os.environ.get("RALPH_QUEUE_BRANCH", DEFAULT_QUEUE_BRANCH),
          help=f"Queue branch name (default: {DEFAULT_QUEUE_BRANCH}).",
      )
      parser.add_argument(
          "--no-push",
          action="store_true",
          help="Commit locally but do not push.",
      )
      parser.add_argument(
          "--dry-run",
          action="store_true",
          help="Compute and log without moving, committing, or pushing.",
      )
      return parser.parse_args(argv)


  def _now_iso() -> str:
      return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()


  def _resolve_blocked_pbi(repo: Path, pbi_id: str) -> Path:
      blocked = repo / ".ralph" / "blocked" / pbi_id
      if blocked.is_dir():
          return blocked
      # Surface a helpful error when the PBI exists but in another folder.
      base = repo / ".ralph"
      for folder in ("current", "inbox", "pending-pr", "done", "archive"):
          if (base / folder / pbi_id).is_dir():
              raise QueueWriterError(
                  f"PBI {pbi_id!r} is in .ralph/{folder}/, not in "
                  f".ralph/blocked/. ralph-triage only operates on the "
                  f"blocked queue."
              )
      raise QueueWriterError(
          f"PBI {pbi_id!r} not found under .ralph/blocked/ "
          f"(or any other state folder)"
      )


  def _resolve_entry_file(pbi_dir: Path) -> Path:
      for candidate in ENTRY_FILE_BY_TYPE.values():
          path = pbi_dir / candidate
          if path.is_file():
              return path
      raise QueueWriterError(
          f"no entry file (PBI.md, BUG.md, or FEEDBACK.md) found in {pbi_dir}"
      )


  def _move_directory(src: Path, dest: Path) -> None:
      """Move ``src`` to ``dest``. Creates ``dest.parent`` on demand.

      Uses ``shutil.move`` so it works whether ``src`` and ``dest`` are
      on the same filesystem or not. The caller is responsible for
      committing both the removal (src) and the addition (dest) via
      ``commit_paths``.
      """
      if dest.exists():
          raise QueueWriterError(
              f"destination already exists: {dest}; refusing to overwrite"
          )
      dest.parent.mkdir(parents=True, exist_ok=True)
      shutil.move(str(src), str(dest))


  def main(argv: list[str] | None = None) -> int:
      args = _parse_args(argv if argv is not None else sys.argv[1:])

      try:
          repo = Path(args.repo).resolve()
          ensure_git_repo(repo)

          if not args.dry_run:
              print(f"switching to {args.branch}...", file=sys.stderr)
              checkout_queue_branch(repo, args.branch)

          old_path = _resolve_blocked_pbi(repo, args.pbi_id)
          archive_existed_before = (repo / ".ralph" / "archive").is_dir()
          new_path = repo / ".ralph" / args.destination / args.pbi_id

          if args.dry_run:
              print(
                  f"dry-run: would move "
                  f"{old_path.relative_to(repo).as_posix()} -> "
                  f"{new_path.relative_to(repo).as_posix()}.",
                  file=sys.stderr,
              )
              result = TriageResult(
                  pbi_id=args.pbi_id,
                  destination=args.destination,
                  previous_state_folder="blocked",
                  old_path=old_path.relative_to(repo).as_posix(),
                  new_path=new_path.relative_to(repo).as_posix(),
                  attempts_reset_to_zero=(args.destination == "inbox"),
                  archive_created=(
                      args.destination == "archive" and not archive_existed_before
                  ),
                  repo_path=str(repo),
                  branch=args.branch,
                  commit_sha="",
                  pushed=False,
                  dry_run=True,
              )
              print(json.dumps(asdict(result), indent=2, sort_keys=True))
              return 0

          # Update frontmatter BEFORE moving so the entry file is
          # rewritten in place and the move that follows captures the
          # updated bytes.
          entry_file = _resolve_entry_file(old_path)
          frontmatter, body = read_frontmatter(entry_file)
          frontmatter["status"] = args.destination
          frontmatter["updated_at"] = _now_iso()
          if args.destination == "inbox":
              frontmatter["attempts"] = 0
          write_frontmatter(entry_file, frontmatter, body)

          # Append the triage entry to HISTORY.md before the move so the
          # final HISTORY.md is part of the moved directory.
          action_name = (
              "return-to-inbox" if args.destination == "inbox" else "archive"
          )
          append_history(
              old_path,
              actor="ralph-triage",
              action=action_name,
              detail=args.note,
          )

          # Now move the directory to its new state folder.
          _move_directory(old_path, new_path)

          # Stage both the removal of the old path and the addition of the
          # new path. ``commit_paths`` accepts absolute paths inside the
          # repo; for the deleted directory we still record its location
          # so ``git add`` notices the removal.
          commit_message = (
              f"chore(queue): triage {args.pbi_id} "
              f"(blocked -> {args.destination})"
          )
          commit_sha = commit_paths(
              repo,
              [old_path, new_path],
              commit_message,
          )

          pushed = False
          if not args.no_push:
              print(f"pushing {args.branch}...", file=sys.stderr)
              push(repo, args.branch)
              pushed = True

          archive_created = (
              args.destination == "archive" and not archive_existed_before
          )
          result = TriageResult(
              pbi_id=args.pbi_id,
              destination=args.destination,
              previous_state_folder="blocked",
              old_path=old_path.relative_to(repo).as_posix(),
              new_path=new_path.relative_to(repo).as_posix(),
              attempts_reset_to_zero=(args.destination == "inbox"),
              archive_created=archive_created,
              repo_path=str(repo),
              branch=args.branch,
              commit_sha=commit_sha,
              pushed=pushed,
              dry_run=False,
          )
          print(json.dumps(asdict(result), indent=2, sort_keys=True))
          return 0

      except QueueWriterError as exc:
          print(f"error: {exc}", file=sys.stderr)
          return 2


  if __name__ == "__main__":
      raise SystemExit(main())
  ```

- [ ] 4. Run the test file; every test must now pass:
  ```
  uv run pytest tests/skills/test_ralph_triage.py -v
  ```
  Expected: eight tests pass. Exit code 0.

- [ ] 5. Run ruff and mypy:
  ```
  uv run ruff check skills/ralph-triage/ tests/skills/test_ralph_triage.py
  uv run mypy skills/ralph-triage/scripts/triage.py tests/skills/test_ralph_triage.py
  ```
  Expected: both report success.

- [ ] 6. Stage and commit:
  ```
  git add skills/ralph-triage/ tests/skills/test_ralph_triage.py
  git commit -m "feat(ralph-triage): route blocked PBIs back to inbox or out to archive"
  ```
  Expected: commit succeeds.

---

## Task 8 — Refactor `ralph-add` onto the shared queue-writer

**Files**
- Modify: `skills/ralph-add/scripts/add.py`

**Steps**

- [ ] 1. Read Plan 3's `skills/ralph-add/scripts/add.py` end-to-end and locate the five inlined helpers it currently owns: `_run_git`, `_ensure_git_repo`, `_checkout_queue_branch`, `_commit_pbi`, `_push`. These duplicate the functionality now in `scripts/queue_writer.py`.

- [ ] 2. Replace the imports block at the top of `skills/ralph-add/scripts/add.py` so it reads (preserving every existing import that is NOT one of the helpers being removed):
  ```python
  # Make ``scripts.ado_client`` (Plan 2) and ``scripts.queue_writer`` (Plan 10)
  # importable when this script is invoked directly with
  # ``uv run python skills/ralph-add/scripts/add.py``.
  _REPO_ROOT = Path(__file__).resolve().parents[3]
  if str(_REPO_ROOT) not in sys.path:
      sys.path.insert(0, str(_REPO_ROOT))

  from scripts.ado_client import AdoClient, AdoError  # noqa: E402
  from scripts.queue_writer import (  # noqa: E402
      QueueWriterError,
      checkout_queue_branch,
      commit_paths,
      ensure_git_repo,
      push,
  )
  ```

- [ ] 3. Delete the inlined `_run_git`, `_ensure_git_repo`, `_checkout_queue_branch`, `_commit_pbi`, and `_push` functions from `skills/ralph-add/scripts/add.py`. Replace their call sites:
  - Replace `_ensure_git_repo(repo)` with `ensure_git_repo(repo)` (the imported helper raises `QueueWriterError`, which the main loop now catches alongside `_FatalError`).
  - Replace `_checkout_queue_branch(repo, args.branch)` with `checkout_queue_branch(repo, args.branch)`.
  - Replace `_commit_pbi(repo, pbi_dir, message)` with `commit_paths(repo, [pbi_dir], message)`.
  - Replace `_push(repo, args.branch)` with `push(repo, args.branch)`.

- [ ] 4. Update the exception handling in `main()` so `QueueWriterError` is caught the same way `_FatalError` is. The relevant `try/except` block in `main` should read:
  ```python
      try:
          # ... existing body unchanged ...

      except _FatalError as exc:
          print(f"error: {exc}", file=sys.stderr)
          return 2
      except QueueWriterError as exc:
          print(f"error: {exc}", file=sys.stderr)
          return 2
      except AdoError as exc:
          print(f"error: ADO request failed: {exc}", file=sys.stderr)
          return 2
      except subprocess.CalledProcessError as exc:
          stderr = exc.stderr or ""
          print(
              f"error: git command failed ({exc.returncode}): "
              f"{' '.join(exc.cmd)}\n{stderr}",
              file=sys.stderr,
          )
          return 2
  ```
  Keep the `subprocess.CalledProcessError` clause for defence-in-depth, even though `commit_paths` / `checkout_queue_branch` now raise `QueueWriterError` instead of letting `CalledProcessError` propagate. The `CalledProcessError` clause exists to handle any future direct subprocess usage that might be reintroduced.

- [ ] 5. Run the existing Plan 3 tests; they must still pass (the refactor is behaviour-preserving):
  ```
  uv run pytest tests/skills/test_ralph_add.py -v
  ```
  Expected: all ten tests from Plan 3 still pass. Exit code 0.

- [ ] 6. Run the new Plan 10 tests alongside Plan 3's; they must all pass:
  ```
  uv run pytest tests/skills/ tests/test_queue_writer.py -v
  ```
  Expected: every skill test passes (10 from Plan 3 + 7 from `ralph-cancel` + 8 from `ralph-promote` + 8 from `ralph-triage` + 13 from the queue-writer unit tests = 46 tests). Exit code 0.

- [ ] 7. Run ruff and mypy across the modified file:
  ```
  uv run ruff check skills/ralph-add/scripts/add.py
  uv run mypy skills/ralph-add/scripts/add.py
  ```
  Expected: both report success.

- [ ] 8. Stage and commit:
  ```
  git add skills/ralph-add/scripts/add.py
  git commit -m "refactor(ralph-add): consume scripts.queue_writer instead of inlined git helpers"
  ```
  Expected: commit succeeds. This is the planned refactor flagged in the plan header.

---

## Task 9 — Cross-skill smoke test

**Files**
- Create: `tests/skills/test_supervisor_skills_smoke.py`

**Steps**

- [ ] 1. Write `tests/skills/test_supervisor_skills_smoke.py` with the exact content below. The smoke test threads a single PBI through three supervisor skills (`ralph-promote` → `ralph-cancel` is invalid; we instead do `ralph-promote` → `ralph-triage` from a blocked seed) to prove the skills compose:
  ```python
  """End-to-end-ish smoke test threading three supervisor skills together.

  The test seeds a PBI in ``.ralph/blocked/``, invokes ``ralph-promote``
  to escalate its severity (which leaves it in blocked/), then invokes
  ``ralph-triage --to inbox`` to return it for retry. The resulting
  ``ralph-queue`` state is asserted in one place to confirm the skills
  agree on the on-disk schema produced by the shared queue-writer.
  """
  from __future__ import annotations

  import importlib.util
  import json
  import subprocess
  from collections.abc import Iterator
  from pathlib import Path
  from types import ModuleType

  import pytest
  import yaml


  REPO_ROOT = Path(__file__).resolve().parents[2]


  def _load(script_relpath: str, name: str) -> ModuleType:
      path = REPO_ROOT / script_relpath
      assert path.is_file(), f"missing script {path}"
      spec = importlib.util.spec_from_file_location(name, path)
      assert spec is not None and spec.loader is not None
      module = importlib.util.module_from_spec(spec)
      spec.loader.exec_module(module)
      return module


  @pytest.fixture(scope="module")
  def promote_module() -> ModuleType:
      return _load(
          "skills/ralph-promote/scripts/promote.py",
          "ralph_promote_smoke",
      )


  @pytest.fixture(scope="module")
  def triage_module() -> ModuleType:
      return _load(
          "skills/ralph-triage/scripts/triage.py",
          "ralph_triage_smoke",
      )


  @pytest.fixture(scope="module")
  def cancel_module() -> ModuleType:
      return _load(
          "skills/ralph-cancel/scripts/cancel.py",
          "ralph_cancel_smoke",
      )


  def _git(cwd: Path, *args: str) -> str:
      result = subprocess.run(
          ["git", *args],
          cwd=str(cwd),
          check=True,
          capture_output=True,
          text=True,
      )
      return result.stdout


  @pytest.fixture
  def git_repo(tmp_path: Path) -> Iterator[Path]:
      bare = tmp_path / "remote.git"
      work = tmp_path / "work"
      subprocess.run(["git", "init", "--bare", str(bare)], check=True)
      subprocess.run(["git", "init", str(work)], check=True)
      _git(work, "config", "user.email", "test@example.com")
      _git(work, "config", "user.name", "Test User")
      _git(work, "commit", "--allow-empty", "-m", "chore: initial main commit")
      _git(work, "branch", "-M", "main")
      _git(work, "remote", "add", "origin", str(bare))
      _git(work, "push", "-u", "origin", "main")
      _git(work, "checkout", "-b", "ralph-queue")
      _git(work, "commit", "--allow-empty", "-m", "chore(queue): bootstrap")
      _git(work, "push", "-u", "origin", "ralph-queue")
      _git(work, "checkout", "main")
      yield work


  def _seed_blocked(repo: Path, pbi_id: str) -> Path:
      _git(repo, "checkout", "ralph-queue")
      pbi_dir = repo / ".ralph" / "blocked" / pbi_id
      pbi_dir.mkdir(parents=True)
      (pbi_dir / "PBI.md").write_text(
          "---\n"
          f"id: {pbi_id}\n"
          "type: feature\n"
          "status: blocked\n"
          "severity: normal\n"
          "attempts: 3\n"
          "created_at: 2026-05-18T08:00:00+00:00\n"
          "updated_at: 2026-05-22T09:30:00+00:00\n"
          "---\n"
          "\n"
          "# body\n",
          encoding="utf-8",
      )
      (pbi_dir / "HISTORY.md").write_text("", encoding="utf-8")
      _git(repo, "add", f".ralph/blocked/{pbi_id}")
      _git(repo, "commit", "-m", "chore(test): seed")
      _git(repo, "push", "origin", "ralph-queue")
      _git(repo, "checkout", "main")
      return pbi_dir


  def _read_frontmatter(entry_file: Path) -> dict[str, object]:
      text = entry_file.read_text(encoding="utf-8")
      lines = text.splitlines()
      assert lines[0] == "---"
      end = next(i for i, line in enumerate(lines[1:], start=1) if line == "---")
      result = yaml.safe_load("\n".join(lines[1:end]))
      assert isinstance(result, dict)
      return result


  def test_promote_then_triage_round_trip(
      git_repo: Path,
      promote_module: ModuleType,
      triage_module: ModuleType,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      _seed_blocked(git_repo, "WI-9000")

      # Step 1: promote while blocked.
      assert (
          promote_module.main(
              [
                  "--pbi-id",
                  "WI-9000",
                  "--severity",
                  "high",
                  "--repo",
                  str(git_repo),
              ]
          )
          == 0
      )
      capsys.readouterr()  # discard JSON; assertions follow from on-disk state

      # Step 2: triage back to inbox with the operator's note.
      assert (
          triage_module.main(
              [
                  "--pbi-id",
                  "WI-9000",
                  "--to",
                  "inbox",
                  "--note",
                  "reset after severity bump",
                  "--repo",
                  str(git_repo),
              ]
          )
          == 0
      )
      capsys.readouterr()

      _git(git_repo, "checkout", "ralph-queue")
      assert not (git_repo / ".ralph" / "blocked" / "WI-9000").exists()
      new_pbi = git_repo / ".ralph" / "inbox" / "WI-9000"
      assert new_pbi.is_dir()
      fm = _read_frontmatter(new_pbi / "PBI.md")
      assert fm["severity"] == "high"
      assert fm["status"] == "inbox"
      assert fm["attempts"] == 0

      history = (new_pbi / "HISTORY.md").read_text(encoding="utf-8")
      assert "ralph-promote" in history
      assert "ralph-triage" in history
      assert "normal -> high" in history
      assert "reset after severity bump" in history


  def test_cancel_then_promote_is_independent(
      git_repo: Path,
      cancel_module: ModuleType,
      promote_module: ModuleType,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      # Seed a PBI in current/ and cancel it; then promote (which can
      # operate on any state folder) and confirm the CANCEL file is
      # untouched and the severity update lands.
      _git(git_repo, "checkout", "ralph-queue")
      pbi_dir = git_repo / ".ralph" / "current" / "WI-9100"
      pbi_dir.mkdir(parents=True)
      (pbi_dir / "PBI.md").write_text(
          "---\n"
          "id: WI-9100\n"
          "type: feature\n"
          "status: current\n"
          "severity: normal\n"
          "attempts: 1\n"
          "created_at: 2026-05-24T10:00:00+00:00\n"
          "updated_at: 2026-05-24T10:00:00+00:00\n"
          "---\n"
          "\n"
          "# body\n",
          encoding="utf-8",
      )
      (pbi_dir / "HISTORY.md").write_text("", encoding="utf-8")
      _git(git_repo, "add", ".ralph/current/WI-9100")
      _git(git_repo, "commit", "-m", "chore(test): seed current WI-9100")
      _git(git_repo, "push", "origin", "ralph-queue")
      _git(git_repo, "checkout", "main")

      assert (
          cancel_module.main(
              [
                  "--pbi-id",
                  "WI-9100",
                  "--repo",
                  str(git_repo),
              ]
          )
          == 0
      )
      capsys.readouterr()

      assert (
          promote_module.main(
              [
                  "--pbi-id",
                  "WI-9100",
                  "--severity",
                  "critical",
                  "--repo",
                  str(git_repo),
              ]
          )
          == 0
      )
      capsys.readouterr()

      _git(git_repo, "checkout", "ralph-queue")
      cancel_file = git_repo / ".ralph" / "current" / "WI-9100" / "CANCEL"
      assert cancel_file.is_file()
      assert cancel_file.read_bytes() == b""
      fm = _read_frontmatter(
          git_repo / ".ralph" / "current" / "WI-9100" / "PBI.md"
      )
      assert fm["severity"] == "critical"
  ```

- [ ] 2. Run the smoke test:
  ```
  uv run pytest tests/skills/test_supervisor_skills_smoke.py -v
  ```
  Expected: two tests pass. Exit code 0.

- [ ] 3. Run ruff and mypy:
  ```
  uv run ruff check tests/skills/test_supervisor_skills_smoke.py
  uv run mypy tests/skills/test_supervisor_skills_smoke.py
  ```
  Expected: both report success.

- [ ] 4. Stage and commit:
  ```
  git add tests/skills/test_supervisor_skills_smoke.py
  git commit -m "test(supervisor-skills): smoke test composing cancel, promote, triage"
  ```
  Expected: commit succeeds.

---

## Task 10 — Full toolchain pass

**Files**
- (none — toolchain-only step)

**Steps**

- [ ] 1. Run the full local gate to confirm Plan 10's deliverables are green and don't disturb Plans 1, 2, or 3:
  ```
  uv run ruff check .
  uv run ruff format --check .
  uv run mypy ralph_executor scripts skills tests
  uv run pytest
  ```
  Expected: every command exits 0. Ruff: `All checks passed!`. Format: nothing to reformat. Mypy: `Success: no issues found in N source files`. Pytest: all tests pass — Plan 1 (8) + Plan 2 (10) + Plan 3 (10) + Plan 10's queue-writer (13) + ralph-cancel (7) + ralph-promote (8) + ralph-triage (8) + smoke (2) = 66 tests.

- [ ] 2. Confirm each skill's `--help` is meaningful:
  ```
  uv run python skills/ralph-cancel/scripts/cancel.py --help
  uv run python skills/ralph-promote/scripts/promote.py --help
  uv run python skills/ralph-triage/scripts/triage.py --help
  ```
  Expected:
  - `ralph-cancel` help text mentions `--pbi-id`, `--repo`, `--branch`, `--no-push`, `--dry-run`.
  - `ralph-promote` help text mentions `--pbi-id`, `--severity` (with the four allowed values), `--repo`, `--branch`, `--no-push`, `--dry-run`.
  - `ralph-triage` help text mentions `--pbi-id`, `--to` (with `inbox`/`archive`), `--note`, `--repo`, `--branch`, `--no-push`, `--dry-run`.

- [ ] 3. Confirm each `SKILL.md` parses as Claude Code skill frontmatter:
  ```
  uv run python -c "
  import yaml
  from pathlib import Path
  for skill in ('ralph-cancel', 'ralph-promote', 'ralph-triage'):
      text = Path(f'skills/{skill}/SKILL.md').read_text(encoding='utf-8')
      lines = text.splitlines()
      assert lines[0] == '---', f'{skill}: SKILL.md must start with frontmatter fence'
      end = next(i for i, line in enumerate(lines[1:], start=1) if line == '---')
      fm = yaml.safe_load('\n'.join(lines[1:end]))
      assert fm['name'] == skill, f'{skill}: name mismatch'
      assert isinstance(fm['description'], str) and len(fm['description']) >= 80
      print(f'SKILL.md frontmatter OK: {fm[\"name\"]}')
  "
  ```
  Expected output:
  ```
  SKILL.md frontmatter OK: ralph-cancel
  SKILL.md frontmatter OK: ralph-promote
  SKILL.md frontmatter OK: ralph-triage
  ```

- [ ] 4. Confirm the commit history is clean and conventional:
  ```
  git log --oneline -12
  ```
  Expected: at the top of the log, Plan 10's commits are visible in order:
  - `feat(queue-writer): add shared git + frontmatter helpers for supervisor skills`
  - `feat(ralph-cancel): drop CANCEL sentinel into current/ and push to ralph-queue`
  - `feat(ralph-promote): bump PBI severity on ralph-queue with history audit`
  - `feat(ralph-triage): route blocked PBIs back to inbox or out to archive`
  - `refactor(ralph-add): consume scripts.queue_writer instead of inlined git helpers`
  - `test(supervisor-skills): smoke test composing cancel, promote, triage`

---

## Verification

This is the orchestrator's Plan 10 verification gate. The gate command from `2026-05-24-00-orchestrator.md` is:

> `uv run pytest tests/skills/test_ralph_cancel.py tests/skills/test_ralph_promote.py tests/skills/test_ralph_triage.py -v` — All pass

Plan 10 satisfies the gate with the artifacts above. The verification has two parts: an artifact gate (no network, hermetic) and a manual rehearsal on a real `ralph-queue` clone.

### Part 1 — artifact gate (no network)

- [ ] 1. Run the per-skill test selection from the orchestrator gate verbatim:
  ```
  uv run pytest tests/skills/test_ralph_cancel.py tests/skills/test_ralph_promote.py tests/skills/test_ralph_triage.py -v
  ```
  Expected: twenty-three tests collected (7 from `ralph-cancel` + 8 from `ralph-promote` + 8 from `ralph-triage`), every test passing. Exit code 0.

- [ ] 2. Run the queue-writer unit tests in isolation to confirm the shared module passes on its own:
  ```
  uv run pytest tests/test_queue_writer.py -v
  ```
  Expected: thirteen tests pass. Exit code 0.

- [ ] 3. Run the cross-skill smoke test:
  ```
  uv run pytest tests/skills/test_supervisor_skills_smoke.py -v
  ```
  Expected: two tests pass.

- [ ] 4. Run the full repo gate to confirm Plan 10 didn't disturb Plans 1, 2, 3:
  ```
  uv run ruff check .
  uv run ruff format --check .
  uv run mypy ralph_executor scripts skills tests
  uv run pytest
  ```
  Expected: every command exits 0. Combined test count is 66 (8 + 10 + 10 + 13 + 7 + 8 + 8 + 2).

### Part 2 — manual rehearsal on a real `ralph-queue` clone (once per environment)

- [ ] 5. Pick a test repo where Plan 2's runbook has been executed (so `origin/ralph-queue` exists). Clone it locally and switch onto `ralph-queue`. Submit a small PBI via Plan 3's `ralph-add` if the queue is empty:
  ```
  uv run python skills/ralph-add/scripts/add.py \
      --work-item WI-<id> \
      --repo /path/to/checked-out/service-repo
  ```
  Expected: the PBI lands in `.ralph/inbox/WI-<id>/`.

- [ ] 6. Promote the PBI's severity:
  ```
  uv run python skills/ralph-promote/scripts/promote.py \
      --pbi-id WI-<id> \
      --severity high \
      --repo /path/to/checked-out/service-repo
  ```
  Expected: exit code 0; JSON summary shows `previous_severity: normal` and `new_severity: high`. Pull `ralph-queue` and inspect the PBI's entry file — `severity: high` and `updated_at` refreshed.

- [ ] 7. Move the PBI from `inbox/` to `current/` manually on the queue branch (simulating the executor's claim), then cancel:
  ```
  cd /path/to/checked-out/service-repo
  git checkout ralph-queue
  git mv .ralph/inbox/WI-<id> .ralph/current/WI-<id>
  git commit -m "chore(test): manual claim into current/"
  git push origin ralph-queue
  cd -

  uv run python skills/ralph-cancel/scripts/cancel.py \
      --pbi-id WI-<id> \
      --repo /path/to/checked-out/service-repo
  ```
  Expected: exit code 0; JSON summary shows `sentinel_path: .ralph/current/WI-<id>/CANCEL` and `pushed: true`. The remote `ralph-queue` now contains an empty `CANCEL` file at that path.

- [ ] 8. Move the PBI to `blocked/` manually (simulating the executor noticing the cancel) and triage it back to inbox with a note:
  ```
  cd /path/to/checked-out/service-repo
  git checkout ralph-queue
  git mv .ralph/current/WI-<id> .ralph/blocked/WI-<id>
  git commit -m "chore(test): manual move to blocked/"
  git push origin ralph-queue
  cd -

  uv run python skills/ralph-triage/scripts/triage.py \
      --pbi-id WI-<id> \
      --to inbox \
      --note "rehearsal: returning for retry" \
      --repo /path/to/checked-out/service-repo
  ```
  Expected: exit code 0; the PBI is now under `.ralph/inbox/WI-<id>/` on the remote, with `attempts: 0`, `status: inbox`, and a HISTORY.md entry containing the note.

- [ ] 9. Archive a different blocked PBI:
  ```
  uv run python skills/ralph-triage/scripts/triage.py \
      --pbi-id WI-<other-id> \
      --to archive \
      --note "rehearsal: closing out as obsolete" \
      --repo /path/to/checked-out/service-repo
  ```
  Expected: exit code 0; the PBI is now under `.ralph/archive/WI-<other-id>/` on the remote (the `archive` folder is created on demand if it didn't exist). HISTORY.md has the closing entry.

If all nine steps pass, Plan 10 is complete. The next plan in the dependency graph is Plan 13 (end-to-end smoke), which depends on Plan 10 and Plan 12.
