# Multi-ralph Implementation Plan (v2 — confidence-rated)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable N `ralph-executor` instances to drain the same `queue_repo` concurrently. Each instance carries a stable, operator-supplied `instance_id`. Workspace path becomes `<workspace_root>/queue-<instance-id>/`. Every claimed PBI carries a `CLAIM.json` naming its owner. Coordination is purely via the existing git push-with-rebase machinery on the queue branch.

**Architecture:** Add `instance_id` to `ExecutorConfig` (CLI > env > project TOML > user TOML > sanitised hostname). Single source of truth helper `queue_clone_path(workspace_root, instance_id)`. Atomic claim via a `post_mv` hook on `_move` so the existing `commit_all` picks up the new `CLAIM.json` alongside the `git mv`. `current_pbi()` filters by ownership. OS-level workspace lockfile. Operator skills use a parallel resolver layer in `scripts/queue_writer.py` — extend it to honour `instance_id`. New `ralph-recover` skill is the manual escape hatch.

**Tech Stack:** Python 3.12 stdlib only — `fcntl` / `msvcrt` for cross-platform locking, `socket` for hostname, `json` for `CLAIM.json`. No new runtime dependencies.

**Spec:** `docs/superpowers/specs/2026-05-31-multi-ralph-design.md`
**Standards consulted:** `standards/ralph-runtime.md` (better-memory knowledge) — confidence-scoring + verify-before-commit gates applied below.

---

## Confidence policy

Each task carries a confidence percentage. Any task below 95% includes one or more **Step 0** verification / scaffold steps to lift it to ≥87%. Tasks at 95%+ are direct TDD steps. No task ships below 87%.

## Forward-reference audit

Task ordering does not introduce import-time forward references:
- T7 imports `queue_path` (T6) — order ok.
- T10 imports `claim` (T9) — order ok.
- T11 imports `claim` (T9) — order ok.
- T14 imports `lockfile` (T13) and `queue_path` (T6) — order ok.
- T15–T18 import `claim` (T9) and use `scripts.queue_writer` extended in TA — TA precedes them.

## Lint-cleanliness conventions

- `from datetime import UTC` (not `timezone.utc`) — UP017.
- `StrEnum` if defining string-valued enums — UP042. None needed here.
- Drop unused `import pytest`; import only what the test body uses — F401.
- Type hints on test helpers; mypy clean.

---

## File Structure

### New files

- `ralph_executor/identity.py` — `sanitize_instance_id`, `validate_instance_id`, `default_instance_id`, `InstanceIdError`, `INSTANCE_ID_REGEX`.
- `ralph_executor/lockfile.py` — `WorkspaceLock` (POSIX `fcntl` / Windows `msvcrt`) context manager.
- `ralph_executor/claim.py` — `ClaimInfo`, `CLAIM_FILENAME`, `read_claim`, `write_claim`, `ClaimParseError`.
- `ralph_executor/queue_path.py` — `queue_clone_path(workspace_root, instance_id) -> Path`.
- `skills/ralph-recover/SKILL.md`, `skills/ralph-recover/scripts/__init__.py`, `skills/ralph-recover/scripts/recover.py`.
- `tests/executor/test_identity.py`, `test_lockfile.py`, `test_claim.py`, `test_queue_path.py`, `test_multi_ralph_integration.py`.
- `tests/skills/test_ralph_recover.py`.

### Modified files

- `ralph_executor/config.py` — `instance_id` field + resolution chain.
- `ralph_executor/user_config.py` — `read_instance_id`, `write_instance_id`.
- `ralph_executor/cli.py` — `--instance-id` flag, override wiring, lockfile acquire/release.
- `ralph_executor/setup_cmds.py` — init prompts for `instance_id`.
- `ralph_executor/queue_clone.py` — `dest: Path | None` parameter + legacy rename.
- `ralph_executor/loop.py` — `_queue_repo_root` uses helper; `_claim_pbi_worktree` passes `post_mv` lambda; `_check_cycle_detector` passes `instance_id`.
- `ralph_executor/queue/filesystem.py` — `_root` uses helper; `current_pbi()` filters by `instance_id`.
- `ralph_executor/queue/movements.py` — `_move` accepts `post_mv: Callable[[Path], None] | None`; `move_inbox_to_current` forwards it.
- `ralph_executor/safety/halt.py` — `halt_and_acknowledge` + `write_meta_bug` accept `tripped_by_instance` and emit it.
- `scripts/queue_writer.py` — `resolve_instance_id`, `acquire_queue_clone(...)` gains `instance_id` parameter.
- `scripts/pbi_reader.py` — `PBIRow` gains `owner: str | None` populated for current/ rows.
- `skills/ralph-status/scripts/status.py` — OWNER column, `--instance-id` flag.
- `skills/ralph-cancel/scripts/cancel.py` — refuse foreign CLAIM, `--instance-id` flag.
- `skills/ralph-promote/scripts/promote.py` — refuse foreign CLAIM on current/, `--instance-id` flag.
- `README.md`, `docs/runbooks/ralph-setup.md` — operator docs.

---

## Task T1: Identity helpers — **confidence: 96%**

**Files:** `ralph_executor/identity.py` (new), `tests/executor/test_identity.py` (new)

- [ ] **Step 1: Write the failing test**

`tests/executor/test_identity.py`:

```python
"""Identity sanitisation, validation, hostname default."""

from __future__ import annotations

import pytest

from ralph_executor.identity import (
    INSTANCE_ID_REGEX,
    InstanceIdError,
    default_instance_id,
    sanitize_instance_id,
    validate_instance_id,
)


class TestSanitize:
    def test_lowercases(self) -> None:
        assert sanitize_instance_id("MyBox") == "mybox"

    def test_replaces_dots_with_dashes(self) -> None:
        assert sanitize_instance_id("box.example.com") == "box-example-com"

    def test_replaces_other_chars_with_dashes(self) -> None:
        assert sanitize_instance_id("box/space:colon") == "box-space-colon"

    def test_collapses_repeated_dashes(self) -> None:
        assert sanitize_instance_id("box...com") == "box-com"

    def test_strips_leading_trailing_dashes(self) -> None:
        assert sanitize_instance_id("---box---") == "box"

    def test_already_clean_passes_through(self) -> None:
        assert sanitize_instance_id("ralph-a") == "ralph-a"

    def test_empty_after_sanitise_returns_empty(self) -> None:
        assert sanitize_instance_id("---") == ""


class TestValidate:
    def test_accepts_simple_id(self) -> None:
        assert validate_instance_id("ralph-a") == "ralph-a"

    def test_accepts_digits_and_underscore(self) -> None:
        assert validate_instance_id("ralph_1") == "ralph_1"

    def test_rejects_empty(self) -> None:
        with pytest.raises(InstanceIdError, match="must be set"):
            validate_instance_id("")

    def test_rejects_leading_dash(self) -> None:
        with pytest.raises(InstanceIdError, match="does not match"):
            validate_instance_id("-ralph")

    def test_rejects_uppercase(self) -> None:
        with pytest.raises(InstanceIdError, match="does not match"):
            validate_instance_id("Ralph")

    def test_rejects_too_long(self) -> None:
        with pytest.raises(InstanceIdError, match="does not match"):
            validate_instance_id("a" * 64)


class TestRegex:
    def test_pattern_value(self) -> None:
        assert INSTANCE_ID_REGEX.pattern == r"^[a-z0-9][a-z0-9_-]{0,62}$"


class TestDefaultFromHostname:
    def test_returns_sanitised_hostname(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("socket.gethostname", lambda: "MyBox.example.com")
        assert default_instance_id() == "mybox-example-com"

    def test_empty_hostname_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("socket.gethostname", lambda: "")
        assert default_instance_id() == ""
```

- [ ] **Step 2: Run test (FAIL on missing module)**

`uv run pytest tests/executor/test_identity.py -v`

- [ ] **Step 3: Implement** — `ralph_executor/identity.py`:

```python
"""Instance identity helpers — sanitisation, validation, hostname default."""

from __future__ import annotations

import re
import socket

INSTANCE_ID_REGEX = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")


class InstanceIdError(ValueError):
    """Raised when an instance_id cannot be validated."""


def sanitize_instance_id(value: str) -> str:
    """Lowercase; replace any char outside [a-z0-9_-] with `-`; collapse
    runs; strip leading/trailing dashes. Pure — never raises."""
    lowered = value.strip().lower()
    swapped = re.sub(r"[^a-z0-9_-]", "-", lowered)
    collapsed = re.sub(r"-+", "-", swapped)
    return collapsed.strip("-")


def validate_instance_id(value: str) -> str:
    """Return value unchanged when it matches INSTANCE_ID_REGEX; else raise."""
    if not value:
        raise InstanceIdError(
            "instance_id must be set (CLI / env / TOML / hostname default)"
        )
    if not INSTANCE_ID_REGEX.match(value):
        raise InstanceIdError(
            f"instance_id {value!r} does not match {INSTANCE_ID_REGEX.pattern}"
        )
    return value


def default_instance_id() -> str:
    """Return the sanitised hostname for use as the default instance_id."""
    return sanitize_instance_id(socket.gethostname())
```

- [ ] **Step 4: PASS** — 13 tests.

- [ ] **Step 5: Commit**

```
git add ralph_executor/identity.py tests/executor/test_identity.py
git commit -m "multi-ralph: instance_id sanitise/validate/default helpers"
```

---

## Task T2: user_config read/write — **confidence: 94%**

**Files:** `ralph_executor/user_config.py` (modify), `tests/executor/test_user_config.py` (extend)

**Step 0 mitigation (verified):** Existing `read_queue_repo` / `write_queue_repo` (`ralph_executor/user_config.py:118` and `:249`) is the template. `_write_user_config` merge-writes — preserves siblings on a single-key write.

- [ ] **Step 1: Failing tests** — append to `tests/executor/test_user_config.py`:

```python
from pathlib import Path

import pytest

from ralph_executor.config import ConfigError
from ralph_executor.user_config import (
    read_instance_id,
    user_config_path,
    write_instance_id,
)


def test_read_instance_id_missing_file_returns_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert read_instance_id() is None


def test_write_then_read_instance_id_roundtrips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    written = write_instance_id("ralph-a")
    assert written == user_config_path()
    assert read_instance_id() == "ralph-a"


def test_read_instance_id_non_string_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    cfg = user_config_path()
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("instance_id = 42\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="instance_id"):
        read_instance_id()


def test_write_instance_id_preserves_other_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ralph_executor.user_config import write_queue_repo

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    write_queue_repo("https://github.com/owner/queue")
    write_instance_id("ralph-a")
    text = user_config_path().read_text(encoding="utf-8")
    assert 'queue_repo = "https://github.com/owner/queue"' in text
    assert 'instance_id = "ralph-a"' in text
```

- [ ] **Step 2: FAIL on import**

- [ ] **Step 3: Implement** — append to `ralph_executor/user_config.py`:

```python
def read_instance_id() -> str | None:
    """Return the ``instance_id`` value from the user config, or None."""
    data = _load_user_config()
    raw = data.get("instance_id")
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise ConfigError(
            f"{user_config_path()}: instance_id must be a non-empty string, "
            f"got {type(raw).__name__}"
        )
    return raw.strip()


def write_instance_id(value: str) -> Path:
    """Persist ``instance_id`` to ``~/.ralph/config.toml``."""
    return _write_user_config({"instance_id": value})
```

- [ ] **Step 4: PASS** — 4 tests.

- [ ] **Step 5: Commit**

```
git add ralph_executor/user_config.py tests/executor/test_user_config.py
git commit -m "multi-ralph: read_instance_id/write_instance_id user-config helpers"
```

---

## Task T3: `ExecutorConfig.instance_id` + resolution — **confidence: 93%**

**Files:** `ralph_executor/config.py` (modify), `tests/executor/test_config.py` (extend)

**Step 0 mitigation (verified):** Re-read `load_config` end-section (`ralph_executor/config.py:790-820`). The `ExecutorConfig(...)` constructor call accepts a new field via plain kwargs; `_TOML_KNOWN_KEYS` is a frozenset extended by adding the key string.

- [ ] **Step 1: Failing tests** — append to `tests/executor/test_config.py`:

```python
def test_instance_id_defaults_to_sanitised_hostname(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("RALPH_INSTANCE_ID", raising=False)
    monkeypatch.setattr("socket.gethostname", lambda: "MyBox.example.com")
    _prepare_user_config(tmp_path, queue_repo="https://github.com/owner/queue")
    _prepare_project_repo(tmp_path)
    monkeypatch.chdir(tmp_path / "project")
    cfg = load_config()
    assert cfg.instance_id == "mybox-example-com"


def test_instance_id_env_wins_over_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("RALPH_INSTANCE_ID", "ralph-from-env")
    _prepare_user_config(tmp_path, queue_repo="https://github.com/owner/queue",
                         instance_id="ralph-from-toml")
    _prepare_project_repo(tmp_path)
    monkeypatch.chdir(tmp_path / "project")
    assert load_config().instance_id == "ralph-from-env"


def test_instance_id_toml_wins_over_hostname(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("RALPH_INSTANCE_ID", raising=False)
    monkeypatch.setattr("socket.gethostname", lambda: "hostname-default")
    _prepare_user_config(tmp_path, queue_repo="https://github.com/owner/queue",
                         instance_id="ralph-from-toml")
    _prepare_project_repo(tmp_path)
    monkeypatch.chdir(tmp_path / "project")
    assert load_config().instance_id == "ralph-from-toml"


def test_instance_id_invalid_raises_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("RALPH_INSTANCE_ID", "Not Valid!")
    _prepare_user_config(tmp_path, queue_repo="https://github.com/owner/queue")
    _prepare_project_repo(tmp_path)
    monkeypatch.chdir(tmp_path / "project")
    with pytest.raises(ConfigError, match="instance_id"):
        load_config()


def test_instance_id_missing_hostname_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("RALPH_INSTANCE_ID", raising=False)
    monkeypatch.setattr("socket.gethostname", lambda: "")
    _prepare_user_config(tmp_path, queue_repo="https://github.com/owner/queue")
    _prepare_project_repo(tmp_path)
    monkeypatch.chdir(tmp_path / "project")
    with pytest.raises(ConfigError, match="instance_id"):
        load_config()
```

Helpers (top of test module if absent):

```python
def _prepare_user_config(
    tmp_path: Path, *, queue_repo: str, instance_id: str | None = None
) -> None:
    cfg = tmp_path / ".ralph" / "config.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    lines = [f'queue_repo = "{queue_repo}"']
    if instance_id is not None:
        lines.append(f'instance_id = "{instance_id}"')
    cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _prepare_project_repo(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir(parents=True, exist_ok=True)
    (project / ".git").mkdir(parents=True, exist_ok=True)
```

- [ ] **Step 2: FAIL on missing attr**

- [ ] **Step 3: Implementation in `ralph_executor/config.py`**

a. Extend `_TOML_KNOWN_KEYS` with `"instance_id"`.

b. Add field to `ExecutorConfig` (after `idle_exit_threshold`):

```python
    # Per-ralph identity. See ralph_executor/identity.py. Resolution:
    # --instance-id CLI > RALPH_INSTANCE_ID env > project TOML >
    # ~/.ralph/config.toml > sanitised hostname.
    instance_id: str = ""
```

c. Append resolution to `load_config`, AFTER `idle_exit_threshold` resolution, BEFORE the `return ExecutorConfig(...)`:

```python
    from ralph_executor.identity import (
        InstanceIdError,
        default_instance_id,
        sanitize_instance_id,
        validate_instance_id,
    )
    from ralph_executor.user_config import read_instance_id, user_config_path

    raw_env = os.environ.get("RALPH_INSTANCE_ID")
    instance_id_value: str | None = None
    instance_id_source = source_label
    if raw_env is not None and raw_env.strip():
        instance_id_value = raw_env.strip()
        instance_id_source = "RALPH_INSTANCE_ID env"
    else:
        toml_value = toml_overrides.get("instance_id")
        if toml_value is not None:
            if not isinstance(toml_value, str):
                raise ConfigError(
                    f"{source_label}: instance_id must be a string, "
                    f"got {type(toml_value).__name__}"
                )
            instance_id_value = toml_value.strip()
        if not instance_id_value:
            user_value = read_instance_id()
            if user_value is not None:
                instance_id_value = user_value
                instance_id_source = str(user_config_path())
        if not instance_id_value:
            instance_id_value = default_instance_id()
            instance_id_source = "hostname default"
    instance_id_value = sanitize_instance_id(instance_id_value or "")
    try:
        instance_id = validate_instance_id(instance_id_value)
    except InstanceIdError as exc:
        raise ConfigError(
            f"{instance_id_source}: {exc}. "
            "Set 'instance_id = \"<name>\"' in ~/.ralph/config.toml "
            "or pass --instance-id."
        ) from exc
```

d. Add `instance_id=instance_id` to the constructor.

- [ ] **Step 4: PASS** — 5 new tests.

- [ ] **Step 5: Commit**

```
git add ralph_executor/config.py tests/executor/test_config.py
git commit -m "multi-ralph: instance_id field + resolution chain in load_config"
```

---

## Task T4: `--instance-id` CLI flag — **confidence: 96%**

**Files:** `ralph_executor/cli.py` (modify), `tests/executor/test_cli.py` (extend)

- [ ] **Step 1: Failing tests**

```python
def test_instance_id_flag_overrides_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ralph_executor.cli import _apply_overrides

    monkeypatch.setenv("RALPH_INSTANCE_ID", "from-env")
    cfg = _make_fake_config(repo_path=tmp_path, instance_id="from-config")
    args = _make_args(instance_id="from-flag")
    assert _apply_overrides(cfg, args).instance_id == "from-flag"


def test_instance_id_flag_validated(tmp_path: Path) -> None:
    import pytest
    from ralph_executor.cli import _apply_overrides
    from ralph_executor.config import ConfigError

    cfg = _make_fake_config(repo_path=tmp_path, instance_id="ok")
    args = _make_args(instance_id="Not Valid!")
    with pytest.raises(ConfigError, match="instance_id"):
        _apply_overrides(cfg, args)
```

Helpers (top of module if absent):

```python
def _make_fake_config(*, repo_path: Path, instance_id: str = "ralph") -> ExecutorConfig:
    return ExecutorConfig(
        repo_path=repo_path,
        queue_repo="https://github.com/owner/queue",
        queue_branch="ralph-queue",
        main_branch="main",
        max_attempts=5,
        log_level=20,
        iteration_sleep_seconds=1.0,
        claude_binary="claude",
        claude_permission_mode="bypassPermissions",
        anthropic_api_key="",
        git_host="github",
        gh_owner="",
        ado_org_url="",
        ado_project="",
        halt_webhook="",
        pr_check_poll_max_attempts=1,
        pr_check_poll_interval_seconds=1.0,
        instance_id=instance_id,
    )


def _make_args(
    *,
    instance_id: str | None = None,
    repo: str | None = None,
    workspace: str | None = None,
    log_level: str | None = None,
    queue_repo: str | None = None,
    queue_branch: str | None = None,
    watch: bool = False,
    once: bool = False,
    iterations: int | None = None,
) -> argparse.Namespace:
    return argparse.Namespace(
        instance_id=instance_id,
        repo=repo,
        workspace=workspace,
        log_level=log_level,
        queue_repo=queue_repo,
        queue_branch=queue_branch,
        watch=watch,
        once=once,
        iterations=iterations,
    )
```

