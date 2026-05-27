# Multi-Repo Checkout (PBI 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ralph's loop reads `pbi.target_repo` and operates dynamically on that repo: clone into `$RALPH_WORKSPACE/clones/<owner>-<name>/` (once per target, fetched each iteration), create the per-PBI feature branch worktree inside the clone, spawn Claude there with `GH_OWNER` set per-subprocess from the target's owner. Sweep applies the same env-bridge when calling pr-github show.

**Architecture:** Three new modules (`url_utils`, `target_clone`, plus `git_ops.clone` helper) plus targeted modifications to `loop._claim_pbi`, `claude_spawn`, `worktree`, `config`, `sweep/runner`, and the `PBI` dataclass. The queue worktree at `<ralph-repo>/.ralph-work/queue/` is unchanged — ralph repo continues to own queue state. The CODE worktree moves out of ralph's checkout into a clone of the target repo.

**Tech Stack:** Python 3.12, `subprocess` (clone/fetch), `urllib.parse`, pytest, ruff, mypy strict.

**Spec:** `docs/superpowers/specs/2026-05-27-multi-repo-checkout-design.md`
**depends_on:** `["RALPH-PBI-TARGET-REPO-FIELD"]` — strict.

> **Workflow note:** all `git` commands use `git -C /c/Users/gethi/ralph-queue ...`. Commits go via the separate clone, NOT ralph's running checkout at `C:/Users/gethi/source/ralph`. Pull-rebase before every commit (ralph is actively pushing).

---

## Confidence per task

All tasks ≥ 90%. Pre-flighted against `types.py` (PBI dataclass at line 21), `loop.py:290`, `worktree.py:28-72`, `claude_spawn.py:434`, `config.py:181`.

| Task | % | Notes |
|---|---|---|
| 1. `url_utils.py` | 96% | Pure-Python URL parsing, stdlib only. Well-bounded; tests cover all branches. |
| 2. `target_clone.py` | 92% | subprocess for clone/fetch wrapped in TargetUnreachable; established mocking pattern. |
| 3. `git_ops.clone` helper | 95% | Mirrors existing `fetch` + `pull` patterns at lines 52, 57. Adds timeout kwarg. |
| 4. `_resolve_path` + `workspace_root` | 95% | Mirrors `_resolve_str` pattern at config.py:181. `Path.expanduser()` handles `~/`. |
| 5. `worktree.work_worktree_path` signature refactor | 91% | Multi-call-site change. Mitigation: Step 5.1 greps callers + lists each; Step 5.5 verifies no missed callsite. |
| 6. PBI dataclass fields | 96% | Mechanical add with defaults; existing positional constructions keep compiling. |
| 7. `loop._claim_pbi` multi-target | 92% | Largest blast radius. Mitigation: broken into 3 sub-steps (read field → parse + host check → ensure clone + worktree); failing tests for each scenario individually before the impl combines them. |
| 8. `claude_spawn` cwd + GH_OWNER | 95% | Small env + cwd change at line 434-443. Test pattern from existing claude_spawn tests. |
| 9. Sweep `GH_OWNER` override | 93% | Small env injection; existing sweep test patterns cover. |
| 10. Full-suite gate | 90% | Could surface latent PBI / SweepConfig literal constructions. Mitigation: Step 10.1 grep finds them; defaults make most callers compile without changes. |

---

## File map

| File | Action | Responsibility |
|---|---|---|
| `ralph_executor/url_utils.py` | Create | `parse_target_repo(url) -> TargetRepoInfo` + dataclass |
| `ralph_executor/target_clone.py` | Create | `ensure_clone` + `TargetClone` + `TargetUnreachable` |
| `ralph_executor/git_ops.py` | Modify (line 52-65 area) | New `clone(url, dest, *, timeout=120.0)` helper |
| `ralph_executor/worktree.py` | Modify (line 38) | `work_worktree_path` takes `clone_root` instead of `repo_root` |
| `ralph_executor/config.py` | Modify (line 181 area + ExecutorConfig dataclass) | `_resolve_path` helper + `workspace_root` field |
| `ralph_executor/setup_cmds.py` | Modify (CONFIG_TOML_STUB) | Document `workspace_root` |
| `ralph_executor/types.py` | Modify (line 41 area) | `PBI.target_info: TargetRepoInfo \| None = None`; `PBI.work_worktree: Path \| None = None`; `PBI.target_repo: str = ""` |
| `ralph_executor/loop.py` | Modify (line 286-330 area) | `_claim_pbi` reads target_repo, ensures clone, creates worktree in clone; `_ClaimError`; `iterate_once` catches and moves PBI to blocked/ |
| `ralph_executor/claude_spawn.py` | Modify (line 434-443) | `cwd = str(pbi.work_worktree)`; `env["GH_OWNER"] = pbi.target_info.owner` |
| `ralph_executor/sweep/runner.py` | Modify | Inject `GH_OWNER` per pending-pr PBI's target when calling pr-github show |
| `tests/executor/test_url_utils.py` | Create | parse_target_repo cases |
| `tests/executor/test_target_clone.py` | Create | clone-once + fetch-each-iter + failures |
| `tests/executor/test_git_ops.py` | Modify | clone helper test |
| `tests/executor/test_worktree.py` | Modify | new signature |
| `tests/executor/test_config_toml.py` | Modify | workspace_root layering |
| `tests/executor/test_loop.py` | Modify | multi-target claim scenarios |
| `tests/executor/test_claude_spawn.py` | Modify | GH_OWNER + cwd assertions |
| `tests/executor/sweep/test_runner.py` | Modify | GH_OWNER injection per pending-pr PBI |

---

## Task 1: `url_utils.py`

**Files:**
- Create: `ralph_executor/url_utils.py`
- Create: `tests/executor/test_url_utils.py`

- [ ] **Step 1.1: Write the failing tests**

Create `tests/executor/test_url_utils.py`:

```python
"""Tests for ralph_executor.url_utils."""

from __future__ import annotations

import pytest

from ralph_executor.url_utils import TargetRepoInfo, parse_target_repo


def test_parse_github_https_url() -> None:
    info = parse_target_repo("https://github.com/emp3thy/ralph")
    assert isinstance(info, TargetRepoInfo)
    assert info.host == "github.com"
    assert info.owner == "emp3thy"
    assert info.name == "ralph"
    assert info.slug == "emp3thy-ralph"
    assert info.clone_url == "https://github.com/emp3thy/ralph.git"


def test_parse_strips_git_suffix() -> None:
    info = parse_target_repo("https://github.com/emp3thy/ralph.git")
    assert info.name == "ralph"
    assert info.clone_url == "https://github.com/emp3thy/ralph.git"


def test_parse_rejects_http_scheme() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        parse_target_repo("http://github.com/x/y")


def test_parse_rejects_missing_host() -> None:
    with pytest.raises(ValueError, match="host"):
        parse_target_repo("https:///x/y")


def test_parse_rejects_path_with_less_than_two_segments() -> None:
    with pytest.raises(ValueError, match="path"):
        parse_target_repo("https://github.com/onlyone")


def test_parse_rejects_no_path() -> None:
    with pytest.raises(ValueError, match="path"):
        parse_target_repo("https://github.com")


def test_parse_ado_url_extracts_first_two_path_segments() -> None:
    """Documented limitation: ADO URLs parse as github-shaped.
    Acceptable because host_select rejects non-github hosts upstream."""
    info = parse_target_repo("https://dev.azure.com/myorg/myproj/_git/myrepo")
    assert info.host == "dev.azure.com"
    assert info.owner == "myorg"
    assert info.name == "myproj"
```

- [ ] **Step 1.2: Run failing tests**

```bash
cd /c/Users/gethi/ralph-queue
uv run pytest tests/executor/test_url_utils.py -v
```

