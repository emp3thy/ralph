# Multi-ralph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable N `ralph-executor` instances to drain the same `queue_repo` concurrently. Each instance is identified by a stable, operator-supplied `instance_id`. Workspace path becomes `<workspace_root>/queue-<instance-id>/`. Every claimed PBI carries a `CLAIM.json` naming its owner. Coordination is purely via the existing git push-with-rebase machinery on the queue branch.

**Architecture:** Add a single `instance_id` knob to `ExecutorConfig` (resolved from CLI > env > TOML > sanitised hostname). Introduce a `queue_clone_path(workspace_root, instance_id)` helper used everywhere the queue clone path is needed. Atomically commit `CLAIM.json` with the existing `git mv inbox/<id>/ current/<id>/` so claim ownership is part of the same push. Replace the "exactly one PBI in current/" invariant with "exactly one PBI in current/ owned by this instance". An OS-level lockfile on the workspace prevents same-host double-launches. A new `ralph-recover` skill is the operator escape hatch for stuck claims.

**Tech Stack:** Python 3.12 stdlib (`fcntl` / `msvcrt` for locks, `socket.gethostname` for default id, `json` for `CLAIM.json`). No new runtime dependencies.

**Spec:** `docs/superpowers/specs/2026-05-31-multi-ralph-design.md`

---

## File Structure

### New files

- `ralph_executor/identity.py` — `sanitize_instance_id`, `INSTANCE_ID_REGEX`, default-from-hostname helper.
- `ralph_executor/lockfile.py` — `WorkspaceLock` cross-platform OS lock, with context-manager interface.
- `ralph_executor/claim.py` — `CLAIM.json` read/write helpers + `ClaimInfo` dataclass.
- `ralph_executor/queue_path.py` — `queue_clone_path(workspace_root, instance_id) -> Path` single source of truth.
- `skills/ralph-recover/SKILL.md` + `skills/ralph-recover/scripts/__init__.py` + `skills/ralph-recover/scripts/recover.py` — manual claim-recovery skill.
- `tests/executor/test_identity.py`
- `tests/executor/test_lockfile.py`
- `tests/executor/test_claim.py`
- `tests/executor/test_queue_path.py`
- `tests/executor/test_multi_ralph_integration.py` — two-instance simulation.
- `tests/skills/test_ralph_recover.py`

### Modified files

- `ralph_executor/config.py` — add `instance_id` field, resolution chain, validation in `load_config`.
- `ralph_executor/user_config.py` — add `read_instance_id`, `write_instance_id`.
- `ralph_executor/cli.py` — add `--instance-id` flag and override wiring; init prompt; acquire lockfile in `main`.
- `ralph_executor/setup_cmds.py` — `cmd_init` prompts for `instance_id`.
- `ralph_executor/queue_clone.py` — `ensure_queue_clone` accepts an explicit `dest` path; legacy rename of `queue/` → `queue-<instance-id>/`.
- `ralph_executor/loop.py` — `_queue_repo_root` and `_run_sweep` use `queue_clone_path`; `_claim_pbi` writes `CLAIM.json` atomically; halt META-BUG includes `tripped_by_instance`.
- `ralph_executor/queue/filesystem.py` — `_root` uses `queue_clone_path`; `current_pbi()` filters by `instance_id`.
- `ralph_executor/queue/movements.py` — `_queue_repo` uses `queue_clone_path`; `move_inbox_to_current` accepts an optional `extra_files: Iterable[Path]` to stage with the mv (so `_claim_pbi` can write `CLAIM.json` and have it land in the same commit).
- `ralph_executor/safety/halt.py` — `halt_and_acknowledge` writes `tripped_by_instance` into the META-BUG.
- `skills/ralph-status/scripts/status.py` — read `CLAIM.json`, add `OWNER` column.
- `skills/ralph-cancel/scripts/cancel.py` — refuse on foreign `CLAIM.json`.
- `skills/ralph-promote/scripts/promote.py` — refuse on foreign ownership transfer where applicable.
- `README.md` — "Running multiple ralphs" rewrite; upgrade procedure.
- `docs/runbooks/ralph-setup.md` — `instance_id` TOML key documented.

---

## Task 1: Identity helper module

**Files:**
- Create: `ralph_executor/identity.py`
- Test: `tests/executor/test_identity.py`

- [ ] **Step 1: Write the failing test**

`tests/executor/test_identity.py`:

```python
"""Identity sanitisation + validation."""

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
        # Caller (validate_instance_id) is responsible for raising; the
        # sanitiser is pure.
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

    def test_rejects_long(self) -> None:
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

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/executor/test_identity.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'ralph_executor.identity'`.

- [ ] **Step 3: Write minimal implementation**

`ralph_executor/identity.py`:

```python
"""Instance identity helpers — sanitisation, validation, hostname default.

The ``instance_id`` is the per-ralph identity that lets multiple
executors operate against the same ``queue_repo``. It is filesystem-safe,
branch-name-safe, and stable across a single ralph's lifetime (operator
discipline: the same id must be supplied on restart).
"""

from __future__ import annotations

import re
import socket

INSTANCE_ID_REGEX = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")


class InstanceIdError(ValueError):
    """Raised when an instance_id cannot be validated."""


def sanitize_instance_id(value: str) -> str:
    """Lowercase, replace non-`[a-z0-9_-]` chars with dashes, collapse runs.

    Pure function — does not raise. Empty / all-non-conforming inputs
    yield ``""``; callers wrap this with ``validate_instance_id`` if they
    need a hard-error guarantee.
    """
    lowered = value.strip().lower()
    # Replace any character outside the safe set with a dash.
    swapped = re.sub(r"[^a-z0-9_-]", "-", lowered)
    # Collapse runs of dashes so 'box.example.com' → 'box-example-com' (not 'box---com').
    collapsed = re.sub(r"-+", "-", swapped)
    return collapsed.strip("-")


def validate_instance_id(value: str) -> str:
    """Return ``value`` unchanged when it matches ``INSTANCE_ID_REGEX``.

    Raises :class:`InstanceIdError` with a message naming the regex when
    the input is empty or non-conforming. The validator does NOT sanitise
    — callers wanting "make this safe" semantics should call
    :func:`sanitize_instance_id` first.
    """
    if not value:
        raise InstanceIdError("instance_id must be set (CLI / env / TOML / hostname default)")
    if not INSTANCE_ID_REGEX.match(value):
        raise InstanceIdError(
            f"instance_id {value!r} does not match {INSTANCE_ID_REGEX.pattern}"
        )
    return value


def default_instance_id() -> str:
    """Return the sanitised hostname for use as the default ``instance_id``.

    Returns ``""`` if the hostname is empty (rare; the validator above will
    surface a clear error in that case).
    """
    return sanitize_instance_id(socket.gethostname())
```

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/executor/test_identity.py -v
```

Expected: PASS — 11 tests.

- [ ] **Step 5: Commit**

```
git add ralph_executor/identity.py tests/executor/test_identity.py
git commit -m "multi-ralph: instance_id sanitise/validate/default helpers"
```

---

## Task 2: User-config read/write for `instance_id`

**Files:**
- Modify: `ralph_executor/user_config.py`
- Test: `tests/executor/test_user_config.py` (add to existing if present; create if missing)

- [ ] **Step 1: Write the failing tests**

Append to `tests/executor/test_user_config.py` (or create if absent):

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
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    from ralph_executor.user_config import write_queue_repo

    write_queue_repo("https://github.com/owner/queue")
    write_instance_id("ralph-a")
    text = user_config_path().read_text(encoding="utf-8")
    assert 'queue_repo = "https://github.com/owner/queue"' in text
    assert 'instance_id = "ralph-a"' in text
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/executor/test_user_config.py::test_read_instance_id_missing_file_returns_none tests/executor/test_user_config.py::test_write_then_read_instance_id_roundtrips tests/executor/test_user_config.py::test_read_instance_id_non_string_raises tests/executor/test_user_config.py::test_write_instance_id_preserves_other_keys -v
```

Expected: FAIL — `ImportError: cannot import name 'read_instance_id'`.

- [ ] **Step 3: Write the implementation**

Append to `ralph_executor/user_config.py`:

```python
def read_instance_id() -> str | None:
    """Return the ``instance_id`` value from the user config, or None.

    Lives at the user-level (per-machine) because multi-ralph identity is
    per-process. Resolution priority in ``load_config`` is: CLI flag >
    env > THIS TOML value > hostname default.
    """
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
    """Persist ``instance_id`` to ``~/.ralph/config.toml``.

    Merges with existing keys so ``queue_repo`` / ``ralph_home`` / others
    are preserved.
    """
    return _write_user_config({"instance_id": value})
```

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/executor/test_user_config.py -v -k instance_id
```

Expected: PASS — 4 tests.

- [ ] **Step 5: Commit**

```
git add ralph_executor/user_config.py tests/executor/test_user_config.py
git commit -m "multi-ralph: read_instance_id/write_instance_id user-config helpers"
```

---

## Task 3: `ExecutorConfig.instance_id` + resolution in `load_config`

**Files:**
- Modify: `ralph_executor/config.py`
- Test: `tests/executor/test_config.py` (extend the existing file)

- [ ] **Step 1: Write the failing tests**

Append to `tests/executor/test_config.py`:

```python
def test_instance_id_defaults_to_sanitised_hostname(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
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
    _prepare_user_config(
        tmp_path, queue_repo="https://github.com/owner/queue", instance_id="ralph-from-toml"
    )
    _prepare_project_repo(tmp_path)
    monkeypatch.chdir(tmp_path / "project")
    cfg = load_config()
    assert cfg.instance_id == "ralph-from-env"


def test_instance_id_toml_wins_over_hostname(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("RALPH_INSTANCE_ID", raising=False)
    monkeypatch.setattr("socket.gethostname", lambda: "hostname-default")
    _prepare_user_config(
        tmp_path, queue_repo="https://github.com/owner/queue", instance_id="ralph-from-toml"
    )
    _prepare_project_repo(tmp_path)
    monkeypatch.chdir(tmp_path / "project")
    cfg = load_config()
    assert cfg.instance_id == "ralph-from-toml"


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

If `_prepare_user_config` / `_prepare_project_repo` do not already exist in the test module, add these helpers near the top:

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

- [ ] **Step 2: Run tests to verify they fail**

```
uv run pytest tests/executor/test_config.py -v -k instance_id
```

Expected: FAIL — `AttributeError: 'ExecutorConfig' object has no attribute 'instance_id'`.

- [ ] **Step 3: Implementation — add field, TOML key, resolver**

In `ralph_executor/config.py`:

a. Add `"instance_id"` to `_TOML_KNOWN_KEYS`:

```python
_TOML_KNOWN_KEYS = frozenset(
    {
        # ... existing keys ...
        "idle_exit_threshold",
        # Per-ralph identity. See ralph_executor/identity.py. Resolution
        # in load_config: CLI > RALPH_INSTANCE_ID env > this TOML key >
        # ~/.ralph/config.toml (user-level) > sanitised hostname.
        "instance_id",
    }
)
```

b. Add the field to `ExecutorConfig` (after `idle_exit_threshold`):

```python
    # Per-ralph identity, set once at startup and never changed during
    # the process lifetime. Resolution order: --instance-id flag >
    # RALPH_INSTANCE_ID env > project .ralph/config.toml > user
    # ~/.ralph/config.toml > sanitised hostname. See
    # ``ralph_executor.identity``.
    instance_id: str = ""
```

c. Add resolution inside `load_config` (after `idle_exit_threshold` resolution, before the `return ExecutorConfig(...)`):

```python
    from ralph_executor.identity import (
        InstanceIdError,
        default_instance_id,
        sanitize_instance_id,
        validate_instance_id,
    )
    from ralph_executor.user_config import read_instance_id, user_config_path

    instance_id_source = "project " + source_label
    instance_id_value: str | None = None
    raw_env = os.environ.get("RALPH_INSTANCE_ID")
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
        instance_id_value = sanitize_instance_id(instance_id_value)
    try:
        instance_id = validate_instance_id(instance_id_value)
    except InstanceIdError as exc:
        raise ConfigError(
            f"{instance_id_source}: {exc}. "
            "Set 'instance_id = \"<name>\"' in ~/.ralph/config.toml "
            "or pass --instance-id."
        ) from exc
```

d. Pass `instance_id=instance_id` into the `ExecutorConfig(...)` constructor at the bottom.

- [ ] **Step 4: Run tests to verify they pass**

```
uv run pytest tests/executor/test_config.py -v -k instance_id
```

Expected: PASS — 5 tests.

- [ ] **Step 5: Commit**

```
git add ralph_executor/config.py tests/executor/test_config.py
git commit -m "multi-ralph: instance_id field + resolution in load_config"
```

---

## Task 4: `--instance-id` CLI flag

**Files:**
- Modify: `ralph_executor/cli.py`
- Test: `tests/executor/test_cli.py` (extend the existing module)

- [ ] **Step 1: Write the failing test**

Append to `tests/executor/test_cli.py`:

```python
def test_instance_id_flag_overrides_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--instance-id flag wins over RALPH_INSTANCE_ID env and TOML."""
    from ralph_executor.cli import _apply_overrides

    monkeypatch.setenv("RALPH_INSTANCE_ID", "from-env")
    cfg = _make_fake_config(repo_path=tmp_path, instance_id="from-config")
    args = _make_args(instance_id="from-flag")
    new_cfg = _apply_overrides(cfg, args)
    assert new_cfg.instance_id == "from-flag"


def test_instance_id_flag_validated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--instance-id with invalid value raises ConfigError before iterate."""
    from ralph_executor.cli import _apply_overrides
    from ralph_executor.config import ConfigError

    cfg = _make_fake_config(repo_path=tmp_path, instance_id="ok")
    args = _make_args(instance_id="Not Valid!")
    with pytest.raises(ConfigError, match="instance_id"):
        _apply_overrides(cfg, args)
```

If `_make_fake_config` and `_make_args` helpers do not yet exist in the test module, add them:

```python
def _make_fake_config(*, repo_path: Path, instance_id: str = "ralph") -> "ExecutorConfig":
    from ralph_executor.config import ExecutorConfig

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

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/executor/test_cli.py -v -k instance_id
```

Expected: FAIL — `argparse.Namespace` lacks `instance_id`, or `_apply_overrides` ignores it.

- [ ] **Step 3: Implementation**

In `ralph_executor/cli.py`:

a. Inside `_build_parser`, add the flag near `--log-level`:

```python
    parser.add_argument(
        "--instance-id",
        dest="instance_id",
        metavar="ID",
        help=(
            "Override the instance_id TOML/env value for this run. "
            "Resolution order: this flag > RALPH_INSTANCE_ID env > "
            "project .ralph/config.toml > ~/.ralph/config.toml > "
            "sanitised hostname."
        ),
    )
```

b. Inside `_apply_overrides`, before the existing return:

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

c. Extend the `dataclasses.replace(...)` call to include `instance_id=instance_id`.

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/executor/test_cli.py -v -k instance_id
```

Expected: PASS — 2 tests.

- [ ] **Step 5: Commit**

```
git add ralph_executor/cli.py tests/executor/test_cli.py
git commit -m "multi-ralph: --instance-id CLI flag wires through _apply_overrides"
```

---

## Task 5: `ralph-executor init` prompts for `instance_id`

**Files:**
- Modify: `ralph_executor/setup_cmds.py`
- Modify: `ralph_executor/cli.py` (init subparser already wired; nothing to add)
- Test: `tests/executor/test_setup_cmds.py` (extend existing if present)

- [ ] **Step 1: Write the failing test**

Append to `tests/executor/test_setup_cmds.py` (create the file if needed; mirror the existing pattern for `test_cmd_init`):

```python
def test_cmd_init_prompts_and_writes_instance_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from ralph_executor.setup_cmds import cmd_init
    from ralph_executor.user_config import read_instance_id

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr("socket.gethostname", lambda: "BoxA")
    inputs = iter(["", "https://github.com/owner/queue", "", "ralph-a"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))
    rc = cmd_init(ralph_home=None, assume_yes=False)
    assert rc == 0
    assert read_instance_id() == "ralph-a"


def test_cmd_init_assume_yes_defaults_to_hostname_instance_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ralph_executor.setup_cmds import cmd_init
    from ralph_executor.user_config import read_instance_id

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr("socket.gethostname", lambda: "BoxA")
    rc = cmd_init(ralph_home=None, assume_yes=True)
    assert rc == 0
    assert read_instance_id() == "boxa"
```

Note: the existing `cmd_init` expects 3 prompts already (ralph_home, queue_repo, queue_branch). Adjust the input iterator order if necessary to match the actual prompt sequence after Step 3.

- [ ] **Step 2: Run tests to verify failure**

```
uv run pytest tests/executor/test_setup_cmds.py -v -k instance_id
```

Expected: FAIL — `cmd_init` never calls `write_instance_id` so the read returns None.

- [ ] **Step 3: Implementation**

In `ralph_executor/setup_cmds.py`, inside `cmd_init`, AFTER the `queue_branch` prompt and BEFORE returning 0, add:

```python
    from ralph_executor.identity import (
        InstanceIdError,
        default_instance_id,
        sanitize_instance_id,
        validate_instance_id,
    )
    from ralph_executor.user_config import write_instance_id

    fallback_instance = default_instance_id() or "ralph"
    if assume_yes:
        instance_id = fallback_instance
    else:
        raw = input(
            f"Instance id [default: {fallback_instance}]: "
        ).strip()
        instance_id = sanitize_instance_id(raw) if raw else fallback_instance
    try:
        instance_id = validate_instance_id(instance_id)
    except InstanceIdError as exc:
        raise ConfigError(f"init: {exc}") from exc
    write_instance_id(instance_id)
    print(f"instance_id = \"{instance_id}\" written to ~/.ralph/config.toml")
```

(Adjust the prompt position to match the existing flow's prompts. Read `setup_cmds.py` first to confirm the order.)

- [ ] **Step 4: Run tests to verify they pass**

```
uv run pytest tests/executor/test_setup_cmds.py -v -k instance_id
```

Expected: PASS — 2 tests.

- [ ] **Step 5: Commit**

```
git add ralph_executor/setup_cmds.py tests/executor/test_setup_cmds.py
git commit -m "multi-ralph: ralph-executor init prompts for instance_id"
```

---

## Task 6: `queue_clone_path` helper + central queue-path resolution

**Files:**
- Create: `ralph_executor/queue_path.py`
- Test: `tests/executor/test_queue_path.py`

- [ ] **Step 1: Write the failing test**

`tests/executor/test_queue_path.py`:

```python
from pathlib import Path

from ralph_executor.queue_path import queue_clone_path


def test_returns_namespaced_path(tmp_path: Path) -> None:
    assert queue_clone_path(tmp_path, "ralph-a") == tmp_path / "queue-ralph-a"


def test_default_instance_id_n1_case(tmp_path: Path) -> None:
    assert queue_clone_path(tmp_path, "boxa") == tmp_path / "queue-boxa"
```

- [ ] **Step 2: Run test to verify failure**

```
uv run pytest tests/executor/test_queue_path.py -v
```

Expected: FAIL — module missing.

- [ ] **Step 3: Implementation**

`ralph_executor/queue_path.py`:

```python
"""Single source of truth for the per-instance queue-clone path.

Every site that needs to read or write inside ``.ralph/`` resolves the
clone root via this helper so we can change the layout in exactly one
place. Pre-multi-ralph this was hard-coded to ``<workspace_root>/queue/``;
now it is ``<workspace_root>/queue-<instance-id>/`` to allow multiple
ralphs to share a single ``workspace_root``.
"""

from __future__ import annotations

from pathlib import Path


def queue_clone_path(workspace_root: Path, instance_id: str) -> Path:
    """Return the directory that holds this instance's queue clone."""
    if not instance_id:
        raise ValueError("queue_clone_path requires a non-empty instance_id")
    return workspace_root / f"queue-{instance_id}"
```

- [ ] **Step 4: Run test to verify pass**

```
uv run pytest tests/executor/test_queue_path.py -v
```

Expected: PASS — 2 tests.

- [ ] **Step 5: Commit**

```
git add ralph_executor/queue_path.py tests/executor/test_queue_path.py
git commit -m "multi-ralph: queue_clone_path helper"
```

---

## Task 7: Route all queue-path callers through `queue_clone_path`

**Files:**
- Modify: `ralph_executor/loop.py`
- Modify: `ralph_executor/queue/filesystem.py`
- Modify: `ralph_executor/queue/movements.py`
- Modify: `ralph_executor/queue_clone.py`
- Test: extend `tests/executor/test_loop.py`, `tests/executor/test_filesystem_queue.py`, `tests/executor/test_queue_clone.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/executor/test_loop.py`:

```python
def test_queue_repo_root_uses_instance_id(tmp_path: Path) -> None:
    """_queue_repo_root resolves to <workspace_root>/queue-<instance-id>/."""
    from ralph_executor.loop import _queue_repo_root

    cfg = _make_fake_config(repo_path=tmp_path, instance_id="ralph-a")
    cfg = dataclasses.replace(cfg, workspace_root=tmp_path)
    assert _queue_repo_root(cfg) == tmp_path / "queue-ralph-a"
```

Add an equivalent test asserting `FilesystemQueueSource._root` resolves to `<workspace_root>/queue-<instance-id>/.ralph` in `tests/executor/test_filesystem_queue.py`.

- [ ] **Step 2: Run tests to verify failure**

```
uv run pytest tests/executor/test_loop.py::test_queue_repo_root_uses_instance_id tests/executor/test_filesystem_queue.py -v
```

Expected: FAIL — paths still hard-coded to `queue/`.

- [ ] **Step 3: Implementation**

a. `ralph_executor/loop.py`: replace the body of `_queue_repo_root`:

```python
def _queue_repo_root(cfg: ExecutorConfig) -> Path:
    """Filesystem path of the queue clone for this instance.

    Always ``<workspace_root>/queue-<instance-id>/``. See
    ``ralph_executor.queue_path`` for the single source of truth.
    """
    from ralph_executor.queue_path import queue_clone_path

    return queue_clone_path(cfg.workspace_root, cfg.instance_id)
```

b. `ralph_executor/queue/filesystem.py`: replace the `_root` property:

```python
    @property
    def _root(self) -> Path:
        from ralph_executor.queue_path import queue_clone_path

        return queue_clone_path(self._config.workspace_root, self._config.instance_id) / ".ralph"
```

c. `ralph_executor/queue/movements.py`: replace the body of `_queue_repo`:

```python
def _queue_repo(cfg: ExecutorConfig) -> Path:
    from ralph_executor.queue_path import queue_clone_path

    return queue_clone_path(cfg.workspace_root, cfg.instance_id)
```

d. `ralph_executor/queue_clone.py`: extend `ensure_queue_clone` to accept an explicit destination so the caller (`loop._pull_queue`) controls the path.

```python
def ensure_queue_clone(
    workspace_root: Path,
    queue_repo: str,
    queue_branch: str,
    *,
    dest: Path | None = None,
    timeout: float = 120.0,
) -> Path:
    """Ensure ``dest`` (default: ``workspace_root / 'queue'``) is a clone of
    ``queue_repo`` on ``queue_branch``.

    Callers that need per-instance namespacing pass ``dest`` explicitly;
    legacy callers receive the old single-clone behaviour.
    """
    workspace_root.mkdir(parents=True, exist_ok=True)
    if dest is None:
        dest = workspace_root / "queue"
    # ... existing clone / fetch / pull logic, replacing every literal
    # `workspace_root / "queue"` with `dest`.
```

e. `ralph_executor/loop.py::_pull_queue`: pass the namespaced dest in:

```python
def _pull_queue(cfg: ExecutorConfig) -> None:
    from ralph_executor.queue_path import queue_clone_path

    dest = queue_clone_path(cfg.workspace_root, cfg.instance_id)
    log.debug(
        "refreshing queue clone for %s (branch=%s) -> %s",
        cfg.queue_repo,
        cfg.queue_branch,
        dest,
    )
    ensure_queue_clone(
        cfg.workspace_root, cfg.queue_repo, cfg.queue_branch, dest=dest
    )
```

- [ ] **Step 4: Run tests to verify pass + sweep existing tests**

```
uv run pytest tests/executor/ -v
```

Expected: PASS for all (the new tests + existing). If any existing test relied on the literal `queue/` path, fix the test fixture to construct `queue-<instance>/` instead.

- [ ] **Step 5: Commit**

```
git add ralph_executor/loop.py ralph_executor/queue/filesystem.py \
        ralph_executor/queue/movements.py ralph_executor/queue_clone.py \
        tests/executor/test_loop.py tests/executor/test_filesystem_queue.py \
        tests/executor/test_queue_clone.py
git commit -m "multi-ralph: route queue-path callers through queue_clone_path"
```

---

## Task 8: Legacy `queue/` → `queue-<instance-id>/` rename on first startup

**Files:**
- Modify: `ralph_executor/queue_clone.py`
- Test: extend `tests/executor/test_queue_clone.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/executor/test_queue_clone.py`:

```python
def test_ensure_queue_clone_migrates_legacy_path(tmp_path: Path) -> None:
    """If <workspace_root>/queue/ exists and dest does not, rename atomically."""
    legacy = tmp_path / "queue"
    legacy.mkdir()
    (legacy / ".git").mkdir()
    (legacy / ".git" / "HEAD").write_text("ref: refs/heads/ralph-queue\n")
    dest = tmp_path / "queue-ralph-a"
    ensure_queue_clone(
        tmp_path, "https://github.com/owner/queue", "ralph-queue", dest=dest
    )
    assert dest.is_dir()
    assert (dest / ".git").is_dir()
    assert not legacy.exists()


def test_ensure_queue_clone_refuses_when_both_paths_exist(tmp_path: Path) -> None:
    """If both legacy and dest exist, raise so operator notices the conflict."""
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

(Stub the actual git fetch/pull so the test does not hit the network — monkeypatch `_run_git` to return a `CompletedProcess` with returncode 0.)

- [ ] **Step 2: Run tests to verify failure**

```
uv run pytest tests/executor/test_queue_clone.py -v -k legacy
```

Expected: FAIL — no rename logic.

- [ ] **Step 3: Implementation**

In `ralph_executor/queue_clone.py`, near the top of `ensure_queue_clone` (immediately after `workspace_root.mkdir(...)` and after resolving `dest`):

```python
    legacy = workspace_root / "queue"
    if dest != legacy and legacy.is_dir() and not dest.exists():
        log.info("migrating legacy queue clone: %s -> %s", legacy, dest)
        legacy.rename(dest)
    elif dest != legacy and legacy.is_dir() and dest.exists():
        raise QueueCloneError(
            f"both legacy queue/ and {dest.name}/ exist under {workspace_root}; "
            "remove one before continuing"
        )
```

- [ ] **Step 4: Run tests to verify pass**

```
uv run pytest tests/executor/test_queue_clone.py -v
```

Expected: PASS — all queue-clone tests.

- [ ] **Step 5: Commit**

```
git add ralph_executor/queue_clone.py tests/executor/test_queue_clone.py
git commit -m "multi-ralph: rename legacy <workspace_root>/queue/ on first startup"
```

---

## Task 9: `CLAIM.json` read/write module

**Files:**
- Create: `ralph_executor/claim.py`
- Test: `tests/executor/test_claim.py`

- [ ] **Step 1: Write the failing test**

`tests/executor/test_claim.py`:

```python
from datetime import datetime, timezone
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
        claimed_at=datetime(2026, 5, 31, 12, 34, 56, tzinfo=timezone.utc),
        hostname="box-a",
    )
    write_claim(pbi_dir, info)
    assert (pbi_dir / CLAIM_FILENAME).is_file()
    parsed = read_claim(pbi_dir)
    assert parsed == info


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
    (pbi_dir / CLAIM_FILENAME).write_text('{"instance_id": "ralph-a"}', encoding="utf-8")
    with pytest.raises(ClaimParseError, match="claimed_at"):
        read_claim(pbi_dir)
```

- [ ] **Step 2: Run tests to verify failure**

```
uv run pytest tests/executor/test_claim.py -v
```

Expected: FAIL — module missing.

- [ ] **Step 3: Implementation**

`ralph_executor/claim.py`:

```python
"""``CLAIM.json`` schema and IO helpers.

Each PBI in ``.ralph/current/<id>/`` carries a ``CLAIM.json`` naming the
ralph instance that owns it. Written atomically with the
``git mv inbox/<id>/ current/<id>/`` so claim and move share a single
commit + push.
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
    """Write ``CLAIM.json`` into ``pbi_dir`` and return the file path."""
    payload = {
        "instance_id": info.instance_id,
        "claimed_at": info.claimed_at.isoformat(),
        "hostname": info.hostname,
    }
    path = pbi_dir / CLAIM_FILENAME
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def read_claim(pbi_dir: Path) -> ClaimInfo | None:
    """Read ``CLAIM.json`` from ``pbi_dir``, or return None if absent.

    Raises :class:`ClaimParseError` for malformed JSON, wrong types, or
    missing required fields.
    """
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

- [ ] **Step 4: Run tests to verify pass**

```
uv run pytest tests/executor/test_claim.py -v
```

Expected: PASS — 4 tests.

- [ ] **Step 5: Commit**

```
git add ralph_executor/claim.py tests/executor/test_claim.py
git commit -m "multi-ralph: CLAIM.json read/write helpers"
```

---

## Task 10: Atomic claim commit — `_claim_pbi` writes `CLAIM.json` with the move

**Files:**
- Modify: `ralph_executor/queue/movements.py` (extend `move_inbox_to_current`)
- Modify: `ralph_executor/loop.py` (`_claim_pbi_worktree`)
- Test: extend `tests/executor/test_loop.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/executor/test_loop.py`:

```python
def test_claim_writes_claim_json_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_claim_pbi writes CLAIM.json in the same commit as the inbox→current move."""
    # ... fixture stub: a bare queue repo with one inbox PBI, monkeypatched
    # target_clone.ensure_clone to return a fake clone_root, monkeypatched
    # ensure_worktree no-op.
    from ralph_executor.claim import read_claim

    cfg = _build_loop_fixture(tmp_path, instance_id="ralph-a", inbox_id="WI-1234")
    iterate_once(cfg)
    pbi_dir = _queue_repo_root(cfg) / ".ralph" / "current" / "WI-1234"
    assert pbi_dir.is_dir()
    claim = read_claim(pbi_dir)
    assert claim is not None
    assert claim.instance_id == "ralph-a"
    # Verify the claim landed in the same commit as the mv (no orphan commits).
    log = subprocess.run(
        ["git", "-C", str(_queue_repo_root(cfg)), "log", "--name-only", "--format=%H"],
        capture_output=True, text=True, check=True,
    ).stdout
    last_commit = log.split("\n\n")[0]  # newest commit's hash + filenames
    assert "CLAIM.json" in last_commit
    assert "current/WI-1234" in last_commit
```

Helper `_build_loop_fixture` should construct a bare queue repo with one PBI in `inbox/`, plus the executor config wiring; reuse / extend any existing two-repo fixture in the file.

- [ ] **Step 2: Run test to verify failure**

```
uv run pytest tests/executor/test_loop.py -v -k claim_writes_claim_json
```

Expected: FAIL — `CLAIM.json` is not written.

- [ ] **Step 3: Implementation**

a. `ralph_executor/queue/movements.py::move_inbox_to_current` — accept an optional `extra_files` parameter and stage them before the commit:

```python
def move_inbox_to_current(
    cfg: ExecutorConfig,
    pbi: PBI,
    *,
    event_log: EventLog | None = None,
    now: datetime | None = None,
    extra_files: list[Path] | None = None,
) -> PBI:
    """Move ``pbi`` from inbox/ to current/, optionally staging extra files
    in the same commit (e.g. CLAIM.json)."""
    # ... existing move logic ...
    # After the git mv, before commit:
    if extra_files:
        for path in extra_files:
            git_ops.stage_path(_queue_repo(cfg), path)
    # ... existing commit + push ...
```

b. `ralph_executor/loop.py::_claim_pbi_worktree` — write CLAIM.json BEFORE invoking `move_inbox_to_current`, pass its path through:

```python
    from datetime import datetime, UTC

    from ralph_executor.claim import CLAIM_FILENAME, ClaimInfo, write_claim

    # ... existing target-clone ensure ...
    event_log = open_log(_queue_repo_root(cfg))
    try:
        # Write CLAIM.json into the inbox dir BEFORE git mv runs;
        # move_inbox_to_current passes ``extra_files`` so the new file
        # lands in the same commit as the mv.
        claim_info = ClaimInfo(
            instance_id=cfg.instance_id,
            claimed_at=datetime.now(tz=UTC),
            hostname=socket.gethostname(),
        )
        claim_path_in_inbox = pbi.path / CLAIM_FILENAME
        write_claim(pbi.path, claim_info)
        moved = move_inbox_to_current(
            cfg,
            pbi,
            event_log=event_log,
            now=datetime.now(tz=UTC),
            extra_files=[claim_path_in_inbox.parent.parent.parent / "current" / pbi.id / CLAIM_FILENAME],
        )
    finally:
        event_log.close()
    # ... existing worktree create ...
```

Note: the `extra_files` path must reflect the post-mv location, because `git mv` updates the index entry. The helper `stage_path` can simply call `git add <path>`; the implementation is permissive about the file already being staged via the mv.

c. Add the simple staging helper `git_ops.stage_path(repo, path)` (or reuse `add_all_changes` if its semantics already match — verify in `ralph_executor/git_ops.py`).

- [ ] **Step 4: Run test to verify pass**

```
uv run pytest tests/executor/test_loop.py -v -k claim_writes_claim_json
uv run pytest tests/executor/ -v   # regression sweep
```

Expected: PASS for the new test, no regressions.

- [ ] **Step 5: Commit**

```
git add ralph_executor/queue/movements.py ralph_executor/loop.py \
        ralph_executor/git_ops.py tests/executor/test_loop.py
git commit -m "multi-ralph: claim writes CLAIM.json atomically with inbox→current mv"
```

---

## Task 11: `current_pbi()` filters by instance_id

**Files:**
- Modify: `ralph_executor/queue/filesystem.py`
- Test: extend `tests/executor/test_filesystem_queue.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/executor/test_filesystem_queue.py`:

```python
def test_current_pbi_returns_own_claim_only(tmp_path: Path) -> None:
    """Filters current/*/ by CLAIM.json.instance_id matching cfg.instance_id."""
    from ralph_executor.claim import ClaimInfo, write_claim
    from ralph_executor.queue.filesystem import FilesystemQueueSource

    cfg = _make_fake_config(repo_path=tmp_path, instance_id="ralph-a")
    cfg = dataclasses.replace(cfg, workspace_root=tmp_path)
    current = tmp_path / "queue-ralph-a" / ".ralph" / "current"
    _make_pbi_dir(current / "WI-1234", pbi_id="WI-1234", pbi_type="feature")
    _make_pbi_dir(current / "WI-5678", pbi_id="WI-5678", pbi_type="feature")
    write_claim(current / "WI-1234", _claim("ralph-a"))
    write_claim(current / "WI-5678", _claim("ralph-b"))
    source = FilesystemQueueSource(cfg)
    pbi = source.current_pbi()
    assert pbi is not None
    assert pbi.id == "WI-1234"


def test_current_pbi_none_when_only_foreign_claims(tmp_path: Path) -> None:
    from ralph_executor.claim import write_claim
    from ralph_executor.queue.filesystem import FilesystemQueueSource

    cfg = _make_fake_config(repo_path=tmp_path, instance_id="ralph-a")
    cfg = dataclasses.replace(cfg, workspace_root=tmp_path)
    current = tmp_path / "queue-ralph-a" / ".ralph" / "current"
    _make_pbi_dir(current / "WI-5678", pbi_id="WI-5678", pbi_type="feature")
    write_claim(current / "WI-5678", _claim("ralph-b"))
    assert FilesystemQueueSource(cfg).current_pbi() is None


def test_current_pbi_raises_when_two_own_claims(tmp_path: Path) -> None:
    from ralph_executor.claim import write_claim
    from ralph_executor.queue.filesystem import FilesystemQueueSource, QueueError

    cfg = _make_fake_config(repo_path=tmp_path, instance_id="ralph-a")
    cfg = dataclasses.replace(cfg, workspace_root=tmp_path)
    current = tmp_path / "queue-ralph-a" / ".ralph" / "current"
    _make_pbi_dir(current / "WI-1", pbi_id="WI-1", pbi_type="feature")
    _make_pbi_dir(current / "WI-2", pbi_id="WI-2", pbi_type="feature")
    write_claim(current / "WI-1", _claim("ralph-a"))
    write_claim(current / "WI-2", _claim("ralph-a"))
    with pytest.raises(QueueError, match="more than one"):
        FilesystemQueueSource(cfg).current_pbi()


def test_current_pbi_skips_dir_without_claim(tmp_path: Path) -> None:
    """A current/<id>/ without CLAIM.json is invisible to this instance."""
    from ralph_executor.queue.filesystem import FilesystemQueueSource

    cfg = _make_fake_config(repo_path=tmp_path, instance_id="ralph-a")
    cfg = dataclasses.replace(cfg, workspace_root=tmp_path)
    current = tmp_path / "queue-ralph-a" / ".ralph" / "current"
    _make_pbi_dir(current / "WI-legacy", pbi_id="WI-legacy", pbi_type="feature")
    assert FilesystemQueueSource(cfg).current_pbi() is None
```

Add helper `_claim(instance_id)` returning a `ClaimInfo` with a fixed `claimed_at` and the given instance.

- [ ] **Step 2: Run tests to verify failure**

```
uv run pytest tests/executor/test_filesystem_queue.py -v -k current_pbi
```

Expected: FAIL — the current implementation raises on >1 PBI in `current/` regardless of ownership.

- [ ] **Step 3: Implementation**

Replace `FilesystemQueueSource.current_pbi`:

```python
    def current_pbi(self) -> PBI | None:
        """Return THIS instance's claimed PBI, or None.

        Scans ``.ralph/current/*/CLAIM.json``. Returns the PBI whose
        ``CLAIM.json.instance_id`` matches ``cfg.instance_id``. Foreign
        claims are skipped; missing claims are skipped (legacy PBIs).
        Raises ``QueueError`` if this instance owns more than one PBI in
        ``current/`` (invariant: one Claude per ralph).
        """
        from ralph_executor.claim import ClaimParseError, read_claim

        own: list[PBI] = []
        for pbi in self._list_pbis("current"):
            try:
                claim = read_claim(pbi.path)
            except ClaimParseError as exc:
                raise QueueError(f"current/{pbi.id}: malformed CLAIM.json: {exc}") from exc
            if claim is None:
                continue
            if claim.instance_id == self._config.instance_id:
                own.append(pbi)
        if not own:
            return None
        if len(own) > 1:
            ids = sorted(p.id for p in own)
            raise QueueError(f"current/ contains more than one PBI owned by this instance: {ids}")
        return own[0]
```

- [ ] **Step 4: Run tests to verify pass**

```
uv run pytest tests/executor/test_filesystem_queue.py -v
```

Expected: PASS — all 4 new tests + existing.

- [ ] **Step 5: Commit**

```
git add ralph_executor/queue/filesystem.py tests/executor/test_filesystem_queue.py
git commit -m "multi-ralph: current_pbi filters by instance_id"
```

---

## Task 12: META-BUG carries `tripped_by_instance`

**Files:**
- Modify: `ralph_executor/safety/halt.py`
- Test: extend `tests/executor/test_halt.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/executor/test_halt.py`:

```python
def test_halt_and_acknowledge_records_instance_id(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from ralph_executor.safety.halt import halt_and_acknowledge
    from ralph_executor.safety.cycle_detector import CycleSignal

    signals = [CycleSignal(rule="dummy", message="for test", evidence=[])]
    halt_and_acknowledge(
        repo=tmp_path,
        signals=signals,
        now=datetime(2026, 5, 31, tzinfo=UTC),
        tripped_by_instance="ralph-a",
    )
    meta = next((tmp_path / ".ralph" / "blocked").rglob("META-cycle-*"))
    text = (meta / "PBI.md").read_text(encoding="utf-8")
    assert "tripped_by_instance: ralph-a" in text
```

- [ ] **Step 2: Run test to verify failure**

```
uv run pytest tests/executor/test_halt.py -v -k tripped_by_instance
```

Expected: FAIL — `halt_and_acknowledge` either does not accept `tripped_by_instance` or does not include it.

- [ ] **Step 3: Implementation**

a. `ralph_executor/safety/halt.py::halt_and_acknowledge` — accept and embed:

```python
def halt_and_acknowledge(
    *,
    repo: Path,
    signals: list[CycleSignal],
    now: datetime,
    tripped_by_instance: str = "",
) -> None:
    # ... existing META-BUG body construction ...
    if tripped_by_instance:
        frontmatter_lines.append(f"tripped_by_instance: {tripped_by_instance}")
    # ... existing write ...
```

b. `ralph_executor/loop.py::_check_cycle_detector` — pass `cfg.instance_id` through:

```python
    halt_and_acknowledge(
        repo=_queue_repo_root(cfg),
        signals=signals,
        now=now,
        tripped_by_instance=cfg.instance_id,
    )
```

- [ ] **Step 4: Run test to verify pass**

```
uv run pytest tests/executor/test_halt.py -v
uv run pytest tests/executor/test_loop.py -v   # regression sweep
```

Expected: PASS — all halt tests + existing loop tests still pass.

- [ ] **Step 5: Commit**

```
git add ralph_executor/safety/halt.py ralph_executor/loop.py tests/executor/test_halt.py
git commit -m "multi-ralph: META-BUG records tripped_by_instance"
```

---

## Task 13: Cross-platform workspace lockfile

**Files:**
- Create: `ralph_executor/lockfile.py`
- Test: `tests/executor/test_lockfile.py`

- [ ] **Step 1: Write the failing test**

`tests/executor/test_lockfile.py`:

```python
from pathlib import Path

import pytest

from ralph_executor.lockfile import LockHeldError, WorkspaceLock


def test_acquire_then_release(tmp_path: Path) -> None:
    lock = WorkspaceLock(tmp_path / "queue-a" / ".ralph.lock", instance_id="ralph-a")
    with lock:
        assert (tmp_path / "queue-a" / ".ralph.lock").is_file()
    # File may remain (POSIX/Windows differ); the OS lock release is the contract.


def test_second_acquire_raises(tmp_path: Path) -> None:
    path = tmp_path / "queue-a" / ".ralph.lock"
    first = WorkspaceLock(path, instance_id="ralph-a")
    second = WorkspaceLock(path, instance_id="ralph-a")
    with first:
        with pytest.raises(LockHeldError):
            second.acquire()


def test_lock_payload_includes_pid_and_instance(tmp_path: Path) -> None:
    import json
    path = tmp_path / "queue-a" / ".ralph.lock"
    lock = WorkspaceLock(path, instance_id="ralph-a")
    with lock:
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["instance_id"] == "ralph-a"
        assert isinstance(payload["pid"], int)
        assert "hostname" in payload
        assert "started_at" in payload
```

- [ ] **Step 2: Run tests to verify failure**

```
uv run pytest tests/executor/test_lockfile.py -v
```

Expected: FAIL — module missing.

- [ ] **Step 3: Implementation**

`ralph_executor/lockfile.py`:

```python
"""OS-level exclusive lockfile for the per-instance workspace.

Acquired at executor startup; released by the OS on process exit. The
lockfile lives at ``<workspace_root>/queue-<instance-id>/.ralph.lock``.

This is intentionally a single-host primitive — it catches operator
double-launches against the same workspace. Cross-host id collisions
require coordinator-level work (Scope 2).
"""

from __future__ import annotations

import json
import os
import socket
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO


class LockHeldError(RuntimeError):
    """Raised when the lockfile is already held by another process."""


@dataclass
class WorkspaceLock:
    """Cross-platform exclusive lockfile.

    Use as a context manager (``with WorkspaceLock(...): ...``) or call
    ``acquire()`` / ``release()`` explicitly. The OS releases the lock
    on process death even if ``release()`` is never called.
    """

    path: Path
    instance_id: str
    _fh: IO[bytes] | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a+b")  # noqa: SIM115 — released in release()
        if sys.platform == "win32":
            import msvcrt

            try:
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                self._fh.close()
                self._fh = None
                raise LockHeldError(
                    f"another process already holds {self.path}"
                ) from exc
        else:
            import fcntl

            try:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                self._fh.close()
                self._fh = None
                raise LockHeldError(
                    f"another process already holds {self.path}"
                ) from exc
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

    def __enter__(self) -> WorkspaceLock:
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()
```

- [ ] **Step 4: Run tests to verify pass**

```
uv run pytest tests/executor/test_lockfile.py -v
```

Expected: PASS — 3 tests.

- [ ] **Step 5: Commit**

```
git add ralph_executor/lockfile.py tests/executor/test_lockfile.py
git commit -m "multi-ralph: cross-platform workspace lockfile"
```

---

## Task 14: CLI acquires the lockfile at startup

**Files:**
- Modify: `ralph_executor/cli.py`
- Test: extend `tests/executor/test_cli.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/executor/test_cli.py`:

```python
def test_main_exits_when_lockfile_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """If another process already holds the workspace lockfile, exit non-zero."""
    from ralph_executor.lockfile import WorkspaceLock
    from ralph_executor.queue_path import queue_clone_path

    # Set up a minimal project + user config in tmp_path.
    _prepare_user_config(tmp_path, queue_repo="https://github.com/owner/queue", instance_id="ralph-a")
    _prepare_project_repo(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("RALPH_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.chdir(tmp_path / "project")

    lock_path = queue_clone_path(tmp_path / "ws", "ralph-a") / ".ralph.lock"
    other = WorkspaceLock(lock_path, instance_id="ralph-a")
    other.acquire()
    try:
        from ralph_executor.cli import main

        rc = main(["--once"])
        assert rc != 0
        captured = capsys.readouterr()
        assert "another ralph already running" in captured.err.lower()
    finally:
        other.release()
```

- [ ] **Step 2: Run test to verify failure**

```
uv run pytest tests/executor/test_cli.py -v -k lockfile
```

Expected: FAIL — `main` does not acquire the lock yet.

- [ ] **Step 3: Implementation**

In `ralph_executor/cli.py::main`, immediately after `_configure_logging(cfg.log_level)` and BEFORE `prepare_host_environment`:

```python
    from ralph_executor.lockfile import LockHeldError, WorkspaceLock
    from ralph_executor.queue_path import queue_clone_path

    lock_path = queue_clone_path(cfg.workspace_root, cfg.instance_id) / ".ralph.lock"
    lock = WorkspaceLock(lock_path, instance_id=cfg.instance_id)
    try:
        lock.acquire()
    except LockHeldError as exc:
        print(f"error: another ralph already running on this workspace: {exc}", file=sys.stderr)
        return 2
```

Wrap the rest of `main`'s body in a `try / finally` that releases the lock on clean exit.

- [ ] **Step 4: Run test to verify pass**

```
uv run pytest tests/executor/test_cli.py -v
```

Expected: PASS — all CLI tests still green.

- [ ] **Step 5: Commit**

```
git add ralph_executor/cli.py tests/executor/test_cli.py
git commit -m "multi-ralph: cli acquires workspace lockfile at startup"
```

---

## Task 15: `ralph-status` adds an OWNER column

**Files:**
- Modify: `skills/ralph-status/scripts/status.py`
- Test: extend `tests/skills/test_ralph_status.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/skills/test_ralph_status.py`:

```python
def test_status_table_includes_owner_column(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Output includes an OWNER column populated from CLAIM.json."""
    from ralph_executor.claim import ClaimInfo, write_claim
    from skills.ralph_status.scripts import status as status_mod

    # Set up a queue clone with one PBI in current/ owned by 'ralph-a'.
    _setup_queue_clone(tmp_path, instance_id="ralph-a")
    current = tmp_path / "queue-ralph-a" / ".ralph" / "current" / "WI-1234"
    _make_pbi_dir(current, pbi_id="WI-1234", pbi_type="feature")
    write_claim(current, ClaimInfo(instance_id="ralph-a", claimed_at=_NOW, hostname="box-a"))

    rendered = status_mod.render_status(workspace_root=tmp_path, instance_id="ralph-a")
    assert "OWNER" in rendered
    assert "ralph-a" in rendered
```

- [ ] **Step 2: Run test to verify failure**

```
uv run pytest tests/skills/test_ralph_status.py -v -k owner
```

Expected: FAIL — `OWNER` not in header / output.

- [ ] **Step 3: Implementation**

In `skills/ralph-status/scripts/status.py`:

a. Read `CLAIM.json` from each `current/<id>/` directory; expose `owner` per row.

b. Add `OWNER` to the table headers and per-row formatting. Foreign-state rows (inbox/pending-pr/done/blocked) format `owner = "—"`.

```python
from ralph_executor.claim import ClaimParseError, read_claim

# ... inside the row-building loop for current/ PBIs:
try:
    claim = read_claim(pbi_dir)
except ClaimParseError:
    owner = "<malformed>"
else:
    owner = claim.instance_id if claim else "—"
rows.append((..., owner))

# Header:
print(f"{'TARGET':<40} {'STATE':<10} {'ID':<10} {'TYPE':<10} {'OWNER':<14} {'AGE':<5} {'TITLE'}")
```

(Adjust to match the existing formatting style — keep alignment under 132 chars.)

- [ ] **Step 4: Run tests to verify pass**

```
uv run pytest tests/skills/test_ralph_status.py -v
```

Expected: PASS — owner test plus existing tests still green.

- [ ] **Step 5: Commit**

```
git add skills/ralph-status/scripts/status.py tests/skills/test_ralph_status.py
git commit -m "multi-ralph: ralph-status OWNER column from CLAIM.json"
```

---

## Task 16: `ralph-cancel` refuses on foreign CLAIM

**Files:**
- Modify: `skills/ralph-cancel/scripts/cancel.py`
- Test: extend `tests/skills/test_ralph_cancel.py`

- [ ] **Step 1: Write the failing test**

```python
def test_cancel_refuses_foreign_claim(tmp_path: Path) -> None:
    from ralph_executor.claim import ClaimInfo, write_claim
    from skills.ralph_cancel.scripts import cancel as cancel_mod

    _setup_queue_clone(tmp_path, instance_id="ralph-a")
    current = tmp_path / "queue-ralph-a" / ".ralph" / "current" / "WI-1234"
    _make_pbi_dir(current, pbi_id="WI-1234", pbi_type="feature")
    write_claim(current, ClaimInfo(instance_id="ralph-b", claimed_at=_NOW, hostname="box-b"))

    rc = cancel_mod.cancel(
        workspace_root=tmp_path, instance_id="ralph-a", pbi_id="WI-1234"
    )
    assert rc != 0
```

- [ ] **Step 2: Run test to verify failure**

```
uv run pytest tests/skills/test_ralph_cancel.py -v -k foreign
```

Expected: FAIL — cancel happily writes the sentinel.

- [ ] **Step 3: Implementation**

Inside `cancel.py`'s main flow, BEFORE writing the CANCEL sentinel:

```python
from ralph_executor.claim import ClaimParseError, read_claim

try:
    claim = read_claim(pbi_dir)
except ClaimParseError as exc:
    print(f"error: malformed CLAIM.json: {exc}", file=sys.stderr)
    return 2
if claim is not None and claim.instance_id != instance_id:
    print(
        f"error: PBI {pbi_id} is claimed by instance {claim.instance_id!r}, "
        f"not by {instance_id!r}. Use `ralph-recover` to force.",
        file=sys.stderr,
    )
    return 2
```

- [ ] **Step 4: Run tests to verify pass**

```
uv run pytest tests/skills/test_ralph_cancel.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```
git add skills/ralph-cancel/scripts/cancel.py tests/skills/test_ralph_cancel.py
git commit -m "multi-ralph: ralph-cancel refuses foreign CLAIM"
```

---

## Task 17: `ralph-promote` refuses cross-instance ownership transfer

**Files:**
- Modify: `skills/ralph-promote/scripts/promote.py`
- Test: extend `tests/skills/test_ralph_promote.py`

- [ ] **Step 1: Write the failing test**

```python
def test_promote_refuses_when_moving_foreign_claim_out_of_current(tmp_path: Path) -> None:
    from ralph_executor.claim import ClaimInfo, write_claim
    from skills.ralph_promote.scripts import promote as promote_mod

    _setup_queue_clone(tmp_path, instance_id="ralph-a")
    current = tmp_path / "queue-ralph-a" / ".ralph" / "current" / "WI-1234"
    _make_pbi_dir(current, pbi_id="WI-1234", pbi_type="feature")
    write_claim(current, ClaimInfo(instance_id="ralph-b", claimed_at=_NOW, hostname="box-b"))

    rc = promote_mod.promote(
        workspace_root=tmp_path,
        instance_id="ralph-a",
        pbi_id="WI-1234",
        from_state="current",
        to_state="inbox",
    )
    assert rc != 0
```

- [ ] **Step 2: Run test to verify failure**

Expected: FAIL — no ownership check.

- [ ] **Step 3: Implementation**

In `promote.py`'s main flow, before performing the move when `from_state == "current"`:

```python
from ralph_executor.claim import ClaimParseError, read_claim

try:
    claim = read_claim(pbi_dir)
except ClaimParseError as exc:
    print(f"error: malformed CLAIM.json: {exc}", file=sys.stderr)
    return 2
if claim is not None and claim.instance_id != instance_id:
    print(
        f"error: PBI {pbi_id} in current/ is owned by {claim.instance_id!r}; "
        f"use `ralph-recover` to take ownership before promoting.",
        file=sys.stderr,
    )
    return 2
```

- [ ] **Step 4: Run tests to verify pass**

```
uv run pytest tests/skills/test_ralph_promote.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```
git add skills/ralph-promote/scripts/promote.py tests/skills/test_ralph_promote.py
git commit -m "multi-ralph: ralph-promote refuses cross-instance ownership transfer"
```

---

## Task 18: `ralph-recover` skill

**Files:**
- Create: `skills/ralph-recover/SKILL.md`
- Create: `skills/ralph-recover/scripts/__init__.py` (empty)
- Create: `skills/ralph-recover/scripts/recover.py`
- Test: `tests/skills/test_ralph_recover.py`

- [ ] **Step 1: Write the failing test**

`tests/skills/test_ralph_recover.py`:

```python
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ralph_executor.claim import CLAIM_FILENAME, ClaimInfo, read_claim, write_claim
from skills.ralph_recover.scripts import recover as recover_mod


_NOW = datetime(2026, 5, 31, 12, 0, 0, tzinfo=UTC)


def test_recover_to_inbox_moves_pbi_and_resets_attempts(tmp_path: Path) -> None:
    _setup_queue_clone(tmp_path, instance_id="ralph-a")
    current = tmp_path / "queue-ralph-a" / ".ralph" / "current" / "WI-1234"
    _make_pbi_dir(current, pbi_id="WI-1234", pbi_type="feature", attempts=5)
    write_claim(current, ClaimInfo(instance_id="ralph-x", claimed_at=_NOW, hostname="dead-box"))

    rc = recover_mod.recover(
        workspace_root=tmp_path, instance_id="ralph-a", pbi_id="WI-1234", destination="inbox"
    )
    assert rc == 0
    inbox = tmp_path / "queue-ralph-a" / ".ralph" / "inbox" / "WI-1234"
    assert inbox.is_dir()
    assert not (inbox / CLAIM_FILENAME).exists()
    # attempts reset to 0
    entry = (inbox / "PBI.md").read_text(encoding="utf-8")
    assert "attempts: 0" in entry


def test_recover_refuses_if_pbi_not_in_current(tmp_path: Path) -> None:
    _setup_queue_clone(tmp_path, instance_id="ralph-a")
    rc = recover_mod.recover(
        workspace_root=tmp_path, instance_id="ralph-a", pbi_id="WI-404", destination="inbox"
    )
    assert rc != 0


def test_recover_refuses_when_halt_active(tmp_path: Path) -> None:
    _setup_queue_clone(tmp_path, instance_id="ralph-a")
    current = tmp_path / "queue-ralph-a" / ".ralph" / "current" / "WI-1234"
    _make_pbi_dir(current, pbi_id="WI-1234", pbi_type="feature")
    write_claim(current, ClaimInfo(instance_id="ralph-x", claimed_at=_NOW, hostname="dead"))
    halt = tmp_path / "queue-ralph-a" / ".ralph" / "state" / "halted"
    halt.parent.mkdir(parents=True, exist_ok=True)
    halt.write_text("halted\n", encoding="utf-8")
    rc = recover_mod.recover(
        workspace_root=tmp_path, instance_id="ralph-a", pbi_id="WI-1234", destination="inbox"
    )
    assert rc != 0


def test_recover_refuses_if_destination_collides(tmp_path: Path) -> None:
    _setup_queue_clone(tmp_path, instance_id="ralph-a")
    current = tmp_path / "queue-ralph-a" / ".ralph" / "current" / "WI-1234"
    inbox = tmp_path / "queue-ralph-a" / ".ralph" / "inbox" / "WI-1234"
    _make_pbi_dir(current, pbi_id="WI-1234", pbi_type="feature")
    _make_pbi_dir(inbox, pbi_id="WI-1234", pbi_type="feature")
    write_claim(current, ClaimInfo(instance_id="ralph-x", claimed_at=_NOW, hostname="dead"))
    rc = recover_mod.recover(
        workspace_root=tmp_path, instance_id="ralph-a", pbi_id="WI-1234", destination="inbox"
    )
    assert rc != 0
```

- [ ] **Step 2: Run tests to verify failure**

```
uv run pytest tests/skills/test_ralph_recover.py -v
```

Expected: FAIL — module / SKILL.md missing.

- [ ] **Step 3: Implementation**

`skills/ralph-recover/scripts/recover.py`:

```python
"""ralph-recover — manual claim recovery escape hatch.

Move a stuck ``current/<id>/`` PBI back to ``inbox/`` or ``blocked/``,
deleting its CLAIM.json and (for inbox) resetting its attempts counter.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from ralph_executor.claim import CLAIM_FILENAME, read_claim
from ralph_executor.queue_path import queue_clone_path


def _reset_attempts(entry_file: Path) -> None:
    text = entry_file.read_text(encoding="utf-8")
    new_text = re.sub(r"^attempts:\s*\d+", "attempts: 0", text, count=1, flags=re.MULTILINE)
    entry_file.write_text(new_text, encoding="utf-8")


def recover(
    *,
    workspace_root: Path,
    instance_id: str,
    pbi_id: str,
    destination: str,
) -> int:
    if destination not in {"inbox", "blocked"}:
        print(f"error: destination must be 'inbox' or 'blocked' (got {destination!r})", file=sys.stderr)
        return 2

    queue = queue_clone_path(workspace_root, instance_id)
    halt = queue / ".ralph" / "state" / "halted"
    if halt.is_file():
        print("error: halt sentinel is active; refusing to operate", file=sys.stderr)
        return 2

    src = queue / ".ralph" / "current" / pbi_id
    dst = queue / ".ralph" / destination / pbi_id
    if not src.is_dir():
        print(f"error: PBI {pbi_id!r} not found in current/", file=sys.stderr)
        return 2
    if dst.exists():
        print(f"error: destination already exists at {dst}", file=sys.stderr)
        return 2

    claim = read_claim(src)
    if claim is not None:
        print(
            f"recover: PBI {pbi_id} was claimed by {claim.instance_id!r} at {claim.claimed_at.isoformat()}",
            file=sys.stderr,
        )

    # 1. Delete CLAIM.json so the destination is clean.
    claim_file = src / CLAIM_FILENAME
    if claim_file.is_file():
        claim_file.unlink()

    # 2. git mv current/<id>/ <destination>/<id>/ — fall back to shutil if not tracked.
    dst.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["git", "-C", str(queue), "mv", str(src.relative_to(queue)), str(dst.relative_to(queue))],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        # Fall back to plain rename for untracked dirs (legacy state).
        shutil.move(str(src), str(dst))
        subprocess.run(["git", "-C", str(queue), "add", str(dst.relative_to(queue))], check=True)

    # 3. Reset attempts when going back to inbox.
    if destination == "inbox":
        for name in ("PBI.md", "BUG.md", "FEEDBACK.md"):
            entry = dst / name
            if entry.is_file():
                _reset_attempts(entry)
                break

    # 4. Append HISTORY.md recovery note.
    now_iso = datetime.now(tz=UTC).replace(microsecond=0).isoformat()
    owner = claim.instance_id if claim else "<no claim>"
    history = dst / "HISTORY.md"
    entry = f"\n## Recovered from instance {owner} by operator — {now_iso}\n"
    existing = history.read_text(encoding="utf-8") if history.is_file() else ""
    history.write_text(existing + entry, encoding="utf-8")

    # 5. Commit + push.
    subprocess.run(
        ["git", "-C", str(queue), "add", str(dst.relative_to(queue))],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(queue), "commit", "-m", f"chore(queue): recover {pbi_id} from {owner}"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(queue), "push", "origin", "HEAD"], check=False
    )  # push failure is recoverable; loop will retry
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ralph-recover")
    parser.add_argument("--pbi-id", required=True)
    parser.add_argument("--to", dest="destination", choices=("inbox", "blocked"), required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    from ralph_executor.config import load_config

    args = _build_parser().parse_args(argv)
    cfg = load_config()
    return recover(
        workspace_root=cfg.workspace_root,
        instance_id=cfg.instance_id,
        pbi_id=args.pbi_id,
        destination=args.destination,
    )


if __name__ == "__main__":
    raise SystemExit(main())
```

`skills/ralph-recover/SKILL.md` — short manual page describing usage; copy the structure from an existing skill (e.g. `skills/ralph-cancel/SKILL.md`).

- [ ] **Step 4: Run tests to verify pass**

```
uv run pytest tests/skills/test_ralph_recover.py -v
```

Expected: PASS — 4 tests.

- [ ] **Step 5: Commit**

```
git add skills/ralph-recover/ tests/skills/test_ralph_recover.py
git commit -m "multi-ralph: ralph-recover manual claim-recovery skill"
```

---

## Task 19: Two-instance integration tests

**Files:**
- Create: `tests/executor/test_multi_ralph_integration.py`

- [ ] **Step 1: Write the failing tests**

`tests/executor/test_multi_ralph_integration.py`:

```python
"""Cross-instance simulation: two ExecutorConfigs against one bare queue repo."""

from __future__ import annotations

import dataclasses
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ralph_executor.claim import read_claim
from ralph_executor.config import ExecutorConfig
from ralph_executor.loop import HaltedError, iterate_once
from ralph_executor.queue_path import queue_clone_path


@pytest.fixture
def two_ralphs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[ExecutorConfig, ExecutorConfig, Path]:
    """Spin up a bare queue repo + two ExecutorConfigs (ralph-a / ralph-b).

    Returns (cfg_a, cfg_b, queue_origin_path).
    """
    # ... build a bare queue repo containing .ralph/inbox/{WI-1, WI-2}
    # ... build cfg_a (workspace_root=tmp_path/'wsa', instance_id='ralph-a')
    # ... build cfg_b (workspace_root=tmp_path/'wsb', instance_id='ralph-b')
    # ... monkeypatch target_clone.ensure_clone to a no-op fake
    # ... monkeypatch ensure_worktree to a no-op fake
    raise NotImplementedError("Fill in fixture as part of Step 3")


def test_T1_two_ralphs_claim_distinct_pbis(two_ralphs):
    cfg_a, cfg_b, origin = two_ralphs
    iterate_once(cfg_a)
    iterate_once(cfg_b)
    queue_a = queue_clone_path(cfg_a.workspace_root, cfg_a.instance_id) / ".ralph" / "current"
    queue_b = queue_clone_path(cfg_b.workspace_root, cfg_b.instance_id) / ".ralph" / "current"
    # After pull, each clone sees both current/ dirs.
    assert (queue_a / "WI-1").is_dir() or (queue_a / "WI-2").is_dir()
    assert (queue_b / "WI-1").is_dir() or (queue_b / "WI-2").is_dir()
    # Each PBI has a CLAIM.json with a different owner.
    owners = set()
    for cur in (queue_a, queue_b):
        for pbi in cur.iterdir():
            claim = read_claim(pbi)
            if claim is not None:
                owners.add(claim.instance_id)
    assert owners == {"ralph-a", "ralph-b"}


def test_T2_claim_race_loser_recovers(two_ralphs):
    """Both ralphs see one inbox PBI; one wins, one gets PushRebaseConflict."""
    cfg_a, cfg_b, origin = two_ralphs
    _strip_inbox_to_one(origin, keep="WI-1")
    iterate_once(cfg_a)
    iterate_once(cfg_b)
    # One claim succeeded; the other observed PushRebaseConflict and got
    # back a recoverable IterationResult on the next pass.
    result_b_followup = iterate_once(cfg_b)
    assert result_b_followup.outcome in ("idle", "uncommitted_source", "push_conflict")


def test_T3_sweep_does_not_clobber_other_claims(two_ralphs):
    # ralph-a's PBI promoted to pending-pr/ — verify ralph-b's sweep does
    # not move ralph-a's PBI.
    pytest.skip("Fill in once sweep stub fixture is added")


def test_T4_halt_visible_to_other_instance(two_ralphs):
    cfg_a, cfg_b, origin = two_ralphs
    halt = origin / ".ralph" / "state" / "halted"
    halt.parent.mkdir(parents=True, exist_ok=True)
    halt.write_text("halted\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(origin), "add", "."], check=True)
    subprocess.run(["git", "-C", str(origin), "commit", "-m", "halt"], check=True)
    with pytest.raises(HaltedError):
        iterate_once(cfg_b)


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

- [ ] **Step 2: Run tests to verify failure**

```
uv run pytest tests/executor/test_multi_ralph_integration.py -v
```

Expected: FAIL — fixture raises `NotImplementedError`.

- [ ] **Step 3: Build the fixture**

Implement `two_ralphs` by:
1. `git init --bare <tmp_path>/queue-origin.git`
2. Clone, write `.ralph/inbox/WI-1/{PBI.md,HISTORY.md}` and `WI-2/...` (use the existing `_make_pbi_dir` helper), commit, push.
3. Build two `ExecutorConfig`s using `_make_fake_config` with distinct `instance_id`s and distinct `workspace_root`s, both pointing at the bare repo URL.
4. Monkeypatch `target_clone.ensure_clone` and `worktree.ensure_worktree` with no-op fakes so iterations do not actually clone GitHub.

- [ ] **Step 4: Run tests to verify pass**

```
uv run pytest tests/executor/test_multi_ralph_integration.py -v
```

Expected: PASS for T1, T2, T4, T5; T3 is `pytest.skip` (sweep coverage is already exercised by Task 19-style direct unit tests in the existing sweep test file).

- [ ] **Step 5: Commit**

```
git add tests/executor/test_multi_ralph_integration.py
git commit -m "multi-ralph: integration tests T1, T2, T4, T5 across two instances"
```

---

## Task 20: Documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/runbooks/ralph-setup.md`

- [ ] **Step 1: README updates**

Replace the existing "Running multiple ralphs" section with content describing the new model:

```markdown
## Running multiple ralphs against the same queue

Ralph supports running N instances against the same `queue_repo`, each
identified by a stable, unique `instance_id`. Coordination is purely via
git pushes on the queue branch.

### Identity

Set `instance_id` once per ralph. Resolution order (first match wins):

1. `--instance-id <name>` CLI flag
2. `RALPH_INSTANCE_ID` environment variable
3. `instance_id` in `~/.ralph/config.toml` (written by `ralph-executor init`)
4. Sanitised hostname

The id must match `^[a-z0-9][a-z0-9_-]{0,62}$`. Hostnames containing dots
or uppercase (e.g. `MyBox.local`) are sanitised automatically to
`mybox-local`.

### Workspace layout

Each ralph gets its own queue clone:

  ```
  <workspace_root>/queue-<instance-id>/
  ```

Target clones (`<workspace_root>/clones/<owner>/<name>/`) remain local to
the host. The current design assumes one ralph per host.

### Claim ownership

When ralph claims an inbox PBI it writes `current/<id>/CLAIM.json` in
the same commit as the `git mv inbox → current`. Operator skills
(`ralph-cancel`, `ralph-promote`) refuse cross-instance operations; use
`ralph-recover` to forcibly take over or abandon another instance's claim.

### Safety

A workspace lockfile (`.ralph.lock`) at the queue-clone root prevents
the same operator from accidentally launching two ralphs against the
same workspace.

### Upgrade procedure

Before upgrading the executor binary, drain `current/` (let in-flight
PBIs complete or move them to inbox via `ralph-promote`). On the next
launch the legacy `<workspace_root>/queue/` will be renamed atomically
to `<workspace_root>/queue-<instance-id>/`.
```

- [ ] **Step 2: Runbook updates**

In `docs/runbooks/ralph-setup.md` add `instance_id` to the config-knob table and document the new lockfile location.

- [ ] **Step 3: Commit**

```
git add README.md docs/runbooks/ralph-setup.md
git commit -m "multi-ralph: docs — README + runbook updates"
```

---

## Final sweep

- [ ] **Run the full test suite**

```
uv run pytest -v
```

All green.

- [ ] **Run lint + types**

```
uv run ruff check .
uv run ruff format --check .
uv run mypy ralph_executor scripts tests
```

All green.

- [ ] **Push**

```
git push origin main
```