- [ ] **Step 2: FAIL**

- [ ] **Step 3: Implementation**

a. Add to `_build_parser`:

```python
    parser.add_argument(
        "--instance-id",
        dest="instance_id",
        metavar="ID",
        help=(
            "Override instance_id for this run. Resolution: this flag > "
            "RALPH_INSTANCE_ID env > project .ralph/config.toml > "
            "~/.ralph/config.toml > sanitised hostname."
        ),
    )
```

b. In `_apply_overrides`, before the existing return:

```python
    instance_id: str = cfg.instance_id
    if getattr(args, "instance_id", None):
        from ralph_executor.identity import (
            InstanceIdError,
            sanitize_instance_id,
            validate_instance_id,
        )

        candidate = sanitize_instance_id(args.instance_id)
        try:
            instance_id = validate_instance_id(candidate)
        except InstanceIdError as exc:
            raise ConfigError(f"--instance-id: {exc}") from exc
        changed = True
```

c. Add `instance_id=instance_id` to the `dataclasses.replace(...)` call.

- [ ] **Step 4: PASS**

- [ ] **Step 5: Commit**

```
git add ralph_executor/cli.py tests/executor/test_cli.py
git commit -m "multi-ralph: --instance-id CLI flag in _apply_overrides"
```

---

## Task T5: `cmd_init` prompts for `instance_id` — **confidence: 92%**

**Files:** `ralph_executor/setup_cmds.py` (modify), `tests/executor/test_setup_cmds.py` (extend)

**Step 0 mitigation (verified):** `cmd_init` flow (`setup_cmds.py:219`):
1. ralph_home check → write
2. queue_repo check → write
3. queue_branch check → write (last `print(f"wrote {branch_cfg}")` around line 326)
4. gh/claude tool checks (line 331+)
5. trailing message

Insertion point: between step 3 and step 4.

- [ ] **Step 1: Failing tests**

```python
def test_cmd_init_assume_yes_writes_hostname_instance_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ralph_executor.setup_cmds import cmd_init
    from ralph_executor.user_config import read_instance_id

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr("socket.gethostname", lambda: "BoxA")
    rc = cmd_init(ralph_home=None, assume_yes=True)
    assert rc == 0
    assert read_instance_id() == "boxa"


def test_cmd_init_idempotent_instance_id_no_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ralph_executor.setup_cmds import cmd_init
    from ralph_executor.user_config import read_instance_id, write_instance_id

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    write_instance_id("ralph-pinned")
    monkeypatch.setattr("socket.gethostname", lambda: "BoxA")
    rc = cmd_init(ralph_home=None, assume_yes=True)
    assert rc == 0
    assert read_instance_id() == "ralph-pinned"
```

- [ ] **Step 2: FAIL**

- [ ] **Step 3: Implementation** — add the BLOCK in `cmd_init` immediately after the `existing_branch`-handling block (after `print(f"wrote {branch_cfg}")`) and before `gh = _check_tool("gh")`:

```python
    # instance_id (multi-ralph identity). Mirrors queue_repo / queue_branch
    # handling — idempotent on re-run, --yes uses sanitised hostname.
    from ralph_executor.identity import (
        InstanceIdError,
        default_instance_id,
        sanitize_instance_id,
        validate_instance_id,
    )
    from ralph_executor.user_config import read_instance_id, write_instance_id

    existing_instance = read_instance_id()
    if existing_instance is not None:
        print(f"instance_id already set to {existing_instance} in {user_config_path()}")
    else:
        host_default = default_instance_id() or "ralph"
        if assume_yes:
            chosen_instance = host_default
        else:
            print(f"Instance id [{host_default}]: ", end="", flush=True)
            try:
                raw = input().strip()
            except EOFError:
                print("")
                raw = ""
            chosen_instance = sanitize_instance_id(raw) if raw else host_default
        try:
            chosen_instance = validate_instance_id(chosen_instance)
        except InstanceIdError as exc:
            raise ConfigError(f"init: {exc}") from exc
        try:
            instance_cfg = write_instance_id(chosen_instance)
        except OSError as exc:
            print(f"error: cannot write instance_id to user config: {exc}", file=sys.stderr)
            return 2
        print(f"instance_id = {chosen_instance}")
        print(f"wrote {instance_cfg}")
```

- [ ] **Step 4: PASS** — 2 new tests, existing init tests still green.

- [ ] **Step 5: Commit**

```
git add ralph_executor/setup_cmds.py tests/executor/test_setup_cmds.py
git commit -m "multi-ralph: cmd_init prompts for instance_id"
```

---

## Task T6: `queue_clone_path` helper — **confidence: 99%**

**Files:** `ralph_executor/queue_path.py` (new), `tests/executor/test_queue_path.py` (new)

- [ ] **Step 1: Failing tests**

```python
from pathlib import Path

import pytest

from ralph_executor.queue_path import queue_clone_path


def test_returns_namespaced_path(tmp_path: Path) -> None:
    assert queue_clone_path(tmp_path, "ralph-a") == tmp_path / "queue-ralph-a"


def test_n1_default_case(tmp_path: Path) -> None:
    assert queue_clone_path(tmp_path, "boxa") == tmp_path / "queue-boxa"


def test_empty_instance_id_raises() -> None:
    with pytest.raises(ValueError, match="instance_id"):
        queue_clone_path(Path("/tmp"), "")
```

- [ ] **Step 2: FAIL**

- [ ] **Step 3: Implement** — `ralph_executor/queue_path.py`:

```python
"""Single source of truth for the per-instance queue-clone path."""

from __future__ import annotations

from pathlib import Path


def queue_clone_path(workspace_root: Path, instance_id: str) -> Path:
    """Return the directory that holds this instance's queue clone."""
    if not instance_id:
        raise ValueError("queue_clone_path requires a non-empty instance_id")
    return workspace_root / f"queue-{instance_id}"
```

- [ ] **Step 4: PASS** — 3 tests.

- [ ] **Step 5: Commit**

```
git add ralph_executor/queue_path.py tests/executor/test_queue_path.py
git commit -m "multi-ralph: queue_clone_path helper"
```

---

## Task T7: Route executor queue-path callers through helper — **confidence: 91%**

**Files:** `ralph_executor/loop.py`, `ralph_executor/queue/filesystem.py`, `ralph_executor/queue/movements.py`, `ralph_executor/queue_clone.py` (modify); their test files (extend)

**Step 0 mitigation:** Sweep these files for the literal `"queue"` path construction; verified 4 sites in source + their counterparts in tests. Update test fixtures in the same task.

- [ ] **Step 1: Failing tests**

```python
def test_queue_repo_root_uses_instance_id(tmp_path: Path) -> None:
    import dataclasses
    from ralph_executor.loop import _queue_repo_root

    cfg = _make_fake_config(repo_path=tmp_path, instance_id="ralph-a")
    cfg = dataclasses.replace(cfg, workspace_root=tmp_path)
    assert _queue_repo_root(cfg) == tmp_path / "queue-ralph-a"


def test_filesystem_root_uses_instance_id(tmp_path: Path) -> None:
    import dataclasses
    from ralph_executor.queue.filesystem import FilesystemQueueSource

    cfg = _make_fake_config(repo_path=tmp_path, instance_id="ralph-a")
    cfg = dataclasses.replace(cfg, workspace_root=tmp_path)
    assert FilesystemQueueSource(cfg)._root == tmp_path / "queue-ralph-a" / ".ralph"
```

- [ ] **Step 2: FAIL**

- [ ] **Step 3: Implementation**

a. `ralph_executor/loop.py::_queue_repo_root`:

```python
def _queue_repo_root(cfg: ExecutorConfig) -> Path:
    """Filesystem path of this instance's queue clone. See queue_path."""
    from ralph_executor.queue_path import queue_clone_path

    return queue_clone_path(cfg.workspace_root, cfg.instance_id)
```

b. `ralph_executor/queue/filesystem.py::FilesystemQueueSource._root`:

```python
    @property
    def _root(self) -> Path:
        from ralph_executor.queue_path import queue_clone_path

        return (
            queue_clone_path(self._config.workspace_root, self._config.instance_id)
            / ".ralph"
        )
```

c. `ralph_executor/queue/movements.py::_queue_repo`:

```python
def _queue_repo(cfg: ExecutorConfig) -> Path:
    from ralph_executor.queue_path import queue_clone_path

    return queue_clone_path(cfg.workspace_root, cfg.instance_id)
```

d. `ralph_executor/queue_clone.py::ensure_queue_clone` — add `dest: Path | None = None` parameter, default to `workspace_root / "queue"` for backwards compat. Replace literal references inside the body with the resolved `dest`.

e. `ralph_executor/loop.py::_pull_queue`:

```python
def _pull_queue(cfg: ExecutorConfig) -> None:
    from ralph_executor.queue_path import queue_clone_path

    dest = queue_clone_path(cfg.workspace_root, cfg.instance_id)
    log.debug(
        "refreshing queue clone for %s (branch=%s) -> %s",
        cfg.queue_repo, cfg.queue_branch, dest,
    )
    ensure_queue_clone(
        cfg.workspace_root, cfg.queue_repo, cfg.queue_branch, dest=dest
    )
```

f. Regression sweep: grep test fixtures for `"queue"` path literals; update each to the namespaced form using the fixture's instance_id.

- [ ] **Step 4: PASS**

```
uv run pytest tests/executor/ -v
```

- [ ] **Step 5: Commit**

```
git add ralph_executor/loop.py ralph_executor/queue/filesystem.py \
        ralph_executor/queue/movements.py ralph_executor/queue_clone.py \
        tests/executor/test_loop.py tests/executor/test_filesystem_queue.py \
        tests/executor/test_queue_clone.py
git commit -m "multi-ralph: route queue-path callers through queue_clone_path"
```

---

## Task T8: Legacy rename — **confidence: 92%**

