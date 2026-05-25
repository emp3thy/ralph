# `ralph-status` Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the read-only supervisor skill, `ralph-status`. Given one or more configured service repos (each holding a `ralph-queue` branch populated by `ralph-add`), the skill enumerates the PBI directories under `.ralph/{inbox,current,pending-pr,done,blocked}/`, parses their frontmatter, and renders a terminal-friendly table to stdout. Supports `--repo <path>` for a single repo, `--repos-file <path>` for a config listing several, an optional `--state` filter, and `--json` for machine-readable output. Reads `ralph-queue` from each configured repo without mutating any working tree — uses `git worktree add` against a temporary directory and tears it down on exit. Parses PBI frontmatter via a shared module `scripts/pbi_reader.py` that internalises (and re-exports) the canonical schema constants from Plan 1's `scripts.validate_samples`. Tests use the same local-bare-repo fixture pattern as Plan 3 (no network, hermetic) plus a few small fixtures that lay down sample PBI directories on `ralph-queue` and assert the rendered output.

**Architecture:** A Claude Code skill in `skills/ralph-status/` with one `SKILL.md` and one Python entry script `scripts/show.py`. The script is structured as composable functions: `_parse_args`, `_load_repos_config` (reads `--repo` or `--repos-file`), `_extract_queue_snapshot` (creates a temp git worktree on the queue branch, returns its path, plus a cleanup callable), `_enumerate_pbis` (walks the five state folders, reads each PBI's entry file, returns a list of typed `PBIRow` records), `_render_table` (renders rows as fixed-width columns), `_render_json` (renders the same rows as JSON), and `main` (orchestrator). All PBI-parsing logic lives in `scripts/pbi_reader.py` — a new shared module that (a) re-exports the Plan 1 schema constants, (b) detects the entry filename from the state-folder + directory layout, and (c) returns either a `PBIRow` or a `PBIRowError` for each directory it inspects. The reader is deliberately tolerant of malformed PBIs (no entry file, broken YAML, missing required fields) — those become `PBIRowError` rows surfaced as a `?` row in the table, never aborting the whole command. Tests use `tmp_path` plus an `_init_repo_with_pbis(...)` fixture to build a local bare + worktree pair populated with fake PBIs on `ralph-queue`, then drive `show.main(...)` and assert against captured stdout.

**Tech Stack:** Python 3.12+, `uv`, `pyyaml`, `pytest`, ruff, mypy strict, `git` (called via `subprocess`).

---

## File Structure

| Path | Responsibility |
|---|---|
| `skills/ralph-status/SKILL.md` | Skill frontmatter (`name: ralph-status`, `description: ...`) plus a body documenting inputs, env vars, output formats, and examples. Mirrors the structure of `skills/ralph-add/SKILL.md`. |
| `skills/ralph-status/scripts/__init__.py` | Empty package marker so mypy and ruff treat the directory as a package. |
| `skills/ralph-status/scripts/show.py` | The entry script. Argparse CLI, orchestrates checkout → enumerate → render, returns 0 on success, 2 on validation/IO error. |
| `scripts/pbi_reader.py` | Shared PBI-reading module. Defines `PBIRow`, `PBIRowError`, `STATE_FOLDERS`, `ENTRY_FILE_BY_TYPE` (re-exported from `scripts.validate_samples` where possible). Exposes `read_pbi(dir_path, state)` and `enumerate_state(repo_root, state)`. |
| `tests/skills/test_ralph_status.py` | Pytest tests covering: empty queue prints empty table, populated queue renders one row per PBI, `--state` filter narrows the rendered set, `--json` emits valid JSON, multiple-repo config aggregates across repos, malformed PBI becomes a `?` row without aborting, missing repo path exits non-zero, branch missing exits non-zero, `--no-cleanup` retains the worktree for debugging. |
| `tests/test_pbi_reader.py` | Unit tests for the shared reader: detects entry filename per type, parses valid frontmatter, returns `PBIRowError` for missing entry file, returns `PBIRowError` for invalid YAML, returns `PBIRowError` for missing required fields. |

---

## Task 1 — Wire `scripts/pbi_reader.py` and the skill scaffold

**Files**
- Create: `scripts/pbi_reader.py`
- Create: `tests/test_pbi_reader.py`
- Create: `skills/ralph-status/SKILL.md`
- Create: `skills/ralph-status/scripts/__init__.py`

**Steps**

- [ ] 1. Confirm Plans 1, 2, and 3 are merged. Run:
  ```
  uv run pytest tests/test_workspace_samples.py tests/test_ado_client.py tests/test_setup_ralph_queue.py tests/skills/test_ralph_add.py -v
  ```
  Expected: every test passes. If any fail, STOP — Plan 4 depends on `scripts.validate_samples` (Plan 1), the `ralph-queue` branch conventions (Plan 2), and the skill scaffolding pattern (Plan 3).

- [ ] 2. Create `skills/ralph-status/scripts/__init__.py` containing exactly:
  ```python
  """Empty package marker."""
  ```

- [ ] 3. Create `skills/ralph-status/SKILL.md` with the exact content below:
  ```markdown
  ---
  name: ralph-status
  description: Read-only view of Ralph's queue across one or more service repos. Enumerates the .ralph/{inbox,current,pending-pr,done,blocked} folders on each repo's ralph-queue branch, parses each PBI's frontmatter, and renders a terminal-friendly table to stdout. Supports filtering by state and JSON output for downstream automation.

  ---

  # ralph-status

  ## What this skill does

  The `ralph-status` skill is the BA / PM / triager workboard for Ralph. It
  reads (never writes) the queue state from one or more service repos and
  renders it as a terminal-friendly table. The data sources are the
  `.ralph/` directories on each repo's `ralph-queue` branch — exactly the
  same trees `ralph-add` writes to.

  For each configured repo, the skill:

  1. Creates a short-lived `git worktree` on the `ralph-queue` branch (or
     the branch named via `--branch`). The worktree lives in a temp
     directory and is torn down on exit so the user's working tree is
     untouched.
  2. Walks the five state folders: `inbox/`, `current/`, `pending-pr/`,
     `done/`, `blocked/`.
  3. For each PBI directory, reads its entry file (`PBI.md`, `BUG.md`,
     `FEEDBACK.md`) and parses the YAML frontmatter against the canonical
     schema defined in Plan 1.
  4. Renders one row per PBI to stdout, optionally filtered by `--state`
     and optionally serialised as JSON via `--json`.

  ## When to use it

  Use `ralph-status` to answer "what is Ralph doing right now?" or "what
  is queued on service X?". It replaces the kanban UI a v2 web supervisor
  would expose.

  ## Inputs

  | Flag | Required | Description |
  |---|---|---|
  | `--repo <path>` | one of `--repo` / `--repos-file` | Path to a checkout (or any working tree) of a service repo. The skill creates a worktree on `ralph-queue` from this repo. |
  | `--repos-file <path>` | one of `--repo` / `--repos-file` | Path to a config file listing multiple repos (one repo path per non-blank line; lines starting with `#` are ignored). |
  | `--state <state>` | no | Filter rows to a single state. Allowed values: `inbox`, `current`, `pending-pr`, `done`, `blocked`. Default: all five. |
  | `--branch <name>` | no | Queue branch name. Default: `ralph-queue` (also picked up from `RALPH_QUEUE_BRANCH` if set). |
  | `--json` | no | Emit JSON to stdout instead of a fixed-width table. |
  | `--no-cleanup` | no | Keep the temporary worktree(s) on disk after the command exits. Useful for debugging the data the skill saw. |

  ## Environment variables

  | Variable | Purpose |
  |---|---|
  | `RALPH_QUEUE_BRANCH` | Default queue branch name; overridden by `--branch`. Optional. |

  ## Output

  ### Table mode (default)

  ```
  REPO                STATE         ID            TYPE        SEVERITY  AGE      TITLE
  service-auth        inbox         WI-1234       feature     normal    2h       Add /healthz endpoint
  service-auth        current       WI-1235       bug         critical  1h       Pod crashloops on ROSA
  service-billing     pending-pr    WI-980        feature     high      6h       Migrate invoices to v2
  ```

  Columns are width-aligned to the widest value in the rendered set. The
  `AGE` column shows the time since `created_at` (`m` minutes, `h` hours,
  `d` days; `?` if `created_at` is missing or unparseable).

  Malformed PBIs (missing entry file, invalid YAML, missing required
  fields) render as a `?` row with the directory name in the ID column
  and the parse error in the TITLE column — they never abort the command.

  ### JSON mode (`--json`)

  ```json
  {
    "rows": [
      {
        "repo": "/path/to/service-auth",
        "repo_name": "service-auth",
        "state": "inbox",
        "id": "WI-1234",
        "type": "feature",
        "severity": "normal",
        "created_at": "2026-05-24T09:15:00+00:00",
        "updated_at": "2026-05-24T09:15:00+00:00",
        "attempts": 0,
        "title": "Add /healthz endpoint",
        "pbi_dir": ".ralph/inbox/WI-1234",
        "error": null
      }
    ],
    "errors": [],
    "repos": [
      {"path": "/path/to/service-auth", "name": "service-auth", "branch": "ralph-queue"}
    ]
  }
  ```

  Malformed PBIs appear in `rows` with `error` set to a string describing
  the parse failure. Repos that could not be inspected at all (path
  missing, branch missing) appear in the top-level `errors` array and
  cause exit code 2 — they are not soft-failed like individual PBIs.

  ## How it is invoked

  ```bash
  uv run python skills/ralph-status/scripts/show.py --repo /path/to/service-auth
  uv run python skills/ralph-status/scripts/show.py --repos-file ~/.config/ralph/repos
  uv run python skills/ralph-status/scripts/show.py --repo /path/to/svc --state current
  uv run python skills/ralph-status/scripts/show.py --repo /path/to/svc --json
  ```

  Tests live at `tests/skills/test_ralph_status.py` and use local bare +
  worktree git fixtures (the same pattern as the `ralph-add` skill's
  tests) — no network, no real ADO, no real remote.

  ## What this skill does NOT do

  - It does not mutate `ralph-queue`, the service repo's working tree,
    or the user's current branch. The temporary worktree is removed on
    exit unless `--no-cleanup` is set.
  - It does not call ADO or any other remote API. It only reads git.
  - It does not invoke `claude -p` or otherwise touch the executor.
  - It does not aggregate PR status (CI green / red, reviewer decisions).
    PR state lives elsewhere; the queue folder a PBI sits in is the
    truth surface this skill reads.
  ```

- [ ] 4. Create `scripts/pbi_reader.py` with the exact content below. The module is deliberately small: it re-uses Plan 1's schema constants where possible and adds two thin layers (`PBIRow` / `PBIRowError` and `enumerate_state`):
  ```python
  """Shared PBI directory reader used by ``ralph-status`` (and reusable).

  The canonical PBI schema lives in ``scripts.validate_samples``; this
  module imports those constants rather than redefining them, so any
  future schema change made in Plan 1 propagates here without edits.

  The reader is deliberately tolerant: malformed PBI directories are
  surfaced as ``PBIRowError`` records rather than raising. Callers
  (e.g. ``skills/ralph-status/scripts/show.py``) decide whether to
  display, log, or ignore the errors.
  """
  from __future__ import annotations

  from collections.abc import Mapping
  from dataclasses import dataclass
  from datetime import datetime
  from pathlib import Path
  from typing import Any

  import yaml

  from scripts.validate_samples import (
      ALLOWED_SEVERITIES,
      ALLOWED_STATUSES,
      ALLOWED_TYPES,
      ENTRY_FILE_BY_TYPE,
      REQUIRED_FRONTMATTER_FIELDS,
      _split_frontmatter,
  )

  STATE_FOLDERS: tuple[str, ...] = (
      "inbox",
      "current",
      "pending-pr",
      "done",
      "blocked",
  )

  # Entry-file detection order when the directory is on the queue (not in
  # ``samples/``). On the queue the directory name is the canonical
  # ``WI-<n>`` form, so we cannot infer type from the directory name —
  # instead we try each entry filename in priority order and pick the
  # first one that exists.
  _ENTRY_FILE_PROBE_ORDER: tuple[str, ...] = ("PBI.md", "BUG.md", "FEEDBACK.md")


  @dataclass(frozen=True)
  class PBIRow:
      """A successfully-parsed PBI from a queue folder."""

      repo_path: Path
      repo_name: str
      state: str
      pbi_dir: Path
      pbi_id: str
      pbi_type: str
      severity: str
      attempts: int
      created_at: datetime | None
      updated_at: datetime | None
      title: str

      def relative_pbi_dir(self) -> str:
          try:
              return str(self.pbi_dir.relative_to(self.repo_path)).replace("\\", "/")
          except ValueError:
              return str(self.pbi_dir).replace("\\", "/")


  @dataclass(frozen=True)
  class PBIRowError:
      """A PBI directory we could not parse."""

      repo_path: Path
      repo_name: str
      state: str
      pbi_dir: Path
      message: str

      def relative_pbi_dir(self) -> str:
          try:
              return str(self.pbi_dir.relative_to(self.repo_path)).replace("\\", "/")
          except ValueError:
              return str(self.pbi_dir).replace("\\", "/")


  def _coerce_datetime(value: Any) -> datetime | None:
      if value is None:
          return None
      if isinstance(value, datetime):
          return value
      if isinstance(value, str):
          try:
              return datetime.fromisoformat(value)
          except ValueError:
              return None
      return None


  def _extract_title(body: str, fallback: str) -> str:
      for line in body.splitlines():
          stripped = line.strip()
          if stripped.startswith("# "):
              return stripped[2:].strip() or fallback
      return fallback


  def _detect_entry_file(pbi_dir: Path) -> Path | None:
      for name in _ENTRY_FILE_PROBE_ORDER:
          candidate = pbi_dir / name
          if candidate.is_file():
              return candidate
      return None


  def read_pbi(
      pbi_dir: Path,
      *,
      repo_path: Path,
      repo_name: str,
      state: str,
  ) -> PBIRow | PBIRowError:
      """Read a single PBI directory; tolerant of malformed input."""
      if not pbi_dir.is_dir():
          return PBIRowError(
              repo_path=repo_path,
              repo_name=repo_name,
              state=state,
              pbi_dir=pbi_dir,
              message=f"not a directory: {pbi_dir}",
          )

      entry_file = _detect_entry_file(pbi_dir)
      if entry_file is None:
          return PBIRowError(
              repo_path=repo_path,
              repo_name=repo_name,
              state=state,
              pbi_dir=pbi_dir,
              message=(
                  "no entry file (expected one of "
                  f"{list(_ENTRY_FILE_PROBE_ORDER)})"
              ),
          )

      try:
          text = entry_file.read_text(encoding="utf-8")
      except OSError as exc:
          return PBIRowError(
              repo_path=repo_path,
              repo_name=repo_name,
              state=state,
              pbi_dir=pbi_dir,
              message=f"failed to read {entry_file.name}: {exc}",
          )

      split = _split_frontmatter(text)
      if split is None:
          return PBIRowError(
              repo_path=repo_path,
              repo_name=repo_name,
              state=state,
              pbi_dir=pbi_dir,
              message=(
                  f"{entry_file.name} has no leading YAML frontmatter "
                  "block"
              ),
          )

      frontmatter_yaml, body = split
      try:
          parsed: Any = yaml.safe_load(frontmatter_yaml)
      except yaml.YAMLError as exc:
          return PBIRowError(
              repo_path=repo_path,
              repo_name=repo_name,
              state=state,
              pbi_dir=pbi_dir,
              message=f"frontmatter YAML is invalid: {exc}",
          )

      if not isinstance(parsed, Mapping):
          return PBIRowError(
              repo_path=repo_path,
              repo_name=repo_name,
              state=state,
              pbi_dir=pbi_dir,
              message=(
                  "frontmatter is not a YAML mapping "
                  f"(got {type(parsed).__name__})"
              ),
          )

      missing = [f for f in REQUIRED_FRONTMATTER_FIELDS if f not in parsed]
      if missing:
          return PBIRowError(
              repo_path=repo_path,
              repo_name=repo_name,
              state=state,
              pbi_dir=pbi_dir,
              message=f"frontmatter missing required fields: {sorted(missing)}",
          )

      pbi_type = parsed.get("type")
      if pbi_type not in ALLOWED_TYPES:
          return PBIRowError(
              repo_path=repo_path,
              repo_name=repo_name,
              state=state,
              pbi_dir=pbi_dir,
              message=(
                  f"type={pbi_type!r} not in allowed set "
                  f"{sorted(ALLOWED_TYPES)}"
              ),
          )

      severity = parsed.get("severity")
      if severity not in ALLOWED_SEVERITIES:
          return PBIRowError(
              repo_path=repo_path,
              repo_name=repo_name,
              state=state,
              pbi_dir=pbi_dir,
              message=(
                  f"severity={severity!r} not in allowed set "
                  f"{sorted(ALLOWED_SEVERITIES)}"
              ),
          )

      status_value = parsed.get("status")
      if status_value is not None and status_value not in ALLOWED_STATUSES:
          # Non-fatal — surface as parse error so the user notices, but
          # still allow the rest of the parse to succeed via the row.
          return PBIRowError(
              repo_path=repo_path,
              repo_name=repo_name,
              state=state,
              pbi_dir=pbi_dir,
              message=(
                  f"status={status_value!r} not in allowed set "
                  f"{sorted(ALLOWED_STATUSES)}"
              ),
          )

      attempts_raw = parsed.get("attempts")
      attempts = attempts_raw if isinstance(attempts_raw, int) else 0

      pbi_id_raw = parsed.get("id")
      pbi_id = (
          pbi_id_raw
          if isinstance(pbi_id_raw, str) and pbi_id_raw.strip()
          else pbi_dir.name
      )

      return PBIRow(
          repo_path=repo_path,
          repo_name=repo_name,
          state=state,
          pbi_dir=pbi_dir,
          pbi_id=pbi_id,
          pbi_type=str(pbi_type),
          severity=str(severity),
          attempts=attempts,
          created_at=_coerce_datetime(parsed.get("created_at")),
          updated_at=_coerce_datetime(parsed.get("updated_at")),
          title=_extract_title(body, fallback=pbi_id),
      )


  def enumerate_state(
      repo_root: Path,
      state: str,
      *,
      repo_name: str | None = None,
  ) -> list[PBIRow | PBIRowError]:
      """Walk ``.ralph/<state>/`` under ``repo_root`` and read every PBI.

      Returns an empty list when the state folder does not exist (which
      is a normal condition — a brand-new queue may not have created
      `done/` yet).
      """
      if state not in STATE_FOLDERS:
          raise ValueError(
              f"unknown state {state!r}; expected one of {STATE_FOLDERS}"
          )
      state_dir = repo_root / ".ralph" / state
      if not state_dir.is_dir():
          return []
      effective_repo_name = repo_name or repo_root.name
      rows: list[PBIRow | PBIRowError] = []
      for child in sorted(state_dir.iterdir()):
          if not child.is_dir():
              continue
          rows.append(
              read_pbi(
                  child,
                  repo_path=repo_root,
                  repo_name=effective_repo_name,
                  state=state,
              )
          )
      return rows


  __all__ = [
      "ENTRY_FILE_BY_TYPE",
      "PBIRow",
      "PBIRowError",
      "STATE_FOLDERS",
      "enumerate_state",
      "read_pbi",
  ]
  ```

- [ ] 5. Create `tests/test_pbi_reader.py` with the exact content below. The tests are pure-Python — no git fixtures — they only verify the parser:
  ```python
  """Unit tests for ``scripts.pbi_reader``.

  Construct PBI directories directly in ``tmp_path``; no git involvement.
  """
  from __future__ import annotations

  from pathlib import Path

  import pytest

  from scripts.pbi_reader import (
      PBIRow,
      PBIRowError,
      enumerate_state,
      read_pbi,
  )

  VALID_FEATURE_FRONTMATTER = """---
  id: WI-1234
  type: feature
  status: inbox
  severity: normal
  attempts: 0
  created_at: 2026-05-24T09:15:00+00:00
  updated_at: 2026-05-24T09:15:00+00:00
  ---

  # Add /healthz endpoint

  Body content here.
  """

  VALID_BUG_FRONTMATTER = """---
  id: BUG-1
  type: bug
  status: inbox
  severity: critical
  attempts: 0
  created_at: 2026-05-24T09:15:00+00:00
  updated_at: 2026-05-24T09:15:00+00:00
  ---

  # Pod crashloops on ROSA

  Body content here.
  """

  VALID_FEEDBACK_FRONTMATTER = """---
  id: PR-feedback-WI-1234-r2
  type: pr-feedback
  status: inbox
  severity: high
  attempts: 0
  created_at: 2026-05-24T09:15:00+00:00
  updated_at: 2026-05-24T09:15:00+00:00
  ---

  # PR feedback round 2

  Body.
  """


  def _make_pbi(
      base: Path, name: str, entry_file: str, content: str
  ) -> Path:
      pbi_dir = base / name
      pbi_dir.mkdir(parents=True)
      (pbi_dir / entry_file).write_text(content, encoding="utf-8")
      return pbi_dir


  def test_read_pbi_feature(tmp_path: Path) -> None:
      pbi_dir = _make_pbi(tmp_path, "WI-1234", "PBI.md", VALID_FEATURE_FRONTMATTER)
      result = read_pbi(
          pbi_dir, repo_path=tmp_path, repo_name="svc", state="inbox"
      )
      assert isinstance(result, PBIRow)
      assert result.pbi_id == "WI-1234"
      assert result.pbi_type == "feature"
      assert result.severity == "normal"
      assert result.title == "Add /healthz endpoint"
      assert result.state == "inbox"


  def test_read_pbi_bug(tmp_path: Path) -> None:
      pbi_dir = _make_pbi(tmp_path, "BUG-1", "BUG.md", VALID_BUG_FRONTMATTER)
      result = read_pbi(
          pbi_dir, repo_path=tmp_path, repo_name="svc", state="current"
      )
      assert isinstance(result, PBIRow)
      assert result.pbi_type == "bug"
      assert result.severity == "critical"
      assert result.title == "Pod crashloops on ROSA"


  def test_read_pbi_feedback(tmp_path: Path) -> None:
      pbi_dir = _make_pbi(
          tmp_path,
          "PR-feedback-WI-1234-r2",
          "FEEDBACK.md",
          VALID_FEEDBACK_FRONTMATTER,
      )
      result = read_pbi(
          pbi_dir, repo_path=tmp_path, repo_name="svc", state="inbox"
      )
      assert isinstance(result, PBIRow)
      assert result.pbi_type == "pr-feedback"


  def test_read_pbi_missing_entry_file(tmp_path: Path) -> None:
      pbi_dir = tmp_path / "WI-9999"
      pbi_dir.mkdir()
      result = read_pbi(
          pbi_dir, repo_path=tmp_path, repo_name="svc", state="inbox"
      )
      assert isinstance(result, PBIRowError)
      assert "no entry file" in result.message


  def test_read_pbi_invalid_yaml(tmp_path: Path) -> None:
      bad = """---
  id: WI-1
  type: feature
  status: inbox
  severity: normal
  attempts: 0
  created_at: 2026-05-24T09:15:00+00:00
  updated_at: 2026-05-24T09:15:00+00:00
    not-properly-indented: [unclosed
  ---

  # Title
  """
      pbi_dir = _make_pbi(tmp_path, "WI-1", "PBI.md", bad)
      result = read_pbi(
          pbi_dir, repo_path=tmp_path, repo_name="svc", state="inbox"
      )
      assert isinstance(result, PBIRowError)
      assert "YAML" in result.message or "yaml" in result.message


  def test_read_pbi_missing_required_field(tmp_path: Path) -> None:
      missing = """---
  id: WI-1
  type: feature
  status: inbox
  severity: normal
  # attempts intentionally omitted
  created_at: 2026-05-24T09:15:00+00:00
  updated_at: 2026-05-24T09:15:00+00:00
  ---

  # Title
  """
      pbi_dir = _make_pbi(tmp_path, "WI-1", "PBI.md", missing)
      result = read_pbi(
          pbi_dir, repo_path=tmp_path, repo_name="svc", state="inbox"
      )
      assert isinstance(result, PBIRowError)
      assert "attempts" in result.message


  def test_read_pbi_bad_type_value(tmp_path: Path) -> None:
      bad_type = """---
  id: WI-1
  type: feechore
  status: inbox
  severity: normal
  attempts: 0
  created_at: 2026-05-24T09:15:00+00:00
  updated_at: 2026-05-24T09:15:00+00:00
  ---

  # Title
  """
      pbi_dir = _make_pbi(tmp_path, "WI-1", "PBI.md", bad_type)
      result = read_pbi(
          pbi_dir, repo_path=tmp_path, repo_name="svc", state="inbox"
      )
      assert isinstance(result, PBIRowError)
      assert "type=" in result.message


  def test_enumerate_state_missing_folder_is_empty(tmp_path: Path) -> None:
      assert enumerate_state(tmp_path, "done") == []


  def test_enumerate_state_walks_pbis(tmp_path: Path) -> None:
      inbox = tmp_path / ".ralph" / "inbox"
      inbox.mkdir(parents=True)
      _make_pbi(inbox, "WI-1", "PBI.md", VALID_FEATURE_FRONTMATTER)
      _make_pbi(inbox, "BUG-1", "BUG.md", VALID_BUG_FRONTMATTER)
      rows = enumerate_state(tmp_path, "inbox", repo_name="svc")
      assert len(rows) == 2
      ids = sorted(
          row.pbi_id if isinstance(row, PBIRow) else "?" for row in rows
      )
      assert ids == ["BUG-1", "WI-1234"]
      # Both successfully parsed.
      assert all(isinstance(row, PBIRow) for row in rows)


  def test_enumerate_state_unknown_state_raises(tmp_path: Path) -> None:
      with pytest.raises(ValueError):
          enumerate_state(tmp_path, "limbo")
  ```

- [ ] 6. Run the pbi-reader tests; they must fail because `scripts.pbi_reader` does not yet exist (the file from step 4 is the implementation — both come in together because the reader has no external network/git surface and is the easiest part of Plan 4 to land first).
  ```
  uv run pytest tests/test_pbi_reader.py -v
  ```
  Expected (after step 4 + step 5 are written): all eleven tests pass. If any fail, fix in `scripts/pbi_reader.py` and re-run.

- [ ] 7. Run ruff and mypy against the new files:
  ```
  uv run ruff check scripts/pbi_reader.py tests/test_pbi_reader.py skills/ralph-status/SKILL.md skills/ralph-status/scripts/__init__.py
  uv run mypy scripts/pbi_reader.py tests/test_pbi_reader.py skills/ralph-status/scripts/__init__.py
  ```
  Expected: ruff: `All checks passed!` (ruff skips `SKILL.md`). Mypy: `Success: no issues found in 3 source files`.

- [ ] 8. Stage and commit:
  ```
  git add scripts/pbi_reader.py tests/test_pbi_reader.py skills/ralph-status/SKILL.md skills/ralph-status/scripts/__init__.py
  git commit -m "feat(pbi-reader): add shared queue-folder PBI reader and ralph-status scaffold"
  ```
  Expected: commit succeeds.

---

## Task 2 — Write the failing test for the `ralph-status` script

**Files**
- Create: `tests/skills/test_ralph_status.py`

**Steps**

- [ ] 1. Write `tests/skills/test_ralph_status.py` with the exact content below. The test uses `importlib.util.spec_from_file_location` to load the script (same pattern as Plan 3 — the skill directory contains a hyphen and is not a valid Python identifier). The `git_repo_with_pbis` fixture creates a local bare repo plus a working clone with a populated `ralph-queue` branch; the skill's worktree-based reader is exercised against that bare repo:
  ```python
  """Tests for the ``ralph-status`` skill's entry script.

  All git operations are local (bare repo + worktree clones in
  ``tmp_path``). No network, no real ADO, no real remote.
  """
  from __future__ import annotations

  import importlib.util
  import json
  import os
  import subprocess
  import textwrap
  from collections.abc import Iterator
  from pathlib import Path
  from types import ModuleType

  import pytest


  REPO_ROOT = Path(__file__).resolve().parents[2]
  SCRIPT_PATH = REPO_ROOT / "skills" / "ralph-status" / "scripts" / "show.py"

  WI_FEATURE = "WI-1234"
  WI_BUG = "BUG-rosa-irsa"
  WI_DONE = "WI-2000"
  WI_BLOCKED = "WI-3000"
  WI_CURRENT = "WI-1235"

  FEATURE_PBI_MD = textwrap.dedent("""\
      ---
      id: WI-1234
      type: feature
      status: inbox
      severity: normal
      attempts: 0
      created_at: 2026-05-24T09:15:00+00:00
      updated_at: 2026-05-24T09:15:00+00:00
      ---

      # Add /healthz endpoint to service-auth

      Body.
      """)

  BUG_PBI_MD = textwrap.dedent("""\
      ---
      id: BUG-rosa-irsa
      type: bug
      status: inbox
      severity: critical
      attempts: 0
      created_at: 2026-05-24T08:00:00+00:00
      updated_at: 2026-05-24T08:00:00+00:00
      ---

      # Pod crashloops on ROSA via IRSA

      Body.
      """)

  CURRENT_PBI_MD = textwrap.dedent("""\
      ---
      id: WI-1235
      type: feature
      status: current
      severity: high
      attempts: 1
      created_at: 2026-05-23T12:00:00+00:00
      updated_at: 2026-05-24T08:00:00+00:00
      ---

      # Onboarding flow refactor

      Body.
      """)

  DONE_PBI_MD = textwrap.dedent("""\
      ---
      id: WI-2000
      type: feature
      status: done
      severity: low
      attempts: 1
      created_at: 2026-05-20T09:15:00+00:00
      updated_at: 2026-05-22T09:15:00+00:00
      ---

      # Old completed work

      Body.
      """)

  BLOCKED_PBI_MD = textwrap.dedent("""\
      ---
      id: WI-3000
      type: feature
      status: blocked
      severity: normal
      attempts: 3
      created_at: 2026-05-22T09:15:00+00:00
      updated_at: 2026-05-23T09:15:00+00:00
      ---

      # Stuck PBI

      Body.
      """)

  MALFORMED_PBI_MD = "this file has no frontmatter at all\n"


  @pytest.fixture(scope="module")
  def show_module() -> ModuleType:
      """Load ``skills/ralph-status/scripts/show.py`` as an importable module."""
      assert SCRIPT_PATH.is_file(), f"missing entry script at {SCRIPT_PATH}"
      spec = importlib.util.spec_from_file_location(
          "ralph_status_script", SCRIPT_PATH
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


  def _write_pbi(
      work: Path, state: str, pbi_id: str, entry_file: str, content: str
  ) -> None:
      pbi_dir = work / ".ralph" / state / pbi_id
      pbi_dir.mkdir(parents=True, exist_ok=True)
      (pbi_dir / entry_file).write_text(content, encoding="utf-8")
      (pbi_dir / "HISTORY.md").write_text("", encoding="utf-8")


  def _init_repo_with_pbis(tmp_path: Path, name: str) -> Path:
      """Return a path to a bare repo whose ``ralph-queue`` branch holds PBIs.

      The bare repo is the "remote" — the skill creates a worktree against
      it via the working clone passed as ``--repo``.
      """
      bare = tmp_path / f"{name}.git"
      work = tmp_path / f"{name}-work"
      subprocess.run(["git", "init", "--bare", str(bare)], check=True)
      subprocess.run(["git", "init", str(work)], check=True)
      _git(work, "config", "user.email", "test@example.com")
      _git(work, "config", "user.name", "Test User")
      _git(work, "commit", "--allow-empty", "-m", "chore: initial main")
      _git(work, "branch", "-M", "main")
      _git(work, "remote", "add", "origin", str(bare))
      _git(work, "push", "-u", "origin", "main")
      _git(work, "checkout", "-b", "ralph-queue")

      _write_pbi(work, "inbox", WI_FEATURE, "PBI.md", FEATURE_PBI_MD)
      _write_pbi(work, "inbox", WI_BUG, "BUG.md", BUG_PBI_MD)
      _write_pbi(work, "current", WI_CURRENT, "PBI.md", CURRENT_PBI_MD)
      _write_pbi(work, "done", WI_DONE, "PBI.md", DONE_PBI_MD)
      _write_pbi(work, "blocked", WI_BLOCKED, "PBI.md", BLOCKED_PBI_MD)

      _git(work, "add", ".ralph")
      _git(work, "commit", "-m", "feat(queue): seed PBIs for tests")
      _git(work, "push", "-u", "origin", "ralph-queue")
      _git(work, "checkout", "main")
      return work


  @pytest.fixture
  def git_repo_with_pbis(tmp_path: Path) -> Path:
      return _init_repo_with_pbis(tmp_path, "service-auth")


  @pytest.fixture
  def empty_repo(tmp_path: Path) -> Path:
      """Bare + working repo with a ralph-queue branch but no PBIs."""
      bare = tmp_path / "empty.git"
      work = tmp_path / "empty-work"
      subprocess.run(["git", "init", "--bare", str(bare)], check=True)
      subprocess.run(["git", "init", str(work)], check=True)
      _git(work, "config", "user.email", "test@example.com")
      _git(work, "config", "user.name", "Test User")
      _git(work, "commit", "--allow-empty", "-m", "chore: initial main")
      _git(work, "branch", "-M", "main")
      _git(work, "remote", "add", "origin", str(bare))
      _git(work, "push", "-u", "origin", "main")
      _git(work, "checkout", "-b", "ralph-queue")
      _git(work, "commit", "--allow-empty", "-m", "chore(queue): bootstrap")
      _git(work, "push", "-u", "origin", "ralph-queue")
      _git(work, "checkout", "main")
      return work


  # ----------------------------------------------------------------------
  # Tests
  # ----------------------------------------------------------------------

  def test_single_repo_renders_all_states(
      git_repo_with_pbis: Path,
      show_module: ModuleType,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      exit_code = show_module.main(["--repo", str(git_repo_with_pbis)])
      assert exit_code == 0
      stdout = capsys.readouterr().out
      # Header row present
      assert "REPO" in stdout
      assert "STATE" in stdout
      assert "ID" in stdout
      # All five PBIs appear
      for pbi_id in (WI_FEATURE, WI_BUG, WI_CURRENT, WI_DONE, WI_BLOCKED):
          assert pbi_id in stdout, f"missing {pbi_id} in:\n{stdout}"
      # State labels appear
      assert "inbox" in stdout
      assert "current" in stdout
      assert "done" in stdout
      assert "blocked" in stdout


  def test_state_filter_narrows_output(
      git_repo_with_pbis: Path,
      show_module: ModuleType,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      exit_code = show_module.main(
          ["--repo", str(git_repo_with_pbis), "--state", "current"]
      )
      assert exit_code == 0
      stdout = capsys.readouterr().out
      assert WI_CURRENT in stdout
      # Other PBIs NOT in output
      for pbi_id in (WI_FEATURE, WI_BUG, WI_DONE, WI_BLOCKED):
          assert pbi_id not in stdout, (
              f"unexpected {pbi_id} in filtered output:\n{stdout}"
          )


  def test_json_output_is_valid(
      git_repo_with_pbis: Path,
      show_module: ModuleType,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      exit_code = show_module.main(
          ["--repo", str(git_repo_with_pbis), "--json"]
      )
      assert exit_code == 0
      payload = json.loads(capsys.readouterr().out)
      assert "rows" in payload
      assert "errors" in payload
      assert "repos" in payload
      assert isinstance(payload["rows"], list)
      assert len(payload["rows"]) == 5  # one per seeded PBI
      ids = sorted(row["id"] for row in payload["rows"])
      assert WI_FEATURE in ids
      assert WI_BUG in ids
      # Schema check on one row
      sample = payload["rows"][0]
      for key in (
          "repo",
          "repo_name",
          "state",
          "id",
          "type",
          "severity",
          "title",
          "attempts",
          "pbi_dir",
          "error",
      ):
          assert key in sample, f"row missing key {key!r}: {sample}"


  def test_empty_queue_renders_header_only(
      empty_repo: Path,
      show_module: ModuleType,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      exit_code = show_module.main(["--repo", str(empty_repo)])
      assert exit_code == 0
      stdout = capsys.readouterr().out
      assert "REPO" in stdout
      # No PBI ids should appear
      assert "WI-" not in stdout
      assert "BUG-" not in stdout


  def test_repos_file_aggregates_across_repos(
      tmp_path: Path,
      git_repo_with_pbis: Path,
      show_module: ModuleType,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      second = _init_repo_with_pbis(tmp_path, "service-billing")
      cfg = tmp_path / "repos.txt"
      cfg.write_text(
          f"# A comment\n{git_repo_with_pbis}\n{second}\n",
          encoding="utf-8",
      )
      exit_code = show_module.main(
          ["--repos-file", str(cfg), "--json"]
      )
      assert exit_code == 0
      payload = json.loads(capsys.readouterr().out)
      assert len(payload["repos"]) == 2
      repo_names = {repo["name"] for repo in payload["repos"]}
      assert repo_names == {"service-auth-work", "service-billing-work"}
      # 5 PBIs per repo
      assert len(payload["rows"]) == 10


  def test_malformed_pbi_becomes_question_row(
      tmp_path: Path,
      show_module: ModuleType,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      # Build a repo with one good PBI and one malformed PBI.
      bare = tmp_path / "svc.git"
      work = tmp_path / "svc-work"
      subprocess.run(["git", "init", "--bare", str(bare)], check=True)
      subprocess.run(["git", "init", str(work)], check=True)
      _git(work, "config", "user.email", "test@example.com")
      _git(work, "config", "user.name", "Test User")
      _git(work, "commit", "--allow-empty", "-m", "chore: initial main")
      _git(work, "branch", "-M", "main")
      _git(work, "remote", "add", "origin", str(bare))
      _git(work, "push", "-u", "origin", "main")
      _git(work, "checkout", "-b", "ralph-queue")
      _write_pbi(work, "inbox", WI_FEATURE, "PBI.md", FEATURE_PBI_MD)
      _write_pbi(work, "inbox", "WI-broken", "PBI.md", MALFORMED_PBI_MD)
      _git(work, "add", ".ralph")
      _git(work, "commit", "-m", "feat(queue): seed mixed PBIs")
      _git(work, "push", "-u", "origin", "ralph-queue")
      _git(work, "checkout", "main")

      exit_code = show_module.main(["--repo", str(work), "--json"])
      assert exit_code == 0, "malformed PBIs must not abort the whole command"
      payload = json.loads(capsys.readouterr().out)
      ids = {row["id"] for row in payload["rows"]}
      assert WI_FEATURE in ids
      assert "WI-broken" in ids
      broken = next(row for row in payload["rows"] if row["id"] == "WI-broken")
      assert broken["error"] is not None
      assert broken["type"] == "?" or broken["type"] is None


  def test_repo_path_must_exist(
      tmp_path: Path,
      show_module: ModuleType,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      missing = tmp_path / "does-not-exist"
      exit_code = show_module.main(["--repo", str(missing)])
      assert exit_code == 2
      err = capsys.readouterr().err
      assert "does not exist" in err or "not a git" in err


  def test_branch_missing_exits_nonzero(
      tmp_path: Path,
      show_module: ModuleType,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      # Bare + working repo, but NO ralph-queue branch.
      bare = tmp_path / "no-queue.git"
      work = tmp_path / "no-queue-work"
      subprocess.run(["git", "init", "--bare", str(bare)], check=True)
      subprocess.run(["git", "init", str(work)], check=True)
      _git(work, "config", "user.email", "test@example.com")
      _git(work, "config", "user.name", "Test User")
      _git(work, "commit", "--allow-empty", "-m", "chore: initial main")
      _git(work, "branch", "-M", "main")
      _git(work, "remote", "add", "origin", str(bare))
      _git(work, "push", "-u", "origin", "main")

      exit_code = show_module.main(["--repo", str(work)])
      assert exit_code == 2
      err = capsys.readouterr().err
      assert "ralph-queue" in err


  def test_no_cleanup_keeps_worktree(
      git_repo_with_pbis: Path,
      show_module: ModuleType,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      exit_code = show_module.main(
          ["--repo", str(git_repo_with_pbis), "--json", "--no-cleanup"]
      )
      assert exit_code == 0
      payload = json.loads(capsys.readouterr().out)
      assert len(payload["repos"]) == 1
      worktree_path = Path(payload["repos"][0]["worktree_path"])
      assert worktree_path.is_dir(), (
          "--no-cleanup must leave the worktree on disk"
      )
      assert (worktree_path / ".ralph").is_dir()


  def test_state_filter_rejects_unknown_state(
      git_repo_with_pbis: Path,
      show_module: ModuleType,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      with pytest.raises(SystemExit) as excinfo:
          show_module.main(
              ["--repo", str(git_repo_with_pbis), "--state", "limbo"]
          )
      assert excinfo.value.code == 2  # argparse error
  ```

- [ ] 2. Run the new tests; they must fail because the script does not yet exist:
  ```
  uv run pytest tests/skills/test_ralph_status.py -v
  ```
  Expected: the fixture-loading step raises `AssertionError: missing entry script at <SCRIPT_PATH>` because `skills/ralph-status/scripts/show.py` is absent. This is the red step.

- [ ] 3. Do NOT commit yet — the failing test is consumed in Task 3.

---

## Task 3 — Implement `skills/ralph-status/scripts/show.py`

**Files**
- Create: `skills/ralph-status/scripts/show.py`

**Steps**

- [ ] 1. Write `skills/ralph-status/scripts/show.py` with the exact content below. The script reuses Plan 1's repo-root import trick (insert the ralph repo root onto `sys.path` so `scripts.pbi_reader` resolves whether the script is invoked via `uv run python skills/ralph-status/scripts/show.py` or imported by tests via `importlib`):
  ```python
  """``ralph-status`` skill entry point.

  Render a read-only view of one or more service repos' ralph-queue
  state. Uses a temporary ``git worktree`` per repo so the user's
  working tree is untouched.
  """
  from __future__ import annotations

  import argparse
  import json
  import os
  import shutil
  import subprocess
  import sys
  import tempfile
  from collections.abc import Iterable
  from dataclasses import dataclass, field
  from datetime import datetime, timezone
  from pathlib import Path

  # Make ``scripts.pbi_reader`` importable when this script is invoked
  # directly via ``uv run python skills/ralph-status/scripts/show.py``.
  _REPO_ROOT = Path(__file__).resolve().parents[3]
  if str(_REPO_ROOT) not in sys.path:
      sys.path.insert(0, str(_REPO_ROOT))

  from scripts.pbi_reader import (  # noqa: E402
      PBIRow,
      PBIRowError,
      STATE_FOLDERS,
      enumerate_state,
  )


  DEFAULT_QUEUE_BRANCH = "ralph-queue"


  # ----------------------------------------------------------------------
  # Data shapes for output
  # ----------------------------------------------------------------------

  @dataclass
  class RepoConfig:
      path: Path
      name: str
      branch: str


  @dataclass
  class RepoSnapshot:
      config: RepoConfig
      worktree_path: Path
      rows: list[PBIRow | PBIRowError] = field(default_factory=list)


  class _FatalError(RuntimeError):
      """Raised internally to signal a clean exit with code 2."""


  # ----------------------------------------------------------------------
  # Argument parsing
  # ----------------------------------------------------------------------

  def _parse_args(argv: list[str]) -> argparse.Namespace:
      parser = argparse.ArgumentParser(
          prog="ralph-status",
          description=(
              "Read-only view of Ralph's queue across one or more service "
              "repos. Creates a short-lived git worktree per repo to read "
              "the ralph-queue branch without disturbing the user's "
              "working tree."
          ),
      )
      group = parser.add_mutually_exclusive_group(required=True)
      group.add_argument(
          "--repo",
          help="Path to a service repo to inspect.",
      )
      group.add_argument(
          "--repos-file",
          help=(
              "Path to a config file listing service repos to inspect "
              "(one path per non-blank, non-comment line)."
          ),
      )
      parser.add_argument(
          "--state",
          choices=STATE_FOLDERS,
          help="Filter rows to a single state.",
      )
      parser.add_argument(
          "--branch",
          default=os.environ.get("RALPH_QUEUE_BRANCH", DEFAULT_QUEUE_BRANCH),
          help=f"Queue branch name (default: {DEFAULT_QUEUE_BRANCH}).",
      )
      parser.add_argument(
          "--json",
          dest="emit_json",
          action="store_true",
          help="Emit JSON to stdout instead of a fixed-width table.",
      )
      parser.add_argument(
          "--no-cleanup",
          action="store_true",
          help="Keep the temporary worktree(s) on disk after the command exits.",
      )
      return parser.parse_args(argv)


  # ----------------------------------------------------------------------
  # Repo config loading
  # ----------------------------------------------------------------------

  def _load_repos_config(args: argparse.Namespace) -> list[RepoConfig]:
      configs: list[RepoConfig] = []
      branch = args.branch

      if args.repo:
          path = Path(args.repo).resolve()
          configs.append(
              RepoConfig(path=path, name=path.name, branch=branch)
          )
      else:
          cfg_path = Path(args.repos_file).resolve()
          if not cfg_path.is_file():
              raise _FatalError(
                  f"--repos-file path does not exist: {cfg_path}"
              )
          for line_num, raw_line in enumerate(
              cfg_path.read_text(encoding="utf-8").splitlines(), start=1
          ):
              line = raw_line.strip()
              if not line or line.startswith("#"):
                  continue
              path = Path(line).expanduser().resolve()
              configs.append(
                  RepoConfig(path=path, name=path.name, branch=branch)
              )
          if not configs:
              raise _FatalError(
                  f"--repos-file contained no usable entries: {cfg_path}"
              )
      return configs


  # ----------------------------------------------------------------------
  # Git helpers
  # ----------------------------------------------------------------------

  def _run_git(repo: Path, *args: str) -> str:
      result = subprocess.run(
          ["git", *args],
          cwd=str(repo),
          check=True,
          capture_output=True,
          text=True,
      )
      return result.stdout.strip()


  def _is_git_repo(path: Path) -> bool:
      return (path / ".git").exists() or (path / "HEAD").is_file()


  def _ensure_branch_exists(repo: Path, branch: str) -> None:
      """Verify the named branch can be resolved locally or via origin/."""
      local = _run_git(repo, "branch", "--list", branch)
      if local.strip():
          return
      remote = _run_git(repo, "branch", "-r", "--list", f"origin/{branch}")
      if remote.strip():
          return
      raise _FatalError(
          f"branch {branch!r} not found locally or as origin/{branch} "
          f"in {repo} (did Plan 2's setup runbook run against this repo?)"
      )


  def _resolve_branch_ref(repo: Path, branch: str) -> str:
      """Return a ref name we can hand to ``git worktree add``."""
      local = _run_git(repo, "branch", "--list", branch)
      if local.strip():
          return branch
      return f"origin/{branch}"


  def _create_worktree(repo: Path, branch: str, dest: Path) -> Path:
      """Create a detached worktree at ``dest`` pointing at ``branch``.

      The worktree is detached (no local branch tracking the ref) so we
      never compete with the user's branch checkouts.
      """
      ref = _resolve_branch_ref(repo, branch)
      # --detach gives us a worktree at the ref without creating a new branch.
      _run_git(repo, "worktree", "add", "--detach", str(dest), ref)
      return dest


  def _remove_worktree(repo: Path, worktree_dir: Path) -> None:
      try:
          _run_git(repo, "worktree", "remove", "--force", str(worktree_dir))
      except subprocess.CalledProcessError:
          # Fall back to manual removal; ``git worktree prune`` will catch up.
          shutil.rmtree(worktree_dir, ignore_errors=True)
          try:
              _run_git(repo, "worktree", "prune")
          except subprocess.CalledProcessError:
              pass


  # ----------------------------------------------------------------------
  # Snapshot extraction
  # ----------------------------------------------------------------------

  def _extract_queue_snapshot(
      config: RepoConfig,
      *,
      states: Iterable[str],
      worktree_root: Path,
  ) -> RepoSnapshot:
      if not config.path.exists():
          raise _FatalError(f"--repo path does not exist: {config.path}")
      if not _is_git_repo(config.path):
          raise _FatalError(
              f"{config.path} is not a git repository (no .git/ directory)"
          )
      _ensure_branch_exists(config.path, config.branch)

      worktree_dir = worktree_root / f"{config.name}__{config.branch}"
      # Pre-clean in case a previous --no-cleanup run left a stub behind.
      if worktree_dir.exists():
          _remove_worktree(config.path, worktree_dir)

      _create_worktree(config.path, config.branch, worktree_dir)
      snapshot = RepoSnapshot(config=config, worktree_path=worktree_dir)
      for state in states:
          snapshot.rows.extend(
              enumerate_state(
                  worktree_dir, state, repo_name=config.name
              )
          )
      return snapshot


  # ----------------------------------------------------------------------
  # Rendering
  # ----------------------------------------------------------------------

  _COLUMN_ORDER: tuple[str, ...] = (
      "REPO",
      "STATE",
      "ID",
      "TYPE",
      "SEVERITY",
      "AGE",
      "TITLE",
  )


  def _age_string(created_at: datetime | None) -> str:
      if created_at is None:
          return "?"
      now = datetime.now(tz=timezone.utc)
      # Normalise: treat naive datetimes as UTC.
      if created_at.tzinfo is None:
          created_at = created_at.replace(tzinfo=timezone.utc)
      delta = now - created_at
      total_seconds = int(delta.total_seconds())
      if total_seconds < 60:
          return f"{max(total_seconds, 0)}s"
      minutes = total_seconds // 60
      if minutes < 60:
          return f"{minutes}m"
      hours = minutes // 60
      if hours < 48:
          return f"{hours}h"
      days = hours // 24
      return f"{days}d"


  def _row_to_cells(row: PBIRow | PBIRowError) -> list[str]:
      if isinstance(row, PBIRow):
          return [
              row.repo_name,
              row.state,
              row.pbi_id,
              row.pbi_type,
              row.severity,
              _age_string(row.created_at),
              row.title,
          ]
      # PBIRowError
      return [
          row.repo_name,
          row.state,
          row.pbi_dir.name,
          "?",
          "?",
          "?",
          f"(parse error) {row.message}",
      ]


  def _render_table(rows: list[PBIRow | PBIRowError]) -> str:
      cells = [list(_COLUMN_ORDER)]
      for row in rows:
          cells.append(_row_to_cells(row))

      widths = [0] * len(_COLUMN_ORDER)
      for row_cells in cells:
          for idx, cell in enumerate(row_cells):
              widths[idx] = max(widths[idx], len(cell))

      lines: list[str] = []
      for row_cells in cells:
          parts = [
              cell.ljust(widths[idx])
              for idx, cell in enumerate(row_cells[:-1])
          ]
          parts.append(row_cells[-1])  # last column not padded
          lines.append("  ".join(parts).rstrip())
      return "\n".join(lines) + "\n"


  def _iso(value: datetime | None) -> str | None:
      if value is None:
          return None
      if value.tzinfo is None:
          value = value.replace(tzinfo=timezone.utc)
      return value.isoformat()


  def _row_to_json(row: PBIRow | PBIRowError) -> dict[str, object]:
      if isinstance(row, PBIRow):
          return {
              "repo": str(row.repo_path),
              "repo_name": row.repo_name,
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
          "repo": str(row.repo_path),
          "repo_name": row.repo_name,
          "state": row.state,
          "id": row.pbi_dir.name,
          "type": "?",
          "severity": "?",
          "attempts": 0,
          "created_at": None,
          "updated_at": None,
          "title": f"(parse error) {row.message}",
          "pbi_dir": row.relative_pbi_dir(),
          "error": row.message,
      }


  def _render_json(
      snapshots: list[RepoSnapshot],
      top_level_errors: list[str],
  ) -> str:
      rows: list[dict[str, object]] = []
      for snap in snapshots:
          for row in snap.rows:
              rows.append(_row_to_json(row))
      payload: dict[str, object] = {
          "rows": rows,
          "errors": top_level_errors,
          "repos": [
              {
                  "path": str(snap.config.path),
                  "name": snap.config.name,
                  "branch": snap.config.branch,
                  "worktree_path": str(snap.worktree_path),
              }
              for snap in snapshots
          ],
      }
      return json.dumps(payload, indent=2, sort_keys=True) + "\n"


  # ----------------------------------------------------------------------
  # Orchestrator
  # ----------------------------------------------------------------------

  def main(argv: list[str] | None = None) -> int:
      args = _parse_args(argv if argv is not None else sys.argv[1:])

      states: tuple[str, ...] = (
          (args.state,) if args.state else STATE_FOLDERS
      )

      try:
          configs = _load_repos_config(args)
      except _FatalError as exc:
          print(f"error: {exc}", file=sys.stderr)
          return 2

      worktree_root = Path(tempfile.mkdtemp(prefix="ralph-status-"))
      snapshots: list[RepoSnapshot] = []
      try:
          for config in configs:
              snap = _extract_queue_snapshot(
                  config, states=states, worktree_root=worktree_root
              )
              snapshots.append(snap)

          all_rows: list[PBIRow | PBIRowError] = []
          for snap in snapshots:
              all_rows.extend(snap.rows)

          if args.emit_json:
              sys.stdout.write(_render_json(snapshots, top_level_errors=[]))
          else:
              sys.stdout.write(_render_table(all_rows))
              # Human-friendly summary on stderr (mirrors ralph-add).
              repo_count = len(snapshots)
              pbi_count = len(all_rows)
              print(
                  f"# {pbi_count} PBI(s) across {repo_count} repo(s) "
                  f"(states: {', '.join(states)})",
                  file=sys.stderr,
              )
          return 0
      except _FatalError as exc:
          print(f"error: {exc}", file=sys.stderr)
          return 2
      except subprocess.CalledProcessError as exc:
          stderr = exc.stderr or ""
          print(
              f"error: git command failed ({exc.returncode}): "
              f"{' '.join(exc.cmd)}\n{stderr}",
              file=sys.stderr,
          )
          return 2
      finally:
          if not args.no_cleanup:
              for snap in snapshots:
                  _remove_worktree(snap.config.path, snap.worktree_path)
              shutil.rmtree(worktree_root, ignore_errors=True)


  if __name__ == "__main__":
      raise SystemExit(main())
  ```

- [ ] 2. Run the tests; they must now pass:
  ```
  uv run pytest tests/skills/test_ralph_status.py -v
  ```
  Expected: all ten tests pass. Exit code 0. The `--no-cleanup` test asserts the worktree path stays on disk; the test does NOT itself clean it up because `tmp_path` removal handles it after the test.

- [ ] 3. Run ruff and mypy against the new file:
  ```
  uv run ruff check skills/ralph-status/scripts/show.py tests/skills/test_ralph_status.py
  uv run mypy skills/ralph-status/scripts/show.py tests/skills/test_ralph_status.py
  ```
  Expected: ruff: `All checks passed!`. Mypy: `Success: no issues found in 2 source files`.

- [ ] 4. Commit:
  ```
  git add skills/ralph-status/scripts/show.py tests/skills/test_ralph_status.py
  git commit -m "feat(ralph-status): render queue state across configured repos"
  ```
  Expected: commit succeeds.

---

## Task 4 — Cross-validate against the workspace samples

**Files**
- Modify: `tests/skills/test_ralph_status.py` (one additional test that runs the reader against the Plan 1 sample directories)

**Steps**

- [ ] 1. Append the following test to the bottom of `tests/skills/test_ralph_status.py` (just before the file ends). The test wires the `ralph-status` reader to Plan 1's sample tree by laying down the samples on a fresh `ralph-queue` branch, then asserting the reader recognises every sample. This guards against drift between the canonical sample fixtures and the reader's expectations:
  ```python
  def test_status_recognises_plan1_samples(
      tmp_path: Path,
      show_module: ModuleType,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      """The reader must parse the canonical Plan 1 sample PBIs cleanly.

      We copy ``samples/feature-WI-1234/`` etc. into ``.ralph/inbox/<id>/``
      on a fresh ralph-queue branch and run the skill against the repo.
      All three samples must show up as successful PBIRow entries (not
      PBIRowError) in the JSON output.
      """
      samples_dir = REPO_ROOT / "samples"

      bare = tmp_path / "svc.git"
      work = tmp_path / "svc-work"
      subprocess.run(["git", "init", "--bare", str(bare)], check=True)
      subprocess.run(["git", "init", str(work)], check=True)
      _git(work, "config", "user.email", "test@example.com")
      _git(work, "config", "user.name", "Test User")
      _git(work, "commit", "--allow-empty", "-m", "chore: initial main")
      _git(work, "branch", "-M", "main")
      _git(work, "remote", "add", "origin", str(bare))
      _git(work, "push", "-u", "origin", "main")
      _git(work, "checkout", "-b", "ralph-queue")

      # Map each sample to a queue id under .ralph/inbox/.
      sample_to_queue_id = {
          "feature-WI-1234": "WI-1234",
          "bug-deploy-rosa-irsa-2026-05-23": "BUG-rosa-irsa-2026-05-23",
          "pr-feedback-WI-1234-r2": "PR-feedback-WI-1234-r2",
      }
      for sample_name, queue_id in sample_to_queue_id.items():
          src = samples_dir / sample_name
          assert src.is_dir(), f"missing sample: {src}"
          dest = work / ".ralph" / "inbox" / queue_id
          dest.mkdir(parents=True)
          for child in src.iterdir():
              if child.is_file():
                  (dest / child.name).write_bytes(child.read_bytes())

      _git(work, "add", ".ralph")
      _git(work, "commit", "-m", "feat(queue): seed from Plan 1 samples")
      _git(work, "push", "-u", "origin", "ralph-queue")
      _git(work, "checkout", "main")

      exit_code = show_module.main(["--repo", str(work), "--json"])
      assert exit_code == 0
      payload = json.loads(capsys.readouterr().out)

      assert len(payload["rows"]) == 3
      assert all(row["error"] is None for row in payload["rows"]), (
          "Plan 1 samples must parse cleanly:\n"
          + json.dumps(payload["rows"], indent=2)
      )
      types = sorted(row["type"] for row in payload["rows"])
      assert types == ["bug", "feature", "pr-feedback"]
  ```

- [ ] 2. Run the full file. The new test must pass alongside the others:
  ```
  uv run pytest tests/skills/test_ralph_status.py -v
  ```
  Expected: eleven tests pass, including `test_status_recognises_plan1_samples`. Exit code 0.

- [ ] 3. Run ruff and mypy:
  ```
  uv run ruff check tests/skills/test_ralph_status.py
  uv run mypy tests/skills/test_ralph_status.py
  ```
  Expected: both clean.

- [ ] 4. Commit:
  ```
  git add tests/skills/test_ralph_status.py
  git commit -m "test(ralph-status): cross-check reader against Plan 1 sample PBIs"
  ```
  Expected: commit succeeds.

---

## Task 5 — Full toolchain pass

**Files**
- (none — toolchain-only step)

**Steps**

- [ ] 1. Run the whole local gate to confirm Plan 4's deliverables are green and don't disturb Plans 1, 2, or 3:
  ```
  uv run ruff check .
  uv run ruff format --check .
  uv run mypy ralph_executor scripts skills tests
  uv run pytest
  ```
  Expected: each command exits 0. Ruff: `All checks passed!`. Format: nothing to reformat. Mypy: `Success: no issues found in N source files`. Pytest: every test from Plans 1 + 2 + 3 + 4 passes.

- [ ] 2. Confirm the skill's `--help` is meaningful:
  ```
  uv run python skills/ralph-status/scripts/show.py --help
  ```
  Expected: help text mentions `--repo`, `--repos-file`, `--state`, `--branch`, `--json`, `--no-cleanup`, and the `RALPH_QUEUE_BRANCH` env var.

- [ ] 3. Smoke-test the JSON output shape with a hand-built minimal queue (no test framework). This step is OPTIONAL but useful for the implementer's confidence:
  ```bash
  # Optional: spin up a throwaway repo in /tmp, seed a single PBI, run the skill.
  TMP=$(mktemp -d)
  git init --bare "$TMP/svc.git" >/dev/null
  git init "$TMP/work" >/dev/null
  ( cd "$TMP/work"
    git config user.email t@example.com
    git config user.name Test
    git commit --allow-empty -m chore >/dev/null
    git branch -M main
    git remote add origin "$TMP/svc.git"
    git push -u origin main >/dev/null
    git checkout -b ralph-queue
    mkdir -p .ralph/inbox/WI-1
    cat > .ralph/inbox/WI-1/PBI.md <<'EOF'
  ---
  id: WI-1
  type: feature
  status: inbox
  severity: normal
  attempts: 0
  created_at: 2026-05-24T09:15:00+00:00
  updated_at: 2026-05-24T09:15:00+00:00
  ---

  # Smoke test PBI
  EOF
    touch .ralph/inbox/WI-1/HISTORY.md
    git add .ralph
    git commit -m feat:seed >/dev/null
    git push -u origin ralph-queue >/dev/null
    git checkout main >/dev/null )
  uv run python skills/ralph-status/scripts/show.py --repo "$TMP/work"
  uv run python skills/ralph-status/scripts/show.py --repo "$TMP/work" --json
  rm -rf "$TMP"
  ```
  Expected: the table mode shows a single `WI-1` row; the JSON mode emits a valid JSON document with `rows[0].id == "WI-1"`.

- [ ] 4. Confirm the commit history is clean and conventional:
  ```
  git log --oneline -10
  ```
  Expected: three Plan-4 commits at the top of the log, each with a conventional-commit prefix:
  - `feat(pbi-reader): add shared queue-folder PBI reader and ralph-status scaffold`
  - `feat(ralph-status): render queue state across configured repos`
  - `test(ralph-status): cross-check reader against Plan 1 sample PBIs`

---

## Verification

This is the orchestrator's Plan 4 verification gate. The gate command from `2026-05-24-00-orchestrator.md` is:

> `uv run pytest tests/skills/test_ralph_status.py -v` — All pass

Plan 4 satisfies the gate with the artifacts above. The verification has two parts: artifact gate (no network, hermetic) and a manual rehearsal against a real `ralph-queue` branch.

### Part 1 — artifact gate (no network)

- [ ] 1. Run the per-skill test selection in isolation:
  ```
  uv run pytest tests/skills/test_ralph_status.py tests/test_pbi_reader.py -v
  ```
  Expected: twenty-two tests collected (eleven from `test_ralph_status.py` + eleven from `test_pbi_reader.py`), all passing.

- [ ] 2. Run the full repo gate to confirm Plan 4 didn't disturb Plans 1, 2, or 3:
  ```
  uv run ruff check .
  uv run ruff format --check .
  uv run mypy ralph_executor scripts skills tests
  uv run pytest
  ```
  Expected: every command exits 0.

- [ ] 3. Confirm the skill is discoverable as a Claude Code skill (the `SKILL.md` frontmatter must parse). Run:
  ```
  uv run python -c "import yaml; from pathlib import Path; text = Path('skills/ralph-status/SKILL.md').read_text(encoding='utf-8'); lines = text.splitlines(); assert lines[0] == '---', 'SKILL.md must start with frontmatter fence'; end = next(i for i, line in enumerate(lines[1:], start=1) if line == '---'); fm = yaml.safe_load('\n'.join(lines[1:end])); assert fm['name'] == 'ralph-status'; assert isinstance(fm['description'], str) and len(fm['description']) >= 80; print('SKILL.md frontmatter OK:', fm['name'])"
  ```
  Expected output: `SKILL.md frontmatter OK: ralph-status`.

### Part 2 — orchestrator gate on a real `ralph-queue` branch (manual; once per environment)

The orchestrator gate also expects `ralph-status` to read an end-to-end queue from a real `ralph-queue` branch. This is the same rehearsal the BA does in production; the verification just exercises it once to prove the wiring.

- [ ] 4. From a checkout where Plans 2 and 3 have been executed (so `origin/ralph-queue` exists on the chosen test repo AND `ralph-add` has been used to write at least one PBI), run:
  ```
  uv run python skills/ralph-status/scripts/show.py --repo /path/to/checked-out/service-repo
  ```
  Expected: exit code 0; the table shows at least one row corresponding to the PBI `ralph-add` submitted; the row's `STATE` column reads `inbox`.

- [ ] 5. Re-run with `--json` and pipe to `jq` to confirm machine-readability:
  ```
  uv run python skills/ralph-status/scripts/show.py --repo /path/to/checked-out/service-repo --json | jq '.rows | length'
  ```
  Expected: a numeric value matching the count of PBIs across all five state folders. (`jq` is optional — the JSON parses with any JSON consumer.)

- [ ] 6. Re-run with `--state inbox --json` and confirm only inbox PBIs are returned:
  ```
  uv run python skills/ralph-status/scripts/show.py --repo /path/to/checked-out/service-repo --state inbox --json | jq '.rows[] | .state' | sort -u
  ```
  Expected: every emitted state value is `"inbox"`.

- [ ] 7. Build a two-repo config and confirm aggregation works:
  ```
  printf '/path/to/service-auth\n/path/to/service-billing\n' > /tmp/repos.txt
  uv run python skills/ralph-status/scripts/show.py --repos-file /tmp/repos.txt
  ```
  Expected: the table shows rows from both repos, each tagged with the appropriate `REPO` column value.

If steps 1–7 all pass, Plan 4 is complete. The next plan in the dependency graph is Plan 7 (executor core), which builds on Plans 1, 2, 5, and 6 — Plan 4 does not block any downstream plan, but it is the canonical read surface that Plans 7, 8, and 9 will share via `scripts/pbi_reader.py`.