Expected: ALL FAIL with `ImportError: cannot import 'TargetRepoInfo'`.

- [ ] **Step 1.3: Implement `ralph_executor/url_utils.py`**

```python
"""Parse + reshape target_repo URLs.

Used by:
- ralph_executor.target_clone to determine clone destination
- ralph_executor.loop._claim_pbi to host-check and propagate target identity
- ralph_executor.claude_spawn to set GH_OWNER per subprocess
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass


@dataclass(frozen=True)
class TargetRepoInfo:
    """Parsed view of a target_repo URL.

    Example for ``https://github.com/emp3thy/ralph``:
      host:  ``"github.com"``
      owner: ``"emp3thy"``
      name:  ``"ralph"`` (no .git suffix)
    """

    host: str
    owner: str
    name: str

    @property
    def clone_url(self) -> str:
        """The URL to pass to ``git clone``. Always re-adds the .git suffix."""
        return f"https://{self.host}/{self.owner}/{self.name}.git"

    @property
    def slug(self) -> str:
        """Filesystem-safe identifier ``<owner>-<name>``."""
        return f"{self.owner}-{self.name}"


def parse_target_repo(url: str) -> TargetRepoInfo:
    """Parse an HTTPS URL into TargetRepoInfo.

    Raises ValueError on:
      - non-HTTPS scheme
      - missing host (netloc)
      - path with fewer than 2 segments (owner + name required)

    ADO URL shapes (``https://dev.azure.com/myorg/myproj/_git/myrepo``)
    parse as ``owner=myorg, name=myproj`` — known wrong for ADO. Out
    of scope: host_select rejects non-github hosts upstream.
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(f"target_repo must be HTTPS, got {url!r}")
    if not parsed.netloc:
        raise ValueError(f"target_repo has no host: {url!r}")
    segments = [p for p in parsed.path.split("/") if p]
    if len(segments) < 2:
        raise ValueError(f"target_repo path must include owner + name: {url!r}")
    owner = segments[0]
    name = segments[1].removesuffix(".git")
    return TargetRepoInfo(host=parsed.netloc, owner=owner, name=name)
```

- [ ] **Step 1.4: Run tests — verify pass**

```bash
uv run pytest tests/executor/test_url_utils.py -v
```

Expected: 7 PASS.

- [ ] **Step 1.5: Run ruff + mypy**

```bash
uv run ruff check ralph_executor/url_utils.py tests/executor/test_url_utils.py
uv run mypy --strict ralph_executor/url_utils.py
```

- [ ] **Step 1.6: Commit**

```bash
git -C /c/Users/gethi/ralph-queue pull --rebase origin ralph-queue
git -C /c/Users/gethi/ralph-queue add ralph_executor/url_utils.py tests/executor/test_url_utils.py
git -C /c/Users/gethi/ralph-queue commit -m "feat(url-utils): parse_target_repo + TargetRepoInfo"
git -C /c/Users/gethi/ralph-queue push origin ralph-queue
```

---

## Task 2: `git_ops.clone` helper

**Files:**
- Modify: `ralph_executor/git_ops.py` (line 52-65 area; after `pull`)
- Modify: `tests/executor/test_git_ops.py`

- [ ] **Step 2.1: Write the failing test**

In `tests/executor/test_git_ops.py`, append:

```python
def test_clone_invokes_git_clone_with_url_and_dest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """git_ops.clone delegates to `git clone <url> <dest>`."""
    captured: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.append(list(argv))
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("ralph_executor.git_ops.subprocess.run", fake_run)
    dest = tmp_path / "newclone"

    from ralph_executor.git_ops import clone
    clone("https://github.com/x/y.git", dest)

    assert len(captured) == 1
    assert captured[0][:2] == ["git", "clone"]
    assert "https://github.com/x/y.git" in captured[0]
    assert str(dest) in captured[0]


def test_clone_raises_GitCommandError_on_non_zero_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=argv, returncode=128, stdout="", stderr="fatal: repository not found"
        )

    monkeypatch.setattr("ralph_executor.git_ops.subprocess.run", fake_run)

    from ralph_executor.git_ops import GitCommandError, clone
    with pytest.raises(GitCommandError, match="repository not found"):
        clone("https://github.com/missing/repo.git", tmp_path / "x")


def test_clone_passes_timeout_to_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured_timeout: list[float | None] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured_timeout.append(kwargs.get("timeout"))
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("ralph_executor.git_ops.subprocess.run", fake_run)
    from ralph_executor.git_ops import clone
    clone("https://github.com/x/y.git", tmp_path / "x", timeout=60.0)

    assert captured_timeout == [60.0]
```

- [ ] **Step 2.2: Run failing tests**

```bash
uv run pytest tests/executor/test_git_ops.py -k "clone" -v
```

Expected: 3 FAIL with `ImportError: cannot import 'clone' from 'ralph_executor.git_ops'`.

- [ ] **Step 2.3: Implement `clone` in `ralph_executor/git_ops.py`**

After the existing `pull` function (around line 57), add:

```python
def clone(url: str, dest: Path, *, timeout: float = 120.0) -> None:
    """Run ``git clone <url> <dest>`` with a wall-clock timeout.

    Full (non-shallow) clone — ralph needs full history for ``git diff main..``
    and sweep-side investigation. Raises GitCommandError on non-zero exit
    or subprocess timeout.
    """
    result = subprocess.run(
        ["git", "clone", url, str(dest)],
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise GitCommandError(
            f"git clone {url} -> {dest} exited {result.returncode}: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
```

If `GitCommandError` is not imported at the test top, add to the test file imports:

```python
from ralph_executor.git_ops import GitCommandError
```

- [ ] **Step 2.4: Run tests — verify pass**

```bash
uv run pytest tests/executor/test_git_ops.py -k "clone" -v
```

Expected: 3 PASS.

- [ ] **Step 2.5: Run ruff + mypy**

```bash
uv run ruff check ralph_executor/git_ops.py tests/executor/test_git_ops.py
uv run mypy --strict ralph_executor/git_ops.py
```

- [ ] **Step 2.6: Commit**

```bash
git -C /c/Users/gethi/ralph-queue pull --rebase origin ralph-queue
git -C /c/Users/gethi/ralph-queue add ralph_executor/git_ops.py tests/executor/test_git_ops.py
git -C /c/Users/gethi/ralph-queue commit -m "feat(git-ops): add clone helper with timeout"
git -C /c/Users/gethi/ralph-queue push origin ralph-queue
```

---

## Task 3: `target_clone.py`

**Files:**
- Create: `ralph_executor/target_clone.py`
- Create: `tests/executor/test_target_clone.py`

- [ ] **Step 3.1: Write the failing tests**

Create `tests/executor/test_target_clone.py`:

```python
"""Tests for ralph_executor.target_clone."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ralph_executor.git_ops import GitCommandError
from ralph_executor.target_clone import (
    TargetClone,
    TargetUnreachable,
    ensure_clone,
)
from ralph_executor.url_utils import parse_target_repo


def test_ensure_clone_runs_git_clone_on_first_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """First time a target is seen: git clone into workspace_root/clones/<slug>/."""
    calls: list[tuple[str, str]] = []

    def fake_clone(url: str, dest: Path, **kwargs: Any) -> None:
        calls.append((url, str(dest)))
        dest.mkdir(parents=True)
        (dest / ".git").mkdir()

    monkeypatch.setattr("ralph_executor.target_clone.git_ops.clone", fake_clone)
    info = parse_target_repo("https://github.com/emp3thy/ralph")
    clone = ensure_clone(info, workspace_root=tmp_path)

    expected_dir = tmp_path / "clones" / "emp3thy-ralph"
    assert isinstance(clone, TargetClone)
    assert clone.info == info
    assert clone.clone_root == expected_dir
    assert calls == [("https://github.com/emp3thy/ralph.git", str(expected_dir))]


def test_ensure_clone_runs_git_fetch_when_clone_already_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Second call (or subsequent iteration): git fetch origin, no clone."""
    clone_dir = tmp_path / "clones" / "emp3thy-ralph"
    clone_dir.mkdir(parents=True)
    (clone_dir / ".git").mkdir()

    fetch_calls: list[Path] = []
    clone_calls: list[str] = []

    monkeypatch.setattr(
        "ralph_executor.target_clone.git_ops.fetch",
        lambda repo, remote="origin": fetch_calls.append(repo),
    )
    monkeypatch.setattr(
        "ralph_executor.target_clone.git_ops.clone",
        lambda url, dest, **kw: clone_calls.append(url),
    )

    info = parse_target_repo("https://github.com/emp3thy/ralph")
    clone = ensure_clone(info, workspace_root=tmp_path)

    assert fetch_calls == [clone_dir]
    assert clone_calls == []
    assert clone.clone_root == clone_dir


def test_ensure_clone_raises_target_unreachable_on_clone_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_clone(url: str, dest: Path, **kwargs: Any) -> None:
        raise GitCommandError("authentication failed")

    monkeypatch.setattr("ralph_executor.target_clone.git_ops.clone", fake_clone)
    info = parse_target_repo("https://github.com/emp3thy/ralph")

    with pytest.raises(TargetUnreachable, match="authentication failed"):
        ensure_clone(info, workspace_root=tmp_path)


def test_ensure_clone_raises_target_unreachable_on_fetch_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clone_dir = tmp_path / "clones" / "emp3thy-ralph"
    clone_dir.mkdir(parents=True)
    (clone_dir / ".git").mkdir()

    def fake_fetch(repo: Path, remote: str = "origin") -> None:
        raise GitCommandError("network unreachable")

    monkeypatch.setattr("ralph_executor.target_clone.git_ops.fetch", fake_fetch)
    info = parse_target_repo("https://github.com/emp3thy/ralph")

    with pytest.raises(TargetUnreachable, match="network unreachable"):
        ensure_clone(info, workspace_root=tmp_path)


def test_ensure_clone_creates_clones_subdir_if_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """workspace_root may not have a clones/ subdir yet on first call."""
    calls: list[Path] = []

    def fake_clone(url: str, dest: Path, **kwargs: Any) -> None:
        # The parent (clones/) must exist before git clone runs.
        assert dest.parent.is_dir(), f"parent {dest.parent} does not exist"
        calls.append(dest)
        dest.mkdir(parents=True)
        (dest / ".git").mkdir()

    monkeypatch.setattr("ralph_executor.target_clone.git_ops.clone", fake_clone)
    info = parse_target_repo("https://github.com/emp3thy/ralph")

    # tmp_path exists but tmp_path/clones doesn't yet.
    ensure_clone(info, workspace_root=tmp_path)

    assert (tmp_path / "clones").is_dir()
    assert len(calls) == 1
```

- [ ] **Step 3.2: Run failing tests**

```bash
uv run pytest tests/executor/test_target_clone.py -v
```

Expected: 5 FAIL with `ImportError: cannot import 'TargetClone' from 'ralph_executor.target_clone'`.

- [ ] **Step 3.3: Implement `ralph_executor/target_clone.py`**

```python
"""Manage clone-once + fetch-each-iter lifecycle per target repo.

Given a TargetRepoInfo and operator workspace_root:
- If <workspace_root>/clones/<info.slug>/ doesn't exist: git clone
- If exists: git fetch origin (refresh main + branches)
- Any git failure -> TargetUnreachable (caller decides — move to blocked
  typically)

The clone is full (not shallow) — ralph needs ``git diff main..`` and
full history for sweep-side investigation.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ralph_executor import git_ops
from ralph_executor.url_utils import TargetRepoInfo


class TargetUnreachable(RuntimeError):
    """Raised when clone or fetch of a target repo fails.

    Caller (typically ``loop._claim_pbi``) maps to ``_ClaimError`` and
    moves the PBI to ``blocked/`` with the error message in HISTORY.md.
    """


@dataclass(frozen=True)
class TargetClone:
    """A target-repo clone on the operator's machine."""

    info: TargetRepoInfo
    clone_root: Path  # filesystem path of the clone (its .git lives here)


def ensure_clone(info: TargetRepoInfo, workspace_root: Path) -> TargetClone:
    """Return a TargetClone, cloning or fetching as needed.

    Disk layout:
      <workspace_root>/clones/<info.slug>/

    First call for a given target: ``git clone <info.clone_url> <root>``.
    Subsequent calls: ``git fetch origin`` inside <root>.

    Creates ``workspace_root/clones/`` if missing. Raises
    ``TargetUnreachable`` on any git failure.
    """
    clones_dir = workspace_root / "clones"
    clones_dir.mkdir(parents=True, exist_ok=True)
    clone_root = clones_dir / info.slug

    if (clone_root / ".git").is_dir():
        try:
            git_ops.fetch(clone_root)
        except git_ops.GitCommandError as exc:
            raise TargetUnreachable(
                f"git fetch failed for {info.clone_url}: {exc}"
            ) from exc
    else:
        try:
            git_ops.clone(info.clone_url, clone_root)
        except git_ops.GitCommandError as exc:
            raise TargetUnreachable(
                f"git clone failed for {info.clone_url}: {exc}"
            ) from exc

    return TargetClone(info=info, clone_root=clone_root)
```

- [ ] **Step 3.4: Run tests — verify pass**

```bash
uv run pytest tests/executor/test_target_clone.py -v
```

Expected: 5 PASS.

- [ ] **Step 3.5: Run ruff + mypy**

```bash
uv run ruff check ralph_executor/target_clone.py tests/executor/test_target_clone.py
uv run mypy --strict ralph_executor/target_clone.py
```

- [ ] **Step 3.6: Commit**

```bash
git -C /c/Users/gethi/ralph-queue pull --rebase origin ralph-queue
git -C /c/Users/gethi/ralph-queue add ralph_executor/target_clone.py tests/executor/test_target_clone.py
git -C /c/Users/gethi/ralph-queue commit -m "feat(target-clone): ensure_clone + TargetClone + TargetUnreachable"
git -C /c/Users/gethi/ralph-queue push origin ralph-queue
```

---

## Task 4: `_resolve_path` + `workspace_root` in config

**Files:**
- Modify: `ralph_executor/config.py` (after `_resolve_float` at line 232; add field to ExecutorConfig; add resolver call in load_config; add to `_TOML_KNOWN_KEYS`)
- Modify: `ralph_executor/setup_cmds.py` (CONFIG_TOML_STUB)
- Modify: `tests/executor/test_config_toml.py`

- [ ] **Step 4.1: Add new env var to `clean_env` fixture cleanup**

In `tests/executor/test_config_toml.py`, find the `clean_env` fixture. Append `"RALPH_WORKSPACE"` to the env-var cleanup tuple.

- [ ] **Step 4.2: Write failing tests**

Append to `tests/executor/test_config_toml.py`:

```python
def test_workspace_root_defaults_to_home_ralph_workspaces(clean_env: Path) -> None:
    cfg = load_config()
    assert cfg.workspace_root == Path.home() / "ralph-workspaces"


def test_workspace_root_from_toml(clean_env: Path, tmp_path: Path) -> None:
    target = tmp_path / "my-workspaces"
    _write_toml(clean_env, f'workspace_root = "{target}"\n')
    cfg = load_config()
    assert cfg.workspace_root == target


def test_workspace_root_env_wins_over_toml(
    clean_env: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_toml(clean_env, f'workspace_root = "{tmp_path / "from-toml"}"\n')
    monkeypatch.setenv("RALPH_WORKSPACE", str(tmp_path / "from-env"))
    cfg = load_config()
    assert cfg.workspace_root == tmp_path / "from-env"


def test_workspace_root_tilde_expansion(
    clean_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """~/ in TOML or env must be expanded relative to the home dir."""
    monkeypatch.setenv("RALPH_WORKSPACE", "~/my-ralph-stuff")
    cfg = load_config()
    assert cfg.workspace_root == Path.home() / "my-ralph-stuff"


def test_workspace_root_non_string_toml_rejected(clean_env: Path) -> None:
    _write_toml(clean_env, "workspace_root = 42\n")
    with pytest.raises(ConfigError, match="workspace_root"):
        load_config()
```

- [ ] **Step 4.3: Run failing tests**

```bash
uv run pytest tests/executor/test_config_toml.py -k "workspace_root" -v
```

Expected: 5 FAIL with `AttributeError: 'ExecutorConfig' object has no attribute 'workspace_root'`.

- [ ] **Step 4.4: Implement `_resolve_path` helper**

In `ralph_executor/config.py`, after `_resolve_float` (around line 232), add:

```python
def _resolve_path(
    *, name: str, env_name: str, toml_value: Any, default: Path, source_label: str
) -> Path:
    """env > toml > default for a Path-valued knob.

    Both env and TOML values are strings; ``~`` is expanded via
    ``Path.expanduser()``. TOML must be a string; bools, ints, etc.
    raise ConfigError.
    """
    raw_env = os.environ.get(env_name)
    if raw_env is not None and raw_env.strip() != "":
        return Path(raw_env.strip()).expanduser()
    if toml_value is None:
        return default
    if not isinstance(toml_value, str):
        raise ConfigError(
            f"{source_label}: {name} must be a string path, "
            f"got {type(toml_value).__name__}"
        )
    return Path(toml_value).expanduser()
```

- [ ] **Step 4.5: Add `workspace_root` to `_TOML_KNOWN_KEYS`**

Find `_TOML_KNOWN_KEYS` (around line 59). Append `"workspace_root"`.

- [ ] **Step 4.6: Add `workspace_root` field to `ExecutorConfig`**

At the end of the dataclass (after the last existing field with default), add:

```python
    # Where target-repo clones live. Default: $HOME/ralph-workspaces.
    # Each target gets a subdir: <workspace_root>/clones/<owner>-<name>/.
    # Override via TOML key `workspace_root` or env `RALPH_WORKSPACE`.
    workspace_root: Path = field(
        default_factory=lambda: Path.home() / "ralph-workspaces"
    )
```

Add `from dataclasses import field` to the imports if not already present.

- [ ] **Step 4.7: Wire resolver into `load_config()`**

After the existing resolver calls in `load_config()`, add:

```python
    workspace_root = _resolve_path(
        name="workspace_root",
        env_name="RALPH_WORKSPACE",
        toml_value=toml_overrides.get("workspace_root"),
        default=Path.home() / "ralph-workspaces",
        source_label=source_label,
    )
```

Pass to `ExecutorConfig(...)` constructor:

```python
    return ExecutorConfig(
        # ... existing kwargs ...
        workspace_root=workspace_root,
    )
```

- [ ] **Step 4.8: Update `CONFIG_TOML_STUB`**

In `ralph_executor/setup_cmds.py`, append to `CONFIG_TOML_STUB`:

```
#
# Where target-repo clones live. Default: ~/ralph-workspaces.
# Each target gets a subdir: <workspace_root>/clones/<owner>-<name>/.
# Env override: RALPH_WORKSPACE.
# workspace_root = "~/ralph-workspaces"
```

- [ ] **Step 4.9: Update `cfg_for_repo` fixture**

In `tests/executor/conftest.py`, add `workspace_root=Path.home() / "ralph-workspaces"` to the `cfg_for_repo` `ExecutorConfig(...)` construction (or use `tmp_path` for test isolation; check what the existing pattern does).

- [ ] **Step 4.10: Run tests — verify pass**

```bash
uv run pytest tests/executor/test_config_toml.py -k "workspace_root" -v
```

Expected: 5 PASS.

- [ ] **Step 4.11: Run ruff + mypy**

```bash
uv run ruff check ralph_executor/config.py ralph_executor/setup_cmds.py tests/executor/conftest.py tests/executor/test_config_toml.py
uv run mypy --strict ralph_executor/config.py
```

- [ ] **Step 4.12: Commit**

```bash
git -C /c/Users/gethi/ralph-queue pull --rebase origin ralph-queue
git -C /c/Users/gethi/ralph-queue add ralph_executor/config.py ralph_executor/setup_cmds.py tests/executor/conftest.py tests/executor/test_config_toml.py
git -C /c/Users/gethi/ralph-queue commit -m "feat(config): _resolve_path + workspace_root (default ~/ralph-workspaces)"
git -C /c/Users/gethi/ralph-queue push origin ralph-queue
```

---

## Task 5: Refactor `worktree.work_worktree_path` signature

**Files:**
- Modify: `ralph_executor/worktree.py:38`
- Modify: `tests/executor/test_worktree.py`
- Modify: all callers of `work_worktree_path` (Step 5.1 enumerates)

- [ ] **Step 5.1: Find all callers**

```bash
grep -rn "work_worktree_path" /c/Users/gethi/ralph-queue/ralph_executor /c/Users/gethi/ralph-queue/tests 2>&1
```

Expected: callers in `loop.py`, `worktree.py` itself (definition + maybe internal use), `tests/executor/test_worktree.py`, `tests/executor/test_loop.py`. Note each.

- [ ] **Step 5.2: Write failing tests for new signature**

In `tests/executor/test_worktree.py`, append:

```python
def test_work_worktree_path_takes_clone_root(tmp_path: Path) -> None:
    """work_worktree_path uses the target's clone root, not ralph's repo."""
    from ralph_executor.worktree import work_worktree_path

    clone_root = tmp_path / "clones" / "emp3thy-ralph"
    clone_root.mkdir(parents=True)

    result = work_worktree_path(clone_root, "PBI-XYZ")
    assert result == clone_root / ".ralph-work" / "PBI-XYZ"
```

If the existing test_worktree.py has a test using the old signature (`work_worktree_path(repo_root, pbi_id)`), modify it to pass a `clone_root` instead. The CONTRACT is the same path shape — `<root>/.ralph-work/<pbi_id>` — only the semantic of `<root>` changed.

- [ ] **Step 5.3: Run failing test**

```bash
uv run pytest tests/executor/test_worktree.py::test_work_worktree_path_takes_clone_root -v
```