**Files:** `ralph_executor/queue_clone.py` (modify), `tests/executor/test_queue_clone.py` (extend)

**Step 0 mitigation:** Use `shutil.move` (not `Path.rename`) so the cross-volume case (workspace_root and tempdir on different drives, common on Windows CI) works. Both-exist guard fires FIRST so the operator gets the clear error before we touch either path.

- [ ] **Step 1: Failing tests**

```python
def test_ensure_queue_clone_migrates_legacy_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess
    from ralph_executor import queue_clone as qc

    monkeypatch.setattr(
        qc, "_run_git",
        lambda repo, *args, timeout=120.0: subprocess.CompletedProcess(
            args=args, returncode=0, stdout="", stderr=""
        ),
    )
    legacy = tmp_path / "queue"
    legacy.mkdir()
    (legacy / ".git").mkdir()
    dest = tmp_path / "queue-ralph-a"
    qc.ensure_queue_clone(
        tmp_path, "https://github.com/owner/queue", "ralph-queue", dest=dest
    )
    assert dest.is_dir()
    assert (dest / ".git").is_dir()
    assert not legacy.exists()


def test_ensure_queue_clone_refuses_both_paths_exist(tmp_path: Path) -> None:
    import pytest
    from ralph_executor.queue_clone import QueueCloneError, ensure_queue_clone

    legacy = tmp_path / "queue"
    legacy.mkdir()
    (legacy / ".git").mkdir()
    dest = tmp_path / "queue-ralph-a"
    dest.mkdir()
    (dest / ".git").mkdir()
    with pytest.raises(QueueCloneError, match="both"):
        ensure_queue_clone(
            tmp_path, "https://github.com/owner/queue", "ralph-queue", dest=dest
        )
```

- [ ] **Step 2: FAIL**

- [ ] **Step 3: Implementation** — inside `ensure_queue_clone` after `workspace_root.mkdir(...)` and before the existing `(dest / ".git").exists()` check:

```python
    import shutil

    legacy = workspace_root / "queue"
    if dest != legacy and legacy.is_dir():
        if dest.exists():
            raise QueueCloneError(
                f"both legacy queue/ and {dest.name}/ exist under {workspace_root}; "
                "remove one before continuing"
            )
        log.info("migrating legacy queue clone: %s -> %s", legacy, dest)
        shutil.move(str(legacy), str(dest))
```

- [ ] **Step 4: PASS**

- [ ] **Step 5: Commit**

```
git add ralph_executor/queue_clone.py tests/executor/test_queue_clone.py
git commit -m "multi-ralph: rename legacy <workspace_root>/queue/ on first startup"
```

---

## Task T9: `CLAIM.json` IO — **confidence: 96%**

**Files:** `ralph_executor/claim.py` (new), `tests/executor/test_claim.py` (new)

- [ ] **Step 1: Failing tests**

```python
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ralph_executor.claim import (
    CLAIM_FILENAME,
    ClaimInfo,
    ClaimParseError,
    read_claim,
    write_claim,
)


def test_write_then_read_roundtrips(tmp_path: Path) -> None:
    pbi_dir = tmp_path / "WI-1234"
    pbi_dir.mkdir()
    info = ClaimInfo(
        instance_id="ralph-a",
        claimed_at=datetime(2026, 5, 31, 12, 34, 56, tzinfo=UTC),
        hostname="box-a",
    )
    write_claim(pbi_dir, info)
    assert (pbi_dir / CLAIM_FILENAME).is_file()
    assert read_claim(pbi_dir) == info


def test_read_missing_returns_none(tmp_path: Path) -> None:
    pbi_dir = tmp_path / "WI-1234"
    pbi_dir.mkdir()
    assert read_claim(pbi_dir) is None


def test_read_malformed_raises(tmp_path: Path) -> None:
    pbi_dir = tmp_path / "WI-1234"
    pbi_dir.mkdir()
    (pbi_dir / CLAIM_FILENAME).write_text("not json", encoding="utf-8")
    with pytest.raises(ClaimParseError):
        read_claim(pbi_dir)


def test_read_missing_field_raises(tmp_path: Path) -> None:
    pbi_dir = tmp_path / "WI-1234"
    pbi_dir.mkdir()
    (pbi_dir / CLAIM_FILENAME).write_text(
        '{"instance_id": "ralph-a"}', encoding="utf-8"
    )
    with pytest.raises(ClaimParseError, match="claimed_at"):
        read_claim(pbi_dir)
```

- [ ] **Step 2: FAIL**

- [ ] **Step 3: Implement** — `ralph_executor/claim.py`:

```python
"""``CLAIM.json`` schema and IO helpers.

Each PBI in ``.ralph/current/<id>/`` carries a ``CLAIM.json`` naming the
ralph instance that owns it. Written atomically with the
``git mv inbox/<id>/ current/<id>/`` via the ``_move`` ``post_mv`` hook.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

CLAIM_FILENAME = "CLAIM.json"


class ClaimParseError(ValueError):
    """Raised when ``CLAIM.json`` is malformed or missing required fields."""


@dataclass(frozen=True)
class ClaimInfo:
    instance_id: str
    claimed_at: datetime
    hostname: str


def write_claim(pbi_dir: Path, info: ClaimInfo) -> Path:
    payload = {
        "instance_id": info.instance_id,
        "claimed_at": info.claimed_at.isoformat(),
        "hostname": info.hostname,
    }
    path = pbi_dir / CLAIM_FILENAME
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def read_claim(pbi_dir: Path) -> ClaimInfo | None:
    path = pbi_dir / CLAIM_FILENAME
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ClaimParseError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ClaimParseError(f"{path}: top level must be an object")
    try:
        instance_id = raw["instance_id"]
        claimed_at_raw = raw["claimed_at"]
        hostname = raw["hostname"]
    except KeyError as exc:
        raise ClaimParseError(f"{path}: missing required field {exc}") from exc
    if not isinstance(instance_id, str) or not instance_id:
        raise ClaimParseError(f"{path}: instance_id must be a non-empty string")
    if not isinstance(claimed_at_raw, str):
        raise ClaimParseError(f"{path}: claimed_at must be an ISO-8601 string")
    try:
        claimed_at = datetime.fromisoformat(claimed_at_raw)
    except ValueError as exc:
        raise ClaimParseError(f"{path}: claimed_at not ISO-8601: {exc}") from exc
    if not isinstance(hostname, str):
        raise ClaimParseError(f"{path}: hostname must be a string")
    return ClaimInfo(instance_id=instance_id, claimed_at=claimed_at, hostname=hostname)
```

- [ ] **Step 4: PASS** — 4 tests.

- [ ] **Step 5: Commit**

```
git add ralph_executor/claim.py tests/executor/test_claim.py
git commit -m "multi-ralph: CLAIM.json IO helpers"
```

---

## Task T10: Atomic claim via `_move` `post_mv` hook — **confidence: 91%**

**Files:** `ralph_executor/queue/movements.py`, `ralph_executor/loop.py` (modify), `tests/executor/test_loop.py` (extend)

**Step 0 mitigations (verified):**
- `_move` calls `git_ops.commit_all` after `_rewrite_status`. A `post_mv` callback that writes `CLAIM.json` into the destination dir BEFORE `commit_all` lets `git add -A` stage the new file in the same commit.
- This avoids the v1 plan's bug: writing CLAIM.json into the inbox dir BEFORE `git mv` would not have travelled with the mv (untracked files are left behind by `git mv` on a directory).

- [ ] **Step 1: Failing test**

```python
def test_claim_writes_claim_json_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess
    from ralph_executor.claim import read_claim
    from ralph_executor.loop import _queue_repo_root, iterate_once

    cfg = _build_two_repo_fixture(tmp_path, instance_id="ralph-a", inbox_id="WI-1234")
    iterate_once(cfg)
    queue = _queue_repo_root(cfg)
    pbi_dir = queue / ".ralph" / "current" / "WI-1234"
    assert pbi_dir.is_dir()
    claim = read_claim(pbi_dir)
    assert claim is not None and claim.instance_id == "ralph-a"
    log_out = subprocess.run(
        ["git", "-C", str(queue), "log", "-1", "--name-only", "--format=%H"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "CLAIM.json" in log_out
    assert "current/WI-1234" in log_out
```

Helper `_build_two_repo_fixture` (in test conftest or top of module):

```python
def _build_two_repo_fixture(
    tmp_path: Path, *, instance_id: str, inbox_id: str
) -> ExecutorConfig:
    """Init bare queue origin + clone with one PBI in inbox/; return ExecutorConfig.

    Monkeypatches target_clone.ensure_clone and worktree.ensure_worktree
    to no-op stubs so iterate_once does not touch GitHub.
    """
    # 1. git init --bare tmp_path/origin.git
    # 2. clone into tmp_path/seed; scaffold .ralph/inbox/<id>/{PBI.md,HISTORY.md}
    # 3. commit + push origin ralph-queue
    # 4. clone tmp_path/wsa/queue-<instance_id> from the bare
    # 5. monkeypatch target_clone.ensure_clone / worktree.ensure_worktree
    # 6. return ExecutorConfig pointed at bare URL + tmp_path/wsa
    # See conftest.py for canonical fixture-builder shape.
```

- [ ] **Step 2: FAIL**

- [ ] **Step 3: Implementation**

a. `ralph_executor/queue/movements.py::_move` — add `post_mv` parameter:

```python
from collections.abc import Callable


def _move(
    cfg: ExecutorConfig,
    pbi: PBI,
    *,
    expected_state: PBIStatus,
    target_state: PBIStatus,
    commit_prefix: str,
    post_mv: Callable[[Path], None] | None = None,
) -> PBI:
    # ... existing precondition checks + git_ops.mv + _rewrite_status ...
    if post_mv is not None:
        post_mv(dst)
    git_ops.commit_all(
        queue_repo,
        f"{commit_prefix}: move {pbi.id} from {expected_state} to {target_state}",
    )
    git_ops.push_with_rebase(queue_repo, remote="origin", branch=cfg.queue_branch)
    return parse_pbi_directory(dst, status=target_state)
```

b. `move_inbox_to_current` — forward the parameter:

```python
def move_inbox_to_current(
    cfg: ExecutorConfig,
    pbi: PBI,
    *,
    event_log: EventLog | None = None,
    now: datetime | None = None,
    post_mv: Callable[[Path], None] | None = None,
) -> PBI:
    moved = _move(
        cfg, pbi,
        expected_state="inbox",
        target_state="current",
        commit_prefix="chore(ralph-queue)",
        post_mv=post_mv,
    )
    # ... existing event_log append ...
    return moved
```

c. `ralph_executor/loop.py::_claim_pbi_worktree` — pass the lambda:

```python
import socket
from datetime import UTC, datetime
from ralph_executor.claim import ClaimInfo, write_claim

# ... existing ensure_clone ...
event_log = open_log(_queue_repo_root(cfg))
try:
    claim_info = ClaimInfo(
        instance_id=cfg.instance_id,
        claimed_at=datetime.now(tz=UTC),
        hostname=socket.gethostname(),
    )
    moved = move_inbox_to_current(
        cfg, pbi,
        event_log=event_log,
        now=datetime.now(tz=UTC),
        post_mv=lambda dst: write_claim(dst, claim_info),
    )
finally:
    event_log.close()
```

- [ ] **Step 4: PASS + regression sweep**

- [ ] **Step 5: Commit**

```
git add ralph_executor/queue/movements.py ralph_executor/loop.py tests/executor/test_loop.py
git commit -m "multi-ralph: claim writes CLAIM.json via _move post_mv hook"
```

---

## Task T11: `current_pbi()` filters by `instance_id` — **confidence: 93%**

**Files:** `ralph_executor/queue/filesystem.py` (modify), `tests/executor/test_filesystem_queue.py` (extend)

**Step 0 mitigation:** Existing tests assert "raises on multi-PBI in current/" — update those in the same task to match the new "exactly one per instance" invariant.

- [ ] **Step 1: Failing tests**

```python
def test_current_pbi_returns_own_claim_only(tmp_path: Path) -> None:
    import dataclasses
    from datetime import UTC, datetime

    from ralph_executor.claim import ClaimInfo, write_claim
    from ralph_executor.queue.filesystem import FilesystemQueueSource

    cfg = _make_fake_config(repo_path=tmp_path, instance_id="ralph-a")
    cfg = dataclasses.replace(cfg, workspace_root=tmp_path)
    current = tmp_path / "queue-ralph-a" / ".ralph" / "current"
    now = datetime(2026, 5, 31, tzinfo=UTC)
    _make_pbi_dir(current / "WI-1234", pbi_id="WI-1234", pbi_type="feature")
    _make_pbi_dir(current / "WI-5678", pbi_id="WI-5678", pbi_type="feature")
    write_claim(current / "WI-1234", ClaimInfo(instance_id="ralph-a", claimed_at=now, hostname="a"))
    write_claim(current / "WI-5678", ClaimInfo(instance_id="ralph-b", claimed_at=now, hostname="b"))
    pbi = FilesystemQueueSource(cfg).current_pbi()
    assert pbi is not None and pbi.id == "WI-1234"


def test_current_pbi_none_when_only_foreign_claims(tmp_path: Path) -> None:
    # Same shape, only ralph-b CLAIM.json. Assert None.


def test_current_pbi_raises_on_two_own_claims(tmp_path: Path) -> None:
    import pytest
    # Two PBIs both ralph-a CLAIM. Assert QueueError.


def test_current_pbi_skips_no_claim_file(tmp_path: Path) -> None:
    # PBI dir without CLAIM.json. Assert None (legacy / mid-migration).
```

Test helper `_make_pbi_dir` writes minimal `PBI.md` with valid frontmatter — copy from existing conftest.

- [ ] **Step 2: FAIL**

- [ ] **Step 3: Implementation** — replace `FilesystemQueueSource.current_pbi`:

```python
    def current_pbi(self) -> PBI | None:
        """Return THIS instance's claimed PBI, or None.

        Scans ``.ralph/current/*/CLAIM.json``. Foreign claims and PBI dirs
        without a CLAIM.json are skipped. Multiple own claims → QueueError.
        """
        from ralph_executor.claim import ClaimParseError, read_claim

        own: list[PBI] = []
        for pbi in self._list_pbis("current"):
            try:
                claim = read_claim(pbi.path)
            except ClaimParseError as exc:
                raise QueueError(
                    f"current/{pbi.id}: malformed CLAIM.json: {exc}"
                ) from exc
            if claim is None:
                continue
            if claim.instance_id == self._config.instance_id:
                own.append(pbi)
        if not own:
            return None
        if len(own) > 1:
            ids = sorted(p.id for p in own)
            raise QueueError(
                f"current/ contains more than one PBI owned by this instance: {ids}"
            )
        return own[0]
```

- [ ] **Step 4: PASS**

- [ ] **Step 5: Commit**

```
git add ralph_executor/queue/filesystem.py tests/executor/test_filesystem_queue.py
git commit -m "multi-ralph: current_pbi filters by instance_id"
```

---

## Task T12: META-BUG carries `tripped_by_instance` — **confidence: 95%**

**Files:** `ralph_executor/safety/halt.py`, `ralph_executor/loop.py` (modify), `tests/executor/test_halt.py` (extend)

**Step 0 mitigation (verified):** `write_meta_bug` builds frontmatter as a Python list (`halt.py:143-152`). Lines are: `["---", "id:", "type:", "severity:", "status:", "created_at:", "summary:", "---", ...]`. Insert the new line at index 7 (after `summary:`, before the closing `---`).

- [ ] **Step 1: Failing test**

```python
def test_halt_and_acknowledge_records_instance_id(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from ralph_executor.safety.halt import halt_and_acknowledge

    signals = [_make_cycle_signal()]  # reuse existing test fixture helper
    meta = halt_and_acknowledge(
        repo=tmp_path,
        signals=signals,
        now=datetime(2026, 5, 31, tzinfo=UTC),
        tripped_by_instance="ralph-a",
    )
    text = meta.path.read_text(encoding="utf-8")
    assert "tripped_by_instance: ralph-a" in text
```

- [ ] **Step 2: FAIL**

- [ ] **Step 3: Implementation**

a. `ralph_executor/safety/halt.py::write_meta_bug` — accept and emit:

```python
def write_meta_bug(
    *,
    repo: Path,
    snapshot: StateSnapshot,
    signals: list[CycleSignal],
    now: datetime | None = None,
    tripped_by_instance: str = "",
) -> MetaBug:
    # ... existing setup ...
    lines: list[str] = [
        "---",
        f"id: {meta_id}",
        "type: meta",
        "severity: critical",
        "status: blocked",
        f"created_at: {created_at.isoformat()}",
        f"summary: {summary}",
    ]
    if tripped_by_instance:
        lines.append(f"tripped_by_instance: {tripped_by_instance}")
    lines.append("---")
    # ... existing remainder of body ...
```

b. `halt_and_acknowledge` — accept + forward:

```python
def halt_and_acknowledge(
    *,
    repo: Path,
    signals: list[CycleSignal],
    now: datetime | None = None,
    webhook_env: str = "RALPH_HALT_WEBHOOK",
    tripped_by_instance: str = "",
) -> MetaBug:
    snapshot = snapshot_state(repo)
    meta = write_meta_bug(
        repo=repo,
        snapshot=snapshot,
        signals=list(signals),
        now=now,
        tripped_by_instance=tripped_by_instance,
    )
    # ... existing remainder ...
```

c. `ralph_executor/loop.py::_check_cycle_detector`:

```python
    halt_and_acknowledge(
        repo=_queue_repo_root(cfg),
        signals=signals,
        now=now,
        tripped_by_instance=cfg.instance_id,
    )
```

- [ ] **Step 4: PASS**

- [ ] **Step 5: Commit**

```
git add ralph_executor/safety/halt.py ralph_executor/loop.py tests/executor/test_halt.py
git commit -m "multi-ralph: META-BUG carries tripped_by_instance"
```

---

## Task T13: Cross-platform workspace lockfile — **confidence: 88%**

**Files:** `ralph_executor/lockfile.py` (new), `tests/executor/test_lockfile.py` (new)

**Step 0 mitigations:**
- POSIX uses `fcntl.flock` (whole-file advisory lock). Windows uses `msvcrt.locking` which locks N bytes from the current file position; seek to byte 0 first, lock 1 byte. Position reset is critical — without it the lock targets undefined offsets.
- `WorkspaceLock` opens the file in `a+b` mode (append-binary, read+write). Truncate after lock, write payload, flush.

- [ ] **Step 1: Failing tests**

```python
import json
import os
from pathlib import Path

import pytest

from ralph_executor.lockfile import LockHeldError, WorkspaceLock


def test_acquire_then_release(tmp_path: Path) -> None:
    lock = WorkspaceLock(tmp_path / "queue-a" / ".ralph.lock", instance_id="ralph-a")
    with lock:
        assert (tmp_path / "queue-a" / ".ralph.lock").is_file()


def test_second_acquire_raises_same_process(tmp_path: Path) -> None:
    path = tmp_path / "queue-a" / ".ralph.lock"
    first = WorkspaceLock(path, instance_id="ralph-a")
    second = WorkspaceLock(path, instance_id="ralph-a")
    first.acquire()
    try:
        with pytest.raises(LockHeldError):
            second.acquire()
    finally:
        first.release()


def test_lock_payload_includes_pid_and_instance(tmp_path: Path) -> None:
    path = tmp_path / "queue-a" / ".ralph.lock"
    lock = WorkspaceLock(path, instance_id="ralph-a")
    with lock:
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["instance_id"] == "ralph-a"
        assert payload["pid"] == os.getpid()
        assert "hostname" in payload
        assert "started_at" in payload
```