Expected: PASS (the function body doesn't need to change — only the parameter NAME and docstring change). If you renamed the param, the function still works on whatever Path you pass.

- [ ] **Step 5.4: Update `work_worktree_path` signature + docstring**

In `ralph_executor/worktree.py:38`:

```python
def work_worktree_path(clone_root: Path, pbi_id: str) -> Path:
    """Canonical filesystem path of the per-PBI work worktree.

    Lives INSIDE the target's clone (not inside ralph's checkout). The
    queue worktree (``queue_worktree_path``) stays in ralph's checkout.
    """
    return clone_root / ".ralph-work" / pbi_id
```

- [ ] **Step 5.5: Update all callers found in Step 5.1**

For each caller using `work_worktree_path(cfg.repo_path, pbi.id)` or similar, update the first argument to a `clone_root` Path (typically `clone.clone_root` from a `TargetClone` once Task 7 wires `ensure_clone` into the claim flow). For now, callers that don't yet have a `TargetClone` can pass `cfg.repo_path` as a placeholder — it'll be updated when Task 7 lands. Mark these with a `# TASK 7 TODO` comment so they're easy to find.

Note: this is a chained refactor — full correctness depends on Task 7. Tests in Task 5 only assert the new signature; tests for end-to-end behavior live in Task 7.

- [ ] **Step 5.6: Run worktree tests**

```bash
uv run pytest tests/executor/test_worktree.py -v
```

Expected: PASS.

- [ ] **Step 5.7: Run ruff + mypy**

```bash
uv run ruff check ralph_executor/worktree.py tests/executor/test_worktree.py
uv run mypy --strict ralph_executor/worktree.py
```

- [ ] **Step 5.8: Commit**

```bash
git -C /c/Users/gethi/ralph-queue pull --rebase origin ralph-queue
git -C /c/Users/gethi/ralph-queue add ralph_executor/worktree.py tests/executor/test_worktree.py
# also add any caller files updated in 5.5
git -C /c/Users/gethi/ralph-queue commit -m "refactor(worktree): work_worktree_path takes clone_root"
git -C /c/Users/gethi/ralph-queue push origin ralph-queue
```

---

## Task 6: PBI dataclass gains target fields

**Files:**
- Modify: `ralph_executor/types.py:21-42` (PBI dataclass)
- Modify: `tests/executor/conftest.py` (any factory for PBI test instances; may need defaults)

- [ ] **Step 6.1: Write failing test**

Append to `tests/test_pbi_reader.py` (or wherever PBI dataclass construction is exercised):

```python
def test_pbi_carries_target_repo_and_target_info_fields() -> None:
    """PBI dataclass has target_repo (raw string), target_info (parsed),
    and work_worktree (per-PBI worktree path)."""
    from datetime import UTC, datetime
    from pathlib import Path

    from ralph_executor.types import PBI
    from ralph_executor.url_utils import TargetRepoInfo

    pbi = PBI(
        id="WI-1",
        type="feature",
        status="inbox",
        severity="normal",
        attempts=0,
        created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
        path=Path("/tmp/x"),
        target_repo="https://github.com/emp3thy/ralph",
        target_info=TargetRepoInfo(host="github.com", owner="emp3thy", name="ralph"),
        work_worktree=Path("/tmp/clone/.ralph-work/WI-1"),
    )
    assert pbi.target_repo == "https://github.com/emp3thy/ralph"
    assert pbi.target_info is not None
    assert pbi.target_info.owner == "emp3thy"
    assert pbi.work_worktree == Path("/tmp/clone/.ralph-work/WI-1")
```

- [ ] **Step 6.2: Run failing test**

```bash
uv run pytest tests/test_pbi_reader.py -k "target_repo_and_target_info" -v
```

Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'target_repo'`.

- [ ] **Step 6.3: Add fields to PBI dataclass**

In `ralph_executor/types.py:21-42`, append the new fields at the end of the dataclass (defaults required because they come after `depends_on` which already has a default):

```python
@dataclass(frozen=True)
class PBI:
    # ... existing fields ...
    depends_on: tuple[str, ...] = ()
    # NEW — multi-target support. target_repo is the raw URL from
    # frontmatter (required field per PBI 1's schema, but defaults to
    # "" here so legacy positional constructions keep compiling).
    # target_info is the parsed view (None until parse_target_repo runs).
    # work_worktree is the per-PBI worktree path inside the target's
    # clone (None until ensure_clone + create_work_worktree complete).
    target_repo: str = ""
    target_info: "TargetRepoInfo | None" = None
    work_worktree: "Path | None" = None
```

Add forward-reference imports at top of the file:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph_executor.url_utils import TargetRepoInfo
```

(`Path` is already imported.)

- [ ] **Step 6.4: Run test — verify pass**

```bash
uv run pytest tests/test_pbi_reader.py -k "target_repo_and_target_info" -v
```

Expected: PASS.

- [ ] **Step 6.5: Run full pbi_reader + types tests**

```bash
uv run pytest tests/test_pbi_reader.py -v
uv run mypy --strict ralph_executor/types.py
```

- [ ] **Step 6.6: Commit**

```bash
git -C /c/Users/gethi/ralph-queue pull --rebase origin ralph-queue
git -C /c/Users/gethi/ralph-queue add ralph_executor/types.py tests/test_pbi_reader.py
git -C /c/Users/gethi/ralph-queue commit -m "feat(types): PBI gains target_repo / target_info / work_worktree fields"
git -C /c/Users/gethi/ralph-queue push origin ralph-queue
```

---

## Task 7: `loop._claim_pbi` multi-target refactor

**Files:**
- Modify: `ralph_executor/loop.py:286-330` (`_claim_pbi`, `_switch_to_feature_branch`)
- Modify: `tests/executor/test_loop.py`

This is the largest task. **Mitigation for sub-90% risk:** broken into three sub-steps (read field → parse + host check → ensure clone + worktree). Each sub-step has its own failing tests before the impl combines them.

### Sub-step 7A: `_read_target_repo_from_pbi` helper

- [ ] **Step 7.1: Write failing test for the helper**

Append to `tests/executor/test_loop.py`:

```python
def test_read_target_repo_from_pbi_reads_frontmatter(tmp_path: Path) -> None:
    """Helper reads target_repo field from PBI.md / BUG.md / FEEDBACK.md."""
    from ralph_executor.loop import _read_target_repo_from_pbi
    from ralph_executor.types import PBI
    from datetime import UTC, datetime

    pbi_dir = tmp_path / "WI-1"
    pbi_dir.mkdir()
    (pbi_dir / "PBI.md").write_text(
        "---\n"
        "id: WI-1\n"
        "type: feature\n"
        "status: current\n"
        "severity: normal\n"
        "attempts: 0\n"
        "target_repo: https://github.com/emp3thy/ralph\n"
        "---\n"
        "# Title\n",
        encoding="utf-8",
    )
    pbi = PBI(
        id="WI-1", type="feature", status="current", severity="normal",
        attempts=0, created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC), path=pbi_dir,
    )
    assert _read_target_repo_from_pbi(pbi) == "https://github.com/emp3thy/ralph"


def test_read_target_repo_from_pbi_raises_when_missing(tmp_path: Path) -> None:
    from ralph_executor.loop import _ClaimError, _read_target_repo_from_pbi
    from ralph_executor.types import PBI
    from datetime import UTC, datetime

    pbi_dir = tmp_path / "WI-2"
    pbi_dir.mkdir()
    (pbi_dir / "PBI.md").write_text(
        "---\n"
        "id: WI-2\n"
        "type: feature\n"
        "status: current\n"
        "severity: normal\n"
        "attempts: 0\n"
        "---\n"
        "# Title (no target_repo)\n",
        encoding="utf-8",
    )
    pbi = PBI(
        id="WI-2", type="feature", status="current", severity="normal",
        attempts=0, created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC), path=pbi_dir,
    )
    with pytest.raises(_ClaimError, match="missing target_repo"):
        _read_target_repo_from_pbi(pbi)
```

- [ ] **Step 7.2: Implement `_ClaimError` + `_read_target_repo_from_pbi`**

In `ralph_executor/loop.py`, near the top of the file (or near other internal classes):

```python
class _ClaimError(RuntimeError):
    """Raised when claim fails for a reason warranting blocked/ + HISTORY entry.

    Caught by ``iterate_once``, which moves the PBI to ``blocked/<id>/``
    and appends the error message to HISTORY.md.
    """
```

Add the helper (near `_feature_branch_name` at line 286):

```python
def _read_target_repo_from_pbi(pbi: PBI) -> str:
    """Read the target_repo field from the PBI's entry file frontmatter.

    Probe order: PBI.md, BUG.md, FEEDBACK.md (matches pbi_reader). Raises
    ``_ClaimError`` if the file is missing, the frontmatter can't be parsed,
    or the target_repo field is absent.
    """
    import yaml

    entry_filenames = ("PBI.md", "BUG.md", "FEEDBACK.md")
    entry: Path | None = None
    for name in entry_filenames:
        candidate = pbi.path / name
        if candidate.is_file():
            entry = candidate
            break
    if entry is None:
        raise _ClaimError(
            f"PBI {pbi.id} has no entry file (looked for "
            f"{', '.join(entry_filenames)})"
        )

    text = entry.read_text(encoding="utf-8")
    if not text.startswith("---\n") and not text.startswith("---\r\n"):
        raise _ClaimError(f"PBI {pbi.id} entry file has no frontmatter fence")
    lines = text.splitlines()
    close_idx = next(
        (i for i in range(1, len(lines)) if lines[i] == "---"), None
    )
    if close_idx is None:
        raise _ClaimError(f"PBI {pbi.id} entry file has no closing fence")
    try:
        frontmatter = yaml.safe_load("\n".join(lines[1:close_idx]))
    except yaml.YAMLError as exc:
        raise _ClaimError(f"PBI {pbi.id} YAML parse error: {exc}") from exc
    if not isinstance(frontmatter, dict):
        raise _ClaimError(f"PBI {pbi.id} frontmatter is not a mapping")
    target_repo = frontmatter.get("target_repo")
    if not target_repo:
        raise _ClaimError(f"PBI {pbi.id} missing target_repo field")
    return str(target_repo)
```

- [ ] **Step 7.3: Run sub-step 7A tests**

```bash
uv run pytest tests/executor/test_loop.py -k "read_target_repo_from_pbi" -v
```

Expected: 2 PASS.

### Sub-step 7B: parse + host check

- [ ] **Step 7.4: Write failing test for host check**

Append to `tests/executor/test_loop.py`:

```python
def test_claim_raises_claim_error_for_non_github_host(
    cfg_for_repo, fake_repo, monkeypatch
) -> None:
    """A PBI with target_repo on a non-github host raises _ClaimError
    with 'unsupported host'."""
    # Set up a PBI with target_repo = https://dev.azure.com/o/p/_git/r
    # Invoke _claim_pbi.
    # Assert _ClaimError raised, message contains "unsupported host"


def test_claim_raises_claim_error_for_invalid_url(
    cfg_for_repo, fake_repo, monkeypatch
) -> None:
    """A PBI with malformed target_repo raises _ClaimError with 'invalid'."""
    # PBI with target_repo = "not a url"
    # _claim_pbi raises _ClaimError matching "invalid target_repo"
```

(Use the existing `cfg_for_repo` fixture pattern from `tests/executor/conftest.py`. Construct minimal fake PBI directories with the relevant target_repo values.)

### Sub-step 7C: ensure_clone + worktree creation

- [ ] **Step 7.5: Write failing test for happy-path multi-target claim**

```python
def test_claim_clones_target_and_creates_worktree_in_clone(
    cfg_for_repo, fake_repo, monkeypatch, tmp_path
) -> None:
    """Happy path: PBI.target_repo points at a real github URL.
    _claim_pbi ensures clone, creates worktree inside clone, returns
    PBI with target_info + work_worktree set."""
    # Set cfg.workspace_root = tmp_path / "ws"
    # Mock target_clone.ensure_clone to return a fake TargetClone with
    #   clone_root = tmp_path / "ws/clones/emp3thy-ralph"
    # Mock worktree.create_work_worktree to be a no-op
    # Invoke _claim_pbi with a PBI whose target_repo is the github URL
    # Assert returned PBI.target_info.owner == "emp3thy"
    # Assert returned PBI.work_worktree == tmp_path / "ws/clones/emp3thy-ralph/.ralph-work/<id>"


def test_claim_raises_claim_error_when_clone_unreachable(
    cfg_for_repo, fake_repo, monkeypatch
) -> None:
    """ensure_clone raises TargetUnreachable -> _claim_pbi raises _ClaimError
    matching 'target unreachable'."""
    # Mock ensure_clone to raise TargetUnreachable("network error")
    # Assert _ClaimError with message "target unreachable: network error"
```

- [ ] **Step 7.6: Run failing tests (all 7-series)**

```bash
uv run pytest tests/executor/test_loop.py -k "claim" -v
```

Expected: 5 FAIL (the 2 from 7B + 2 from 7C + the helper test from 7A which passed in Step 7.3 already). Now claim_pbi needs the full body to satisfy them.

- [ ] **Step 7.7: Implement the new `_claim_pbi` body**

Replace the body of `_claim_pbi` (loop.py:290-323):

```python
def _claim_pbi(cfg: ExecutorConfig, pbi: PBI) -> PBI:
    """Move PBI into current/ and prepare the target-repo clone + worktree.

    Sequence:
      1. Read pbi.target_repo from frontmatter.
      2. Parse + host-check (github.com only this PBI).
      3. ensure_clone -> TargetClone at $RALPH_WORKSPACE/clones/<slug>/.
      4. Move PBI inbox -> current on ralph-queue (unchanged).
      5. Create per-PBI worktree inside the clone at
         <clone_root>/.ralph-work/<PBI-ID>/, branched from main.
      6. Return PBI with target_info + work_worktree populated.

    On any failure between steps 1-3, raise _ClaimError; iterate_once
    catches it and moves the PBI to blocked/<id>/ with the error in
    HISTORY.md.
    """
    from dataclasses import replace

    from ralph_executor import target_clone as tc_mod
    from ralph_executor.url_utils import parse_target_repo

    target_url = _read_target_repo_from_pbi(pbi)
    try:
        info = parse_target_repo(target_url)
    except ValueError as exc:
        raise _ClaimError(f"invalid target_repo URL: {exc}") from exc

    if info.host != "github.com":
        raise _ClaimError(
            f"unsupported host {info.host!r} (only github.com is supported)"
        )

    try:
        clone = tc_mod.ensure_clone(info, workspace_root=cfg.workspace_root)
    except tc_mod.TargetUnreachable as exc:
        raise _ClaimError(f"target unreachable: {exc}") from exc

    # Move PBI inbox -> current on ralph-queue (existing logic).
    event_log = open_log(cfg.repo_path)
    try:
        moved = move_inbox_to_current(
            cfg, pbi, event_log=event_log, now=datetime.now(tz=UTC),
        )
    finally:
        event_log.close()

    # Create per-PBI worktree INSIDE the target's clone.
    work_wt = worktree.work_worktree_path(clone.clone_root, moved.id)
    branch = _feature_branch_name(moved)
    if not _local_branch_exists(clone.clone_root, branch):
        worktree.create_work_worktree(
            clone.clone_root, work_wt, branch, base_branch="main"
        )
    else:
        # Existing branch — attach an existing worktree.
        worktree.attach_worktree(clone.clone_root, work_wt, branch)

    return replace(
        moved,
        target_repo=target_url,
        target_info=info,
        work_worktree=work_wt,
    )
```

(Note: this assumes `worktree.create_work_worktree` and `worktree.attach_worktree` already exist with these signatures from STAGE-B-PLAN-20a-worktree. If their signatures differ, adapt the calls to match. Run `grep -n "def create_work_worktree\|def attach_worktree" ralph_executor/worktree.py` to verify.)

- [ ] **Step 7.8: Modify `iterate_once` to catch `_ClaimError`**

In `iterate_once` (find its definition in `loop.py`), wrap the `_claim_pbi` call:

```python
try:
    claimed = _claim_pbi(cfg, picked)
except _ClaimError as exc:
    log.warning(
        "claim failed for PBI %s: %s; moving to blocked/", picked.id, exc
    )
    _move_to_blocked_with_reason(cfg, picked, reason=str(exc))
    return IterationResult(outcome="claim_failed", pbi_id=picked.id)
```

Add `_move_to_blocked_with_reason` helper near the other move helpers:

```python
def _move_to_blocked_with_reason(cfg: ExecutorConfig, pbi: PBI, *, reason: str) -> None:
    """Move PBI to blocked/<id>/ and append the reason to HISTORY.md."""
    # Use the existing movement helpers from ralph_executor.queue.movements.
    # Append reason to HISTORY.md before / after the move.
    ...
```

(Exact body depends on the queue.movements API; pattern-match from existing `move_inbox_to_current` / `move_current_to_blocked` callsites.)

- [ ] **Step 7.9: Run all Task 7 tests**

```bash
uv run pytest tests/executor/test_loop.py -k "claim" -v
```

Expected: 5 PASS.

- [ ] **Step 7.10: Run ruff + mypy**

```bash
uv run ruff check ralph_executor/loop.py tests/executor/test_loop.py
uv run mypy --strict ralph_executor/loop.py
```

- [ ] **Step 7.11: Commit**

```bash
git -C /c/Users/gethi/ralph-queue pull --rebase origin ralph-queue
git -C /c/Users/gethi/ralph-queue add ralph_executor/loop.py tests/executor/test_loop.py
git -C /c/Users/gethi/ralph-queue commit -m "feat(loop): _claim_pbi multi-target via target_repo + _ClaimError handling"
git -C /c/Users/gethi/ralph-queue push origin ralph-queue
```

---

## Task 8: `claude_spawn` cwd + `GH_OWNER` per-subprocess

**Files:**
- Modify: `ralph_executor/claude_spawn.py:434-443`
- Modify: `tests/executor/test_claude_spawn.py`

- [ ] **Step 8.1: Write failing tests**

Append to `tests/executor/test_claude_spawn.py`:

```python
def test_spawn_uses_pbi_work_worktree_as_cwd(
    cfg_for_repo, monkeypatch, tmp_path
) -> None:
    """spawn_claude_p cwd = pbi.work_worktree (per-PBI worktree in target's clone)."""
    from dataclasses import replace
    from datetime import UTC, datetime

    from ralph_executor.claude_spawn import spawn_claude_p
    from ralph_executor.types import PBI
    from ralph_executor.url_utils import TargetRepoInfo

    captured: dict[str, object] = {}

    class FakePopen:
        def __init__(self, argv: list[str], **kwargs: object) -> None:
            captured.update(kwargs)
            self.stdout = io.StringIO("")
            self.stderr = io.StringIO("")
            self.returncode = 0

        def wait(self) -> int:
            return 0

        def kill(self) -> None:
            pass

    monkeypatch.setattr("ralph_executor.claude_spawn.subprocess.Popen", FakePopen)
    monkeypatch.setattr(
        "ralph_executor.claude_spawn._query_open_pr_via_gh",
        lambda repo, branch: None,
    )

    work_wt = tmp_path / "clone" / ".ralph-work" / "PBI-X"
    work_wt.mkdir(parents=True)
    pbi = PBI(
        id="PBI-X", type="feature", status="current", severity="normal",
        attempts=0, created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC), path=tmp_path / "queue" / ".ralph" / "current" / "PBI-X",
        target_info=TargetRepoInfo(host="github.com", owner="orgA", name="repoA"),
        work_worktree=work_wt,
    )

    spawn_claude_p(cfg_for_repo, pbi)
    assert captured["cwd"] == str(work_wt)


def test_spawn_sets_gh_owner_env_from_target_info(
    cfg_for_repo, monkeypatch, tmp_path
) -> None:
    """env['GH_OWNER'] = pbi.target_info.owner regardless of cfg.gh_owner."""
    # Same fixture setup as above. Assert captured["env"]["GH_OWNER"] == "orgA"
    # even if cfg.gh_owner is "emp3thy".
```

- [ ] **Step 8.2: Run failing tests**

```bash
uv run pytest tests/executor/test_claude_spawn.py -k "work_worktree or gh_owner" -v
```

Expected: FAIL (cwd is currently cfg.repo_path; GH_OWNER is from cfg).

- [ ] **Step 8.3: Modify `spawn_claude_p` in claude_spawn.py:434-443**

Replace the env + cwd lines:

```python
    env = os.environ.copy()
    env["RALPH_PBI_DIR"] = str(pbi.path)
    env["BASH_MAX_TIMEOUT_MS"] = str(cfg.bash_max_timeout_ms)
    if cfg.anthropic_api_key:
        env.setdefault("ANTHROPIC_API_KEY", cfg.anthropic_api_key)
    # NEW: per-target owner so pr-github subprocess calls write to the
    # right repo. Falls back to cfg.gh_owner for legacy single-target setups.
    if pbi.target_info is not None:
        env["GH_OWNER"] = pbi.target_info.owner
    log.info("spawning %s for PBI %s", argv[0], pbi.id)
    start = time.monotonic()
    # NEW: cwd is the per-PBI worktree in the target's clone. Fall back to
    # cfg.repo_path for legacy single-target setups (work_worktree None).
    cwd_path = (
        str(pbi.work_worktree) if pbi.work_worktree is not None
        else str(cfg.repo_path)
    )
    proc = subprocess.Popen(
        argv,
        cwd=cwd_path,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
```

The `target_info is not None` / `work_worktree is not None` guards make this backwards-compatible during the rollout window (Task 7 lands first, but existing tests may construct PBIs without these fields).

- [ ] **Step 8.4: Run tests — verify pass**

```bash
uv run pytest tests/executor/test_claude_spawn.py -v
```

Expected: all PASS.

- [ ] **Step 8.5: Run ruff + mypy**

```bash
uv run ruff check ralph_executor/claude_spawn.py tests/executor/test_claude_spawn.py
uv run mypy --strict ralph_executor/claude_spawn.py
```

- [ ] **Step 8.6: Commit**

```bash
git -C /c/Users/gethi/ralph-queue pull --rebase origin ralph-queue
git -C /c/Users/gethi/ralph-queue add ralph_executor/claude_spawn.py tests/executor/test_claude_spawn.py
git -C /c/Users/gethi/ralph-queue commit -m "feat(spawn): cwd = pbi.work_worktree; GH_OWNER from pbi.target_info"
git -C /c/Users/gethi/ralph-queue push origin ralph-queue
```

---

## Task 9: Sweep `GH_OWNER` per-PBI override

**Files:**
- Modify: `ralph_executor/sweep/runner.py` (find where it invokes pr-github show subprocess)
- Modify: `tests/executor/sweep/test_runner.py`

- [ ] **Step 9.1: Locate the sweep subprocess invocation**

```bash
grep -n "show.py\|subprocess.run\|_invoke_skill" /c/Users/gethi/ralph-queue/ralph_executor/sweep/runner.py
```

Note the line(s) where pr-github show is invoked.

- [ ] **Step 9.2: Write failing test**

Append to `tests/executor/sweep/test_runner.py`:

```python
def test_sweep_sets_gh_owner_from_pending_pbi_target_info(
    tmp_path, monkeypatch
) -> None:
    """When sweep calls pr-github show for a pending-pr PBI, the subprocess
    env must include GH_OWNER from that PBI's target_info.owner."""
    captured: list[dict[str, str]] = []

    def fake_run(argv, **kwargs):
        env = kwargs.get("env") or {}
        captured.append(dict(env))
        import subprocess
        return subprocess.CompletedProcess(
            args=argv, returncode=0,
            stdout='{"pr_id": 1, "title": "t", "status": "completed", '
                   '"ci_status": "succeeded", "source_branch": "b", '
                   '"target_branch": "main", "reviewers": [], "url": "u", '
                   '"mergeable_state": "clean"}',
            stderr="",
        )

    monkeypatch.setattr("ralph_executor.sweep.pr_state.subprocess.run", fake_run)

    # Build a pending-pr PBI directory with target_repo pointing at orgX/repoY.
    queue_root = tmp_path / ".ralph"
    for sub in ("inbox", "current", "pending-pr", "done", "blocked"):
        (queue_root / sub).mkdir(parents=True)
    pbi_dir = queue_root / "pending-pr" / "PBI-Z"
    pbi_dir.mkdir()
    (pbi_dir / "PBI.md").write_text(
        "---\n"
        "id: PBI-Z\n"
        "type: feature\n"
        "status: pending-pr\n"
        "severity: normal\n"
        "attempts: 0\n"
        "target_repo: https://github.com/orgX/repoY\n"
        "---\n"
        "# Title\n",
        encoding="utf-8",
    )
    (pbi_dir / "PR-LINK.md").write_text("PR ID 1\n", encoding="utf-8")
    (pbi_dir / "HISTORY.md").write_text("", encoding="utf-8")

    # Build minimal SweepContext + run the sweep. Mock attachments...
    # Assert at least one captured env had GH_OWNER == "orgX".
    assert any(env.get("GH_OWNER") == "orgX" for env in captured), captured
```

(The test is sketched; fill in `SweepContext` construction copying the pattern from existing `test_runner.py` tests. Key assertion: GH_OWNER env var matches the per-PBI target_repo owner.)

- [ ] **Step 9.3: Run failing test**

```bash
uv run pytest tests/executor/sweep/test_runner.py -k "gh_owner" -v
```

Expected: FAIL (sweep currently uses inherited GH_OWNER, not per-PBI).

- [ ] **Step 9.4: Modify sweep to inject GH_OWNER per-PBI**

In `ralph_executor/sweep/runner.py` (or `pr_state.py` if the subprocess call lives there), wherever pr-github show is invoked for a pending-pr PBI:

```python
# Read target_repo from the PBI's frontmatter; parse to TargetRepoInfo.
target_url = _read_target_repo_from_pbi(pbi)  # same helper as loop.py
try:
    info = parse_target_repo(target_url)
except ValueError:
    log.warning("sweep: PBI %s has invalid target_repo; skipping", pbi.id)
    continue
env = os.environ.copy()
env["GH_OWNER"] = info.owner
subprocess.run(
    [sys.executable, str(show_script), "--pr-id", str(pr_id), "--repo", info.name],
    env=env,
    ...
)
```

The `--repo` value also changes per PBI (target's name, not cfg's). Update accordingly.

- [ ] **Step 9.5: Run tests — verify pass**

```bash
uv run pytest tests/executor/sweep/test_runner.py -v
```

Expected: all PASS.

- [ ] **Step 9.6: Run ruff + mypy**

```bash
uv run ruff check ralph_executor/sweep/runner.py ralph_executor/sweep/pr_state.py tests/executor/sweep/test_runner.py
uv run mypy --strict ralph_executor/sweep/runner.py ralph_executor/sweep/pr_state.py
```

- [ ] **Step 9.7: Commit**

```bash
git -C /c/Users/gethi/ralph-queue pull --rebase origin ralph-queue
git -C /c/Users/gethi/ralph-queue add ralph_executor/sweep/runner.py ralph_executor/sweep/pr_state.py tests/executor/sweep/test_runner.py
git -C /c/Users/gethi/ralph-queue commit -m "feat(sweep): inject GH_OWNER per pending-pr PBI from target_info"
git -C /c/Users/gethi/ralph-queue push origin ralph-queue
```

---

## Task 10: Full-suite gate + smoke

**Files:** none modified directly; verification.

- [ ] **Step 10.1: Sweep tests for literal PBI / SweepConfig / cfg constructions**

```bash
grep -rn "PBI(" /c/Users/gethi/ralph-queue/tests 2>&1 | head -20
grep -rn "ExecutorConfig(" /c/Users/gethi/ralph-queue/tests 2>&1 | head -20
```

For each match: if positional args, new defaults make it still compile. If keyword args asserted on the full set, update to include `target_repo=""`, `target_info=None`, `work_worktree=None`, and `workspace_root=Path.home() / "ralph-workspaces"` as appropriate.

- [ ] **Step 10.2: Run full pytest**

```bash
uv run pytest tests/ -v
```

Expected: all green. Common failures:

- A test fixture constructing PBI without the new keyword args: add them (defaults make this rare).
- A test mocking `worktree.work_worktree_path` and asserting the old `repo_root` arg: update to `clone_root`.
- A test asserting on subprocess env that didn't expect GH_OWNER override: update assertion.

- [ ] **Step 10.3: Run ruff + format + mypy across the whole package**

```bash
uv run ruff check ralph_executor/ skills/ scripts/ tests/
uv run ruff format --check ralph_executor/ skills/ scripts/ tests/
uv run mypy --strict ralph_executor/ scripts/
```

Expected: all green.

- [ ] **Step 10.4: Self-host smoke (optional but recommended)**

With a PBI in the inbox declaring `target_repo: https://github.com/emp3thy/ralph` and `GH_TOKEN` set:

```bash
cd /c/Users/gethi/source/ralph    # ralph's running checkout
$env:RALPH_WORKSPACE = "C:/Users/gethi/ralph-workspaces"
uv run ralph-executor --once
```

Expected:
- A new clone at `C:/Users/gethi/ralph-workspaces/clones/emp3thy-ralph/` appears.
- A per-PBI worktree at `C:/Users/gethi/ralph-workspaces/clones/emp3thy-ralph/.ralph-work/<PBI-ID>/` appears.
- Claude session spawns from that worktree.
- No errors; outcome = "claimed" or "partial" / "pr_created" / etc.

---

## Acceptance (matches spec)

- [x] `ralph_executor/url_utils.py` exists with `parse_target_repo` + `TargetRepoInfo` — Task 1
- [x] `ralph_executor/target_clone.py` exists with `ensure_clone` + `TargetClone` + `TargetUnreachable` — Task 3
- [x] `ralph_executor/git_ops.py` has `clone(url, dest, *, timeout=120.0)` — Task 2
- [x] `ralph_executor/worktree.py.work_worktree_path` takes `clone_root` — Task 5
- [x] `ralph_executor/config.py` has `workspace_root: Path` default `~/ralph-workspaces`, layered defaults < TOML < env — Task 4
- [x] `_resolve_path` helper exists — Task 4
- [x] `CONFIG_TOML_STUB` documents `workspace_root` — Task 4
- [x] `ralph_executor/loop._claim_pbi` reads `pbi.target_repo`, parses, host-checks, ensures clone, creates worktree in clone — Task 7
- [x] `_ClaimError` class exists; `iterate_once` catches and moves to blocked/ — Task 7
- [x] `ralph_executor/claude_spawn.spawn_claude_p` sets `env["GH_OWNER"]` per subprocess from `pbi.target_info.owner`; uses `cwd=pbi.work_worktree` — Task 8
- [x] Sweep injects `GH_OWNER` per pending-pr PBI when calling pr-github show — Task 9
- [x] pytest / ruff / mypy strict all green — Task 10
- [x] depends_on: `["RALPH-PBI-TARGET-REPO-FIELD"]`

## Out of scope (per spec)

- ADO targets
- Per-target auth tokens (single global GH_TOKEN)
- Concurrent iteration locking
- Auto-cleanup of stale clones
- Shallow clones
- `target_branch` field
- Migration tooling for switching existing operators

## Operational notes

- **Workflow:** all commits go via `C:/Users/gethi/ralph-queue` (the separate clone). Pull-rebase before every commit.
- **Ralph running?** This PBI changes a critical claim path. Halt ralph before merge of this PBI's PR; resume after.
- **First run after merge:** ralph will clone the target repo (for self-host: a fresh clone of the ralph repo) into `$RALPH_WORKSPACE`. One-time cost.
- **depends_on:** strict on `RALPH-PBI-TARGET-REPO-FIELD`. Don't start this until PBI 1 is merged.