- [ ] **Step 2: FAIL**

- [ ] **Step 3: Implement** — `ralph_executor/lockfile.py`:

```python
"""OS-level exclusive lockfile for the per-instance workspace.

Acquired at executor startup; released by the OS on process exit even if
release() is never called. Lockfile lives at
``<workspace_root>/queue-<instance-id>/.ralph.lock`` — outside the .git
tree, never committed.
"""

from __future__ import annotations

import json
import os
import socket
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import IO


class LockHeldError(RuntimeError):
    """Raised when the lockfile is already held by another process."""


@dataclass
class WorkspaceLock:
    path: Path
    instance_id: str
    _fh: IO[bytes] | None = field(default=None, init=False, repr=False)

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+b")  # noqa: SIM115 — released in release()
        try:
            if sys.platform == "win32":
                import msvcrt

                fh.seek(0)
                try:
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError as exc:
                    raise LockHeldError(
                        f"another process already holds {self.path}"
                    ) from exc
            else:
                import fcntl

                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    raise LockHeldError(
                        f"another process already holds {self.path}"
                    ) from exc
        except BaseException:
            fh.close()
            raise

        self._fh = fh
        payload = {
            "instance_id": self.instance_id,
            "hostname": socket.gethostname(),
            "pid": os.getpid(),
            "started_at": datetime.now(tz=UTC).isoformat(),
        }
        self._fh.seek(0)
        self._fh.truncate()
        self._fh.write((json.dumps(payload, indent=2) + "\n").encode("utf-8"))
        self._fh.flush()

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt

                self._fh.seek(0)
                try:
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            else:
                import fcntl

                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            self._fh.close()
            self._fh = None

    def __enter__(self) -> "WorkspaceLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()
```

- [ ] **Step 4: PASS**

- [ ] **Step 5: Commit**

```
git add ralph_executor/lockfile.py tests/executor/test_lockfile.py
git commit -m "multi-ralph: cross-platform workspace lockfile"
```

---

## Task T14: CLI acquires the lockfile — **confidence: 92%**

**Files:** `ralph_executor/cli.py` (modify), `tests/executor/test_cli.py` (extend)

**Step 0 mitigation:** Acquire AFTER `_apply_overrides` (need final `workspace_root` + `instance_id`), BEFORE `prepare_host_environment`. Wrap the iteration-loop body in `try/finally` so the lock releases on every exit path including the exception handler at the bottom of `main`.

- [ ] **Step 1: Failing test**

```python
def test_main_exits_when_lockfile_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from ralph_executor.cli import main
    from ralph_executor.lockfile import WorkspaceLock
    from ralph_executor.queue_path import queue_clone_path

    _prepare_user_config(tmp_path, queue_repo="https://github.com/owner/queue",
                         instance_id="ralph-a")
    _prepare_project_repo(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("RALPH_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.chdir(tmp_path / "project")

    lock_path = queue_clone_path(tmp_path / "ws", "ralph-a") / ".ralph.lock"
    other = WorkspaceLock(lock_path, instance_id="ralph-a")
    other.acquire()
    try:
        rc = main(["--once"])
        assert rc == 2
        assert "another ralph already running" in capsys.readouterr().err.lower()
    finally:
        other.release()
```

- [ ] **Step 2: FAIL**

- [ ] **Step 3: Implementation** — in `ralph_executor/cli.py::main`, after the `_configure_logging(cfg.log_level)` line:

```python
    log.info(
        "ralph-executor starting (repo=%s queue_repo=%s queue_branch=%s instance=%s)",
        cfg.repo_path, cfg.queue_repo, cfg.queue_branch, cfg.instance_id,
    )

    from ralph_executor.lockfile import LockHeldError, WorkspaceLock
    from ralph_executor.queue_path import queue_clone_path

    lock_path = queue_clone_path(cfg.workspace_root, cfg.instance_id) / ".ralph.lock"
    lock = WorkspaceLock(lock_path, instance_id=cfg.instance_id)
    try:
        lock.acquire()
    except LockHeldError as exc:
        print(
            f"error: another ralph already running on this workspace: {exc}",
            file=sys.stderr,
        )
        return 2

    try:
        # ... existing GH_OWNER / ADO_ORG_URL env bridge ...
        # ... existing prepare_host_environment ...
        # ... existing iteration loop body, returning 0 on clean exit ...
        return 0
    finally:
        lock.release()
```

- [ ] **Step 4: PASS**

- [ ] **Step 5: Commit**

```
git add ralph_executor/cli.py tests/executor/test_cli.py
git commit -m "multi-ralph: cli acquires workspace lockfile at startup"
```

---

## Task TA: `scripts/queue_writer` — `resolve_instance_id` + namespaced `acquire_queue_clone` — **confidence: 90%**

**Files:** `scripts/queue_writer.py` (modify), `tests/scripts/test_queue_writer.py` (extend / create)

**Step 0 mitigation (verified):** Existing resolvers (`resolve_workspace_root` `:95`, `resolve_queue_repo` `:123`, `resolve_queue_branch` `:158`) all share the same shape: CLI → user TOML → default. Mirror exactly. `acquire_queue_clone` `:73` forwards to `ensure_queue_clone` — T7 has already taught the latter to accept `dest`.

- [ ] **Step 1: Failing tests**

```python
import pytest

from scripts.queue_writer import QueueWriterError, resolve_instance_id


def test_resolve_instance_id_cli_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "ralph_executor.user_config.read_instance_id", lambda: "ralph-toml"
    )
    assert resolve_instance_id("ralph-cli") == "ralph-cli"


def test_resolve_instance_id_falls_back_to_toml(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "ralph_executor.user_config.read_instance_id", lambda: "ralph-toml"
    )
    assert resolve_instance_id(None) == "ralph-toml"


def test_resolve_instance_id_falls_back_to_hostname(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "ralph_executor.user_config.read_instance_id", lambda: None
    )
    monkeypatch.setattr("socket.gethostname", lambda: "MyBox")
    assert resolve_instance_id(None) == "mybox"


def test_resolve_instance_id_empty_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "ralph_executor.user_config.read_instance_id", lambda: None
    )
    monkeypatch.setattr("socket.gethostname", lambda: "")
    with pytest.raises(QueueWriterError, match="instance_id"):
        resolve_instance_id(None)
```

- [ ] **Step 2: FAIL**

- [ ] **Step 3: Implementation** — append to `scripts/queue_writer.py`:

```python
def resolve_instance_id(cli_value: str | None = None) -> str:
    """Resolve ``instance_id`` for operator skills.

    Order: CLI flag → user TOML → sanitised hostname.
    """
    from ralph_executor.config import ConfigError
    from ralph_executor.identity import (
        InstanceIdError,
        default_instance_id,
        sanitize_instance_id,
        validate_instance_id,
    )
    from ralph_executor.user_config import read_instance_id

    if cli_value is not None:
        candidate = sanitize_instance_id(cli_value)
        try:
            return validate_instance_id(candidate)
        except InstanceIdError as exc:
            raise QueueWriterError(f"--instance-id: {exc}") from exc
    try:
        from_toml = read_instance_id()
    except ConfigError as exc:
        raise QueueWriterError(str(exc)) from exc
    candidate = from_toml or default_instance_id()
    try:
        return validate_instance_id(sanitize_instance_id(candidate))
    except InstanceIdError as exc:
        raise QueueWriterError(
            f"instance_id not resolvable ({exc}); pass --instance-id or set it via "
            "`ralph-executor init`"
        ) from exc
```

Modify `acquire_queue_clone`:

```python
def acquire_queue_clone(
    workspace_root: Path,
    queue_repo: str,
    queue_branch: str,
    instance_id: str,
    *,
    timeout: float = 120.0,
) -> Path:
    """Idempotent queue clone for operator skills (per-instance namespaced)."""
    from ralph_executor.queue_path import queue_clone_path

    dest = queue_clone_path(workspace_root, instance_id)
    try:
        return ensure_queue_clone(
            workspace_root, queue_repo, queue_branch, dest=dest, timeout=timeout
        )
    except QueueCloneError as exc:
        raise QueueWriterError(str(exc)) from exc
```

- [ ] **Step 4: PASS**

- [ ] **Step 5: Commit**

```
git add scripts/queue_writer.py tests/scripts/test_queue_writer.py
git commit -m "multi-ralph: queue_writer resolve_instance_id + namespaced acquire_queue_clone"
```

---

## Task T15: `ralph-status` OWNER column + `--instance-id` — **confidence: 90%**

**Files:** `skills/ralph-status/scripts/status.py` (modify), `scripts/pbi_reader.py` (modify), `tests/skills/test_ralph_status.py` (extend)

**Step 0 mitigation (verified):** `status.py:218` calls `acquire_queue_clone(workspace_root, queue_repo, queue_branch)` — must add the `instance_id` argument here. `_COLUMN_ORDER` (`status.py:81`) lists 7 columns; add OWNER between TYPE and SEVERITY (index 4). Extend `PBIRow` in `scripts/pbi_reader.py` with `owner: str | None = None`; populate it for current/ rows in `enumerate_state`.

- [ ] **Step 1: Failing test**

```python
def test_status_shows_owner_column(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 1. Build a queue clone at tmp_path/queue-ralph-a/.ralph/current/WI-1/
    #    with valid PBI.md frontmatter + CLAIM.json owned by ralph-a.
    # 2. Monkeypatch resolve_* to return the test paths + queue_repo URL.
    # 3. Call status.main(["--workspace", str(tmp_path), "--instance-id", "ralph-a",
    #                      "--queue-repo", "https://x", "--no-pull-mock-fixture"]).
    # 4. Capture stdout; assert "OWNER" in header line and "ralph-a" in WI-1 row.
```

- [ ] **Step 2: FAIL**

- [ ] **Step 3: Implementation**

a. `skills/ralph-status/scripts/status.py::_parse_args` — add flag:

```python
    parser.add_argument(
        "--instance-id",
        dest="instance_id",
        help="Override instance_id from ~/.ralph/config.toml.",
    )
```

b. `main` — resolve + pass:

```python
    workspace_root = resolve_workspace_root(args.workspace)
    queue_repo = resolve_queue_repo(args.queue_repo)
    queue_branch = resolve_queue_branch(args.queue_branch)
    instance_id = resolve_instance_id(args.instance_id)
    queue_clone = acquire_queue_clone(
        workspace_root, queue_repo, queue_branch, instance_id
    )
```

Update the import block at the top of `status.py` to include `resolve_instance_id`.

c. `_COLUMN_ORDER` — add OWNER:

```python
_COLUMN_ORDER: tuple[str, ...] = (
    "TARGET", "STATE", "ID", "TYPE", "OWNER", "SEVERITY", "AGE", "TITLE",
)
```

d. `scripts/pbi_reader.py::PBIRow` dataclass — add field:

```python
@dataclass
class PBIRow:
    # ... existing fields ...
    owner: str | None = None
```

In `enumerate_state`, when `state == "current"`, attempt `read_claim(pbi_dir)`; populate `owner` to `claim.instance_id`, or to the literal string `"<malformed>"` on `ClaimParseError`, or `None` on missing claim.

e. `status.py::_row_to_cells` — emit OWNER cell at the new index:

```python
    return [
        _truncate_target(row.target_repo) if row.target_repo else "?",
        row.state,
        row.pbi_id,
        row.pbi_type,
        row.owner or "—",
        row.severity,
        _age_string(row.created_at),
        row.title,
    ]
```

Same alignment in `_row_to_json` (`"owner": row.owner`).

- [ ] **Step 4: PASS**

- [ ] **Step 5: Commit**

```
git add skills/ralph-status/scripts/status.py scripts/pbi_reader.py \
        tests/skills/test_ralph_status.py
git commit -m "multi-ralph: ralph-status OWNER column + --instance-id flag"
```

---

## Task T16: `ralph-cancel` refuses foreign CLAIM — **confidence: 92%**

**Files:** `skills/ralph-cancel/scripts/cancel.py` (modify), `tests/skills/test_ralph_cancel.py` (extend)

**Step 0 mitigation (verified):** `cancel.py:138` calls `_resolve_current_pbi(clone, args.pbi_id)` returning the dir. Insert ownership check immediately after. Add `--instance-id` flag, thread through `acquire_queue_clone`.

- [ ] **Step 1: Failing test**

```python
def test_cancel_refuses_foreign_claim(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    # 1. Build queue-ralph-a/.ralph/current/WI-1/ with CLAIM.json owned by ralph-b.
    # 2. Call cancel.main(["--pbi-id", "WI-1", "--instance-id", "ralph-a", ...]).
    # 3. Assert rc == 2 and "claimed by" / "ralph-b" in capsys.err.
```

- [ ] **Step 2: FAIL**

- [ ] **Step 3: Implementation**

a. Add `--instance-id` argument to `_parse_args`.

b. In `main`, resolve + pass to `acquire_queue_clone(..., instance_id)`.

c. After `_resolve_current_pbi`:

```python
        from ralph_executor.claim import ClaimParseError, read_claim

        try:
            claim = read_claim(pbi_dir)
        except ClaimParseError as exc:
            raise QueueWriterError(f"malformed CLAIM.json: {exc}") from exc
        if claim is not None and claim.instance_id != instance_id:
            raise QueueWriterError(
                f"PBI {args.pbi_id} is claimed by instance {claim.instance_id!r}, "
                f"not {instance_id!r}. Use `ralph-recover` if you need to force."
            )
```

Update the import block to include `resolve_instance_id`.

- [ ] **Step 4: PASS**

- [ ] **Step 5: Commit**

```
git add skills/ralph-cancel/scripts/cancel.py tests/skills/test_ralph_cancel.py
git commit -m "multi-ralph: ralph-cancel refuses foreign CLAIM + --instance-id flag"
```

---

## Task T17: `ralph-promote` refuses cross-instance transfer — **confidence: 91%**

**Files:** `skills/ralph-promote/scripts/promote.py` (modify), `tests/skills/test_ralph_promote.py` (extend)

**Step 0 mitigation:** Read `skills/ralph-promote/scripts/promote.py` to find the from-state-current branch. Insert the ownership check there. Add `--instance-id` flag.

- [ ] **Steps 1–4** — same pattern as T16, applied only when moving OUT OF `current/`.

- [ ] **Step 5: Commit**

```
git add skills/ralph-promote/scripts/promote.py tests/skills/test_ralph_promote.py
git commit -m "multi-ralph: ralph-promote refuses cross-instance current/ transfer + --instance-id flag"
```

---

## Task T18: `ralph-recover` skill — **confidence: 87%**

**Files:** `skills/ralph-recover/SKILL.md`, `skills/ralph-recover/scripts/__init__.py`, `skills/ralph-recover/scripts/recover.py` (new); `tests/skills/test_ralph_recover.py` (new)

**Step 0 mitigations:**
- Use `commit_paths` + `push` from `scripts/queue_writer` (already used by `ralph-cancel`) — no raw subprocess git, no shutil-fallback branch (avoids the partial-state risk in v1).
- Frontmatter rewrites go through a single `_rewrite_lines` helper that handles `status:`, `attempts:`, `updated_at:` — not free-form regex. Mirrors `_rewrite_status` in `ralph_executor.queue.movements`.

- [ ] **Step 1: Failing tests**

```python
def test_recover_to_inbox_moves_pbi_and_resets_attempts(tmp_path: Path) -> None:
    # Setup current/WI-1234 with foreign CLAIM, attempts: 5.
    # recover.main(["--pbi-id", "WI-1234", "--to", "inbox", ...]).
    # Assert: inbox/WI-1234 exists, no CLAIM.json, attempts: 0 in PBI.md, status: inbox.


def test_recover_refuses_if_not_in_current(tmp_path: Path) -> None: ...
def test_recover_refuses_when_halt_active(tmp_path: Path) -> None: ...
def test_recover_refuses_destination_collision(tmp_path: Path) -> None: ...
def test_recover_appends_history_note(tmp_path: Path) -> None: ...
```

- [ ] **Step 2: FAIL**

- [ ] **Step 3: Implementation** — `skills/ralph-recover/scripts/recover.py`:

```python
"""ralph-recover — manual claim recovery escape hatch."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from ralph_executor.claim import CLAIM_FILENAME, read_claim  # noqa: E402
from scripts.queue_writer import (  # noqa: E402
    QueueWriterError,
    acquire_queue_clone,
    commit_paths,
    push,
    resolve_instance_id,
    resolve_queue_branch,
    resolve_queue_repo,
    resolve_workspace_root,
)


DESTINATIONS = ("inbox", "blocked")
_ENTRY_FILENAMES = ("PBI.md", "BUG.md", "FEEDBACK.md")


@dataclass
class RecoverResult:
    pbi_id: str
    from_state: str
    to_state: str
    previous_owner: str | None
    commit_sha: str
    pushed: bool


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="ralph-recover")
    parser.add_argument("--pbi-id", required=True, dest="pbi_id")
    parser.add_argument("--to", choices=DESTINATIONS, required=True, dest="destination")
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--queue-repo", dest="queue_repo")
    parser.add_argument("--queue-branch", dest="queue_branch")
    parser.add_argument("--instance-id", dest="instance_id")
    parser.add_argument("--no-push", action="store_true")
    return parser.parse_args(argv)


def _find_entry_file(pbi_dir: Path) -> Path:
    for name in _ENTRY_FILENAMES:
        cand = pbi_dir / name
        if cand.is_file():
            return cand
    raise QueueWriterError(f"PBI {pbi_dir.name}: no entry file found")


def _rewrite_lines(
    entry_file: Path, *, status: str | None, attempts: int | None
) -> None:
    text = entry_file.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise QueueWriterError(f"{entry_file}: no opening frontmatter fence")
    end = -1
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end = idx
            break
    if end < 0:
        raise QueueWriterError(f"{entry_file}: no closing frontmatter fence")

    now_iso = datetime.now(tz=UTC).replace(microsecond=0).isoformat()
    saw_status = saw_attempts = saw_updated = False
    for idx in range(1, end):
        stripped = lines[idx].lstrip()
        if status is not None and stripped.startswith("status:"):
            lines[idx] = f"status: {status}\n"
            saw_status = True
        elif attempts is not None and stripped.startswith("attempts:"):
            lines[idx] = f"attempts: {attempts}\n"
            saw_attempts = True
        elif stripped.startswith("updated_at:"):
            lines[idx] = f"updated_at: {now_iso}\n"
            saw_updated = True
    if status is not None and not saw_status:
        lines.insert(end, f"status: {status}\n")
        end += 1
    if attempts is not None and not saw_attempts:
        lines.insert(end, f"attempts: {attempts}\n")
        end += 1
    if not saw_updated:
        lines.insert(end, f"updated_at: {now_iso}\n")
    entry_file.write_text("".join(lines), encoding="utf-8")


def _append_history(pbi_dir: Path, *, previous_owner: str) -> Path:
    history = pbi_dir / "HISTORY.md"
    now_iso = datetime.now(tz=UTC).replace(microsecond=0).isoformat()
    note = (
        f"\n## Recovered from instance {previous_owner} by operator — {now_iso}\n"
    )
    existing = history.read_text(encoding="utf-8") if history.is_file() else ""
    history.write_text(existing + note, encoding="utf-8")
    return history


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    try:
        workspace_root = resolve_workspace_root(args.workspace)
        queue_repo = resolve_queue_repo(args.queue_repo)
        queue_branch = resolve_queue_branch(args.queue_branch)
        instance_id = resolve_instance_id(args.instance_id)
        clone = acquire_queue_clone(workspace_root, queue_repo, queue_branch, instance_id)

        halt = clone / ".ralph" / "state" / "halted"
        if halt.is_file():
            raise QueueWriterError("halt sentinel is active; refusing to operate")

        src_dir = clone / ".ralph" / "current" / args.pbi_id
        dst_dir = clone / ".ralph" / args.destination / args.pbi_id
        if not src_dir.is_dir():
            raise QueueWriterError(f"PBI {args.pbi_id!r} not found in current/")
        if dst_dir.exists():
            raise QueueWriterError(
                f"destination already exists at {dst_dir.relative_to(clone)}"
            )

        previous_owner: str | None = None
        claim = read_claim(src_dir)
        if claim is not None:
            previous_owner = claim.instance_id

        claim_file = src_dir / CLAIM_FILENAME
        if claim_file.is_file():
            claim_file.unlink()
        dst_dir.parent.mkdir(parents=True, exist_ok=True)
        src_dir.rename(dst_dir)

        entry = _find_entry_file(dst_dir)
        if args.destination == "inbox":
            _rewrite_lines(entry, status="inbox", attempts=0)
        else:
            _rewrite_lines(entry, status="blocked", attempts=None)
        _append_history(dst_dir, previous_owner=previous_owner or "<no claim>")

        commit_sha = commit_paths(
            clone,
            [src_dir, dst_dir],
            f"chore(queue): recover {args.pbi_id} from {previous_owner or '<no claim>'}",
        )
        pushed = False
        if not args.no_push:
            push(clone, queue_branch)
            pushed = True

        print(json.dumps(asdict(RecoverResult(
            pbi_id=args.pbi_id,
            from_state="current",
            to_state=args.destination,
            previous_owner=previous_owner,
            commit_sha=commit_sha,
            pushed=pushed,
        )), indent=2, sort_keys=True))
        return 0
    except QueueWriterError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
```

`SKILL.md` mirrors `skills/ralph-cancel/SKILL.md`.

- [ ] **Step 4: PASS** — 5 tests.

- [ ] **Step 5: Commit**

```
git add skills/ralph-recover/ tests/skills/test_ralph_recover.py
git commit -m "multi-ralph: ralph-recover manual claim-recovery skill"
```

---

## Task T19: Two-instance integration tests — **confidence: 88%**

**Files:** `tests/executor/test_multi_ralph_integration.py` (new)

**Step 0 mitigations:**
- Concrete fixture, not `NotImplementedError`. Steps explicit below.
- T2 (claim-race) assertion tightened: post-recovery, loser's `current/<own-id>/` is empty AND winner's CLAIM.json on disk in the loser's clone after pull.

- [ ] **Step 1: Failing tests**

```python
"""Cross-instance simulation: two ExecutorConfigs against one bare queue."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ralph_executor.claim import read_claim
from ralph_executor.config import ExecutorConfig
from ralph_executor.loop import HaltedError, iterate_once
from ralph_executor.queue_path import queue_clone_path


@pytest.fixture
def two_ralphs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[ExecutorConfig, ExecutorConfig, Path]:
    """Bare queue origin + two configs (ralph-a, ralph-b) pointed at it.

    Builds an origin with .ralph/inbox/{WI-1, WI-2}, both with valid
    PBI.md frontmatter + HISTORY.md. Monkeypatches target_clone and
    worktree helpers to no-ops so iterate_once does not touch GitHub.
    """
    # 1. Bare origin
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", str(origin)], check=True)
    # 2. Seed clone, scaffold .ralph/inbox/{WI-1,WI-2}, push ralph-queue
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", str(origin), str(seed)], check=True)
    # ... scaffold helper ...
    # 3. Two ExecutorConfigs
    cfg_a = _build_cfg(tmp_path / "wsa", "ralph-a", str(origin))
    cfg_b = _build_cfg(tmp_path / "wsb", "ralph-b", str(origin))
    # 4. Monkeypatch
    from ralph_executor import target_clone, worktree
    monkeypatch.setattr(target_clone, "ensure_clone", _fake_ensure_clone)
    monkeypatch.setattr(worktree, "ensure_worktree", lambda *a, **kw: None)
    return cfg_a, cfg_b, origin


def test_T1_two_ralphs_claim_distinct_pbis(two_ralphs) -> None:
    cfg_a, cfg_b, _ = two_ralphs
    iterate_once(cfg_a)
    iterate_once(cfg_b)
    qa = queue_clone_path(cfg_a.workspace_root, cfg_a.instance_id) / ".ralph" / "current"
    qb = queue_clone_path(cfg_b.workspace_root, cfg_b.instance_id) / ".ralph" / "current"
    owners = set()
    for cur in (qa, qb):
        for pbi in cur.iterdir():
            claim = read_claim(pbi)
            if claim is not None:
                owners.add(claim.instance_id)
    assert owners == {"ralph-a", "ralph-b"}


def test_T2_claim_race_loser_recovers(two_ralphs, tmp_path: Path) -> None:
    cfg_a, cfg_b, origin = two_ralphs
    _strip_inbox_to_one(origin, keep="WI-1")
    iterate_once(cfg_a)
    iterate_once(cfg_b)
    qb = queue_clone_path(cfg_b.workspace_root, cfg_b.instance_id) / ".ralph" / "current"
    assert not any(qb.iterdir())  # ralph-b has no own claim


def test_T4_halt_visible_to_other_instance(two_ralphs) -> None:
    cfg_a, cfg_b, origin = two_ralphs
    # commit a halt sentinel into origin and assert ralph-b raises HaltedError
    # ...


def test_T5_lockfile_blocks_second_process(tmp_path: Path) -> None:
    from ralph_executor.lockfile import LockHeldError, WorkspaceLock

    path = tmp_path / "queue-ralph-a" / ".ralph.lock"
    a = WorkspaceLock(path, instance_id="ralph-a")
    b = WorkspaceLock(path, instance_id="ralph-a")
    a.acquire()
    try:
        with pytest.raises(LockHeldError):
            b.acquire()
    finally:
        a.release()
```

(Helper functions `_build_cfg`, `_fake_ensure_clone`, `_strip_inbox_to_one` are implemented in the same module — bodies straightforward; the fixture comment lists exact steps.)

- [ ] **Step 2: FAIL on fixture**

- [ ] **Step 3: Fill in fixture body + helpers** to match the comment.

- [ ] **Step 4: PASS** — T1, T2, T4, T5. T3 (sweep) covered by existing sweep unit tests; not duplicated here.

- [ ] **Step 5: Commit**

```
git add tests/executor/test_multi_ralph_integration.py
git commit -m "multi-ralph: cross-instance integration tests (T1, T2, T4, T5)"
```

---

## Task T20: Documentation — **confidence: 96%**

**Files:** `README.md`, `docs/runbooks/ralph-setup.md` (modify)

- [ ] **Step 1: README** — replace the "Running multiple ralphs" section. New content covers: identity resolution chain, workspace path namespacing, CLAIM.json ownership marker, `--instance-id` on every operator skill, `.ralph.lock`, upgrade procedure ("drain current/ first").

- [ ] **Step 2: Runbook** — add `instance_id` to the config-knob table. Document `--instance-id` on every operator skill. Document `.ralph.lock` location.

- [ ] **Step 3: Commit**

```
git add README.md docs/runbooks/ralph-setup.md
git commit -m "multi-ralph: docs — README + runbook updates"
```

---

## Final sweep

- [ ] `uv run pytest -v` — full suite green.
- [ ] `uv run ruff check . && uv run ruff format --check .` — clean.
- [ ] `uv run mypy ralph_executor scripts skills tests` — clean.
- [ ] `git push origin main`.

---

## Confidence summary

| Task | % | Key risk | Mitigation baked in |
|---|---|---|---|
| T1 identity | 96 | regex edge cases | tests exhaustive |
| T2 user_config | 94 | rewrite preserves siblings | verified pattern, test asserts coexistence |
| T3 config field | 93 | resolver source-label confusion | Step 0 re-read load_config tail |
| T4 CLI flag | 96 | low | direct |
| T5 init prompt | 92 | exact insertion point | Step 0 traced cmd_init flow lines |
| T6 queue_clone_path | 99 | trivial | direct |
| T7 route callers | 91 | fixture sweep | Step 0 enumerated 4 sites + test fixtures |
| T8 legacy rename | 92 | cross-volume | `shutil.move` + both-exist guard before rename |
| T9 CLAIM.json IO | 96 | low | direct |
| T10 atomic claim | 91 | `git mv` + untracked file gotcha | redesigned to `post_mv` callback inside `_move` |
| T11 current_pbi filter | 93 | existing test churn | bundled fix |
| T12 META-BUG | 95 | frontmatter index | concrete `lines.append` slot |
| T13 lockfile | 88 | Windows specifics | seek-then-lock, payload truncate, OS-release contract |
| T14 CLI lockfile | 92 | wrapping `main` body | explicit try/finally |
| TA queue_writer | 90 | skill resolver layer | mirror existing resolvers exactly |
| T15 status OWNER | 90 | PBIRow extension | explicit dataclass field + per-state population |
| T16 cancel refuse | 92 | check placement | post-`_resolve_current_pbi` |
| T17 promote refuse | 91 | branch placement | mirror T16, only on from-current path |
| T18 recover skill | 87 | many edge cases | use queue_writer helpers, single atomic commit, `_rewrite_lines` not regex |
| T19 integration | 88 | fixture build | concrete steps inline, tightened race assertion |
| T20 docs | 96 | low | direct |

All tasks ≥87%. All sub-95% tasks carry baked-in mitigations.
