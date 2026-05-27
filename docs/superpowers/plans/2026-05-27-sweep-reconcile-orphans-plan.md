# Sweep Reconciles Orphan pending-pr Entries — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sweep auto-reconciles `pending-pr/<PBI-ID>/` directories that are missing `PR-LINK.md` by looking up the PR via the host's API (using the source branch `ralph/<PBI-ID>`) and moving the dir to `done/`, `blocked/`, or `inbox/` based on PR state. Also adds a `ralph-executor reconcile` CLI subcommand for one-shot manual cleanup.

**Architecture:** New `lookup_by_branch.py` sub-op in `skills/pr-github/scripts/` (6th op, sibling of `show.py`, `create_pr.py`, etc.). New `ralph_executor/sweep/reconcile.py` module exposing `reconcile_orphan` + `reconcile_all`, called from both `sweep/runner.py:run()` (auto) and a new `reconcile` CLI subparser (manual). Subprocess invocation of the staged `pr/scripts/lookup_by_branch.py`; JSON parsed; state mapped to a 5-state action enum. ADO version deferred via follow-up GitHub issue.

**Tech Stack:** Python 3.12, `requests`, `responses` (test mocking — already a dep, used in `tests/skills/test_pr_github.py`), `argparse` subparsers (`cli.py` already uses them), `subprocess.run`, `shutil.move`, `dataclasses`, `StrEnum`. pytest, ruff, mypy strict.

**Spec:** `docs/superpowers/specs/2026-05-27-sweep-reconcile-orphans-design.md`

---

## Confidence per task

All tasks ≥ 90%. Pre-flight checks (line numbers, fixture names, library usage patterns) verified against the actual repo state before writing the plan.

| Task | % | Notes |
|---|---|---|
| 1. Failing test for `lookup_by_branch` | 95% | `responses` library pattern at `tests/skills/test_pr_github.py:22` (`import responses`, `@responses.activate` decorator). Direct replication. |
| 2. Implement `lookup_by_branch.py` | 92% | REST endpoints both well-documented: `GET /repos/{owner}/{repo}/pulls?head={owner}:{branch}&state=all` returns array; `GET /repos/{owner}/{repo}/branches/{branch}` returns 200 or 404. Risk: 5% for response-shape edge cases (empty list shape vs explicit `null`; `merged_at` ISO format). Step 2.4 explicitly handles both shapes; the failing tests in Task 1 cover the edge cases. |
| 3. Add types + failing reconcile tests | 95% | `StrEnum` + `@dataclass(frozen=True)` patterns at `sweep/types.py:11,19,70` — exact precedent. |
| 4. Implement `reconcile.py` | 92% | `subprocess.run` with `capture_output=True`, JSON parse, state map. Risk: `dry_run=True` path needs every branch covered in tests; Step 4.3 explicitly lists each variant. |
| 5. Wire reconcile into `runner.py` | 96% | Insert point is `run()` loop body at lines 201–206 (verified). The branch is `if not (pbi_dir / "PR-LINK.md").is_file()` BEFORE the existing `_process_pbi` call. No deep refactor. |
| 6. Add `reconcile` CLI subcommand | 94% | `cli.py:116` already has `subparsers = parser.add_subparsers(dest="subcommand")` + `health/doctor/init/scaffold` parsers — pattern verified. Subcommand dispatch in `main()` is the existing if-elif ladder. |
| 7. Full-suite green + smoke | 91% | Could surface a test elsewhere that asserts the legacy "PR-LINK.md is missing" warning shape. Step 7.1 grep finds them; Step 7.2 fix is mechanical. |

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `skills/pr-github/scripts/lookup_by_branch.py` | Create | argparse + REST calls + JSON stdout. Exit 0/2/3. |
| `ralph_executor/sweep/types.py` | Modify | Add `ReconcileAction` (StrEnum) and `ReconcileReport` (frozen dataclass). |
| `ralph_executor/sweep/reconcile.py` | Create | `reconcile_orphan` (single PBI) + `reconcile_all` (iterate pending-pr/). subprocess to lookup_by_branch. State→destination mapping. PR-LINK.md retroactive writes. |
| `ralph_executor/sweep/runner.py` | Modify | In `run()` loop (lines 201–206), branch on missing PR-LINK.md: call reconcile instead of letting `_process_pbi` raise. |
| `ralph_executor/cli.py` | Modify | Add `reconcile` subparser (line ~165 area, after existing `scaffold_parser`). Dispatch in `main()`. |
| `tests/skills/test_pr_github.py` | Modify | Append test class for `lookup_by_branch` using `@responses.activate` pattern. |
| `tests/executor/sweep/test_reconcile.py` | Create | All `reconcile_orphan` + `reconcile_all` cases. |
| `tests/executor/sweep/test_runner.py` | Modify | Update the existing "missing PR-LINK.md raises sweep error" assertion: reconcile is now called instead. |
| `tests/executor/test_cli_reconcile.py` | Create | CLI subcommand wiring + dry-run + exit codes. |

---

## Task 1: Failing test for `lookup_by_branch`

**Files:**
- Modify: `tests/skills/test_pr_github.py` (append at end of file)

- [ ] **Step 1.1: Add the test fixture helpers**

Append to the end of `tests/skills/test_pr_github.py`:

```python
# ---------------------------------------------------------------------------
# lookup_by_branch
# ---------------------------------------------------------------------------

# Add these imports at the top of the file if not already present:
#   import json
#   import subprocess
#   import sys
#   from pathlib import Path

_LOOKUP_SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "skills"
    / "pr-github"
    / "scripts"
    / "lookup_by_branch.py"
)


def _run_lookup(
    *args: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Invoke lookup_by_branch.py as a subprocess.

    Uses the same Python interpreter as the test runner. Returns the
    raw CompletedProcess so callers can assert on stdout/stderr/returncode.
    """
    base_env = {
        "GH_TOKEN": "fake-token",
        "GH_OWNER": "emp3thy",
        "PATH": os.environ.get("PATH", ""),
    }
    if env is not None:
        base_env.update(env)
    return subprocess.run(
        [sys.executable, str(_LOOKUP_SCRIPT), *args],
        capture_output=True,
        text=True,
        env=base_env,
        check=False,
    )
```

- [ ] **Step 1.2: Add the test cases**

Append these tests after the helpers:

```python
@responses.activate
def test_lookup_by_branch_returns_open_pr() -> None:
    responses.add(
        responses.GET,
        "https://api.github.com/repos/emp3thy/ralph/pulls",
        json=[
            {
                "number": 42,
                "state": "open",
                "merged_at": None,
                "html_url": "https://github.com/emp3thy/ralph/pull/42",
            }
        ],
        status=200,
    )
    result = _run_lookup("--branch", "ralph/PBI-XYZ", "--repo", "ralph")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["pr"] == {
        "state": "open",
        "url": "https://github.com/emp3thy/ralph/pull/42",
        "pr_id": 42,
        "merged_at": None,
    }
    assert payload["branch_exists"] is None


@responses.activate
def test_lookup_by_branch_returns_merged_pr() -> None:
    responses.add(
        responses.GET,
        "https://api.github.com/repos/emp3thy/ralph/pulls",
        json=[
            {
                "number": 25,
                "state": "closed",
                "merged_at": "2026-05-26T18:00:00Z",
                "html_url": "https://github.com/emp3thy/ralph/pull/25",
            }
        ],
        status=200,
    )
    result = _run_lookup("--branch", "ralph/STAGE-B-PLAN-03", "--repo", "ralph")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["pr"]["state"] == "merged"
    assert payload["pr"]["merged_at"] == "2026-05-26T18:00:00Z"


@responses.activate
def test_lookup_by_branch_returns_closed_unmerged_pr() -> None:
    responses.add(
        responses.GET,
        "https://api.github.com/repos/emp3thy/ralph/pulls",
        json=[
            {
                "number": 7,
                "state": "closed",
                "merged_at": None,
                "html_url": "https://github.com/emp3thy/ralph/pull/7",
            }
        ],
        status=200,
    )
    result = _run_lookup("--branch", "ralph/x", "--repo", "ralph")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["pr"]["state"] == "closed"


@responses.activate
def test_lookup_by_branch_returns_null_when_no_pr() -> None:
    responses.add(
        responses.GET,
        "https://api.github.com/repos/emp3thy/ralph/pulls",
        json=[],
        status=200,
    )
    result = _run_lookup("--branch", "ralph/never-existed", "--repo", "ralph")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["pr"] is None
    assert payload["branch_exists"] is None


@responses.activate
def test_lookup_by_branch_checks_branch_exists_when_flag_set() -> None:
    responses.add(
        responses.GET,
        "https://api.github.com/repos/emp3thy/ralph/pulls",
        json=[],
        status=200,
    )
    responses.add(
        responses.GET,
        "https://api.github.com/repos/emp3thy/ralph/branches/ralph/PBI-Z",
        status=200,
        json={"name": "ralph/PBI-Z"},
    )
    result = _run_lookup(
        "--branch", "ralph/PBI-Z", "--repo", "ralph", "--include-branch-check"
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["pr"] is None
    assert payload["branch_exists"] is True


@responses.activate
def test_lookup_by_branch_reports_branch_missing() -> None:
    responses.add(
        responses.GET,
        "https://api.github.com/repos/emp3thy/ralph/pulls",
        json=[],
        status=200,
    )
    responses.add(
        responses.GET,
        "https://api.github.com/repos/emp3thy/ralph/branches/ralph/PBI-DEAD",
        status=404,
        json={"message": "Branch not found"},
    )
    result = _run_lookup(
        "--branch", "ralph/PBI-DEAD", "--repo", "ralph", "--include-branch-check"
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["pr"] is None
    assert payload["branch_exists"] is False


def test_lookup_by_branch_missing_gh_token_exits_2() -> None:
    result = _run_lookup(
        "--branch", "ralph/x", "--repo", "ralph",
        env={"GH_TOKEN": "", "GH_OWNER": "emp3thy"},
    )
    assert result.returncode == 2
    assert "GH_TOKEN" in result.stderr


def test_lookup_by_branch_missing_branch_arg_exits_2() -> None:
    result = _run_lookup("--repo", "ralph")
    assert result.returncode == 2


@responses.activate
def test_lookup_by_branch_github_500_exits_3() -> None:
    responses.add(
        responses.GET,
        "https://api.github.com/repos/emp3thy/ralph/pulls",
        status=500,
        json={"message": "Internal server error"},
    )
    result = _run_lookup("--branch", "ralph/x", "--repo", "ralph")
    assert result.returncode == 3
    assert "github error" in result.stderr
```

- [ ] **Step 1.3: Run the new tests and verify they fail**

```bash
uv run pytest tests/skills/test_pr_github.py -k "lookup_by_branch" -v
```

Expected: **9 FAILs** — `FileNotFoundError` or similar because `lookup_by_branch.py` doesn't exist yet.

- [ ] **Step 1.4: Commit**

```bash
git -C /c/Users/gethi/source/ralph add tests/skills/test_pr_github.py
git -C /c/Users/gethi/source/ralph commit -m "test(pr-github): failing tests for lookup_by_branch sub-op"
```

---

## Task 2: Implement `lookup_by_branch.py`

**Files:**
- Create: `skills/pr-github/scripts/lookup_by_branch.py`

- [ ] **Step 2.1: Scaffold the script**

Create `skills/pr-github/scripts/lookup_by_branch.py` with the imports and argparse shape that mirrors `show.py`:

```python
"""``pr-github lookup_by_branch`` -- resolve a PR by source branch name.

REST endpoints used:
    GET /repos/{owner}/{repo}/pulls?head={owner}:{branch}&state=all
        docs: https://docs.github.com/en/rest/pulls/pulls#list-pull-requests
    GET /repos/{owner}/{repo}/branches/{branch}  (only when
        --include-branch-check is set)
        docs: https://docs.github.com/en/rest/branches/branches#get-a-branch

Used by ralph_executor.sweep.reconcile to resolve orphan pending-pr/
directories (those without PR-LINK.md) back to a PR state so the sweep
loop can move them to done/, blocked/, or inbox/.

State mapping (GitHub -> reconcile vocabulary):
    state=open                          -> "open"
    state=closed AND merged_at != null  -> "merged"
    state=closed AND merged_at == null  -> "closed"

This vocab is DISTINCT from show.py's cross-host active/completed/
abandoned because reconcile must distinguish merged from closed-unmerged
to pick the right destination directory.

Exit codes: 0 success, 2 validation, 3 GitHub error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from _common import (  # noqa: E402
    FatalError,
    HttpError,
    fail,
    handle_http_error,
    load_client,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lookup_by_branch",
        description=(
            "Resolve a PR by source branch name. Used by sweep to "
            "reconcile orphan pending-pr/ entries."
        ),
    )
    parser.add_argument("--branch", required=True, help="Source branch name (e.g. ralph/PBI-XYZ).")
    parser.add_argument("--repo", required=True, help="GitHub repository name (without owner).")
    parser.add_argument(
        "--include-branch-check",
        action="store_true",
        help=(
            "Also call GET /branches/{branch} to determine if the branch "
            "exists on origin. Adds one API call per invocation."
        ),
    )
    return parser
```

- [ ] **Step 2.2: Add the PR-state mapping helper**

Append to the same file:

```python
def _map_pr_state(raw_state: str, merged_at: str | None) -> str:
    """Map GitHub's (state, merged_at) to the reconcile vocab.

    GitHub's REST API exposes ``state`` as ``open`` or ``closed`` and a
    ``merged_at`` timestamp that is null unless the PR was merged. We
    expose three distinct values for the reconcile state machine.
    """
    if raw_state == "open":
        return "open"
    if raw_state == "closed":
        if merged_at:
            return "merged"
        return "closed"
    # Defensive — GitHub has never returned anything else, but if it
    # ever does we surface the raw string rather than guessing.
    raise FatalError(f"unexpected GitHub PR state: {raw_state!r}")
```

- [ ] **Step 2.3: Add the lookup function**

```python
def _lookup_pr(
    client: Any,
    repo: str,
    branch: str,
) -> dict[str, Any] | None:
    """Return the first matching PR or None.

    GitHub's ``head=owner:branch`` filter is exact-match. There is at
    most one open PR per source branch by GitHub policy, but closed
    PRs can stack. We take the most recent (first item; the API
    returns newest first by default) as the canonical match.
    """
    path = f"repos/{client.owner}/{repo}/pulls"
    params = {"head": f"{client.owner}:{branch}", "state": "all"}
    response = client.session.get(
        f"https://api.github.com/{path}",
        headers=client._headers(),
        params=params,
        timeout=30,
    )
    if response.status_code >= 400:
        raise HttpError(
            f"GET /{path}?head={client.owner}:{branch}: HTTP {response.status_code}: {response.text}"
        )
    payload = response.json()
    if not isinstance(payload, list):
        raise HttpError(f"GET /{path} returned non-list: {type(payload).__name__}")
    if not payload:
        return None
    first = payload[0]
    return {
        "state": _map_pr_state(first["state"], first.get("merged_at")),
        "url": first["html_url"],
        "pr_id": int(first["number"]),
        "merged_at": first.get("merged_at"),
    }


def _lookup_branch(client: Any, repo: str, branch: str) -> bool:
    """Return True if the branch exists on origin, False if 404.

    Any other non-2xx is an HttpError, mapped to exit 3.
    """
    path = f"repos/{client.owner}/{repo}/branches/{branch}"
    response = client.session.get(
        f"https://api.github.com/{path}",
        headers=client._headers(),
        timeout=30,
    )
    if response.status_code == 404:
        return False
    if response.status_code >= 400:
        raise HttpError(
            f"GET /{path}: HTTP {response.status_code}: {response.text}"
        )
    return True
```

- [ ] **Step 2.4: Add the main entry point**

```python
def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        client = load_client()
    except FatalError as err:
        return fail(str(err))

    branch = args.branch.strip()
    repo = args.repo.strip()
    if not branch:
        return fail("--branch must be non-empty")
    if not repo:
        return fail("--repo must be non-empty")

    try:
        pr = _lookup_pr(client, repo, branch)
        branch_exists: bool | None = None
        if args.include_branch_check:
            branch_exists = _lookup_branch(client, repo, branch)
    except HttpError as err:
        return handle_http_error(err)

    print(json.dumps({"pr": pr, "branch_exists": branch_exists}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2.5: Run the failing tests and verify they now pass**

```bash
uv run pytest tests/skills/test_pr_github.py -k "lookup_by_branch" -v
```

Expected: **9 PASS**.

- [ ] **Step 2.6: Run ruff + mypy on the new file**

```bash
uv run ruff check skills/pr-github/scripts/lookup_by_branch.py
uv run mypy --strict skills/pr-github/scripts/lookup_by_branch.py
```

Expected: all green. If mypy complains about `client: Any` — that's intentional; `load_client()` returns an internal dataclass. If it asks for an import, satisfy it minimally without changing the runtime shape.

- [ ] **Step 2.7: Commit**

```bash
git -C /c/Users/gethi/source/ralph add skills/pr-github/scripts/lookup_by_branch.py
git -C /c/Users/gethi/source/ralph commit -m "feat(pr-github): add lookup_by_branch sub-op for branch->PR resolution"
```

---

## Task 3: Add types + failing reconcile tests

**Files:**
- Modify: `ralph_executor/sweep/types.py` (append at end)
- Create: `tests/executor/sweep/test_reconcile.py`

- [ ] **Step 3.1: Add types to `sweep/types.py`**

Append to `ralph_executor/sweep/types.py`:

```python


from collections.abc import Mapping


class ReconcileAction(StrEnum):
    MOVED_TO_DONE = "moved_to_done"
    MOVED_TO_BLOCKED = "moved_to_blocked"
    MOVED_TO_INBOX = "moved_to_inbox"
    KEEP_PENDING = "keep_pending"          # PR open; PR-LINK.md written
    KEEP_API_ERROR = "keep_api_error"      # subprocess exit 3; retry next iter


@dataclass(frozen=True)
class ReconcileReport:
    """Aggregate of reconcile_all over .ralph/pending-pr/.

    ``actions`` maps each processed PBI id to its chosen action.
    ``errors`` carries human-readable error strings for PBIs that
    raised ReconcileError (matches the per-PBI isolation semantics of
    the existing sweep loop).
    """

    actions: Mapping[str, ReconcileAction]
    errors: Mapping[str, str]
```

(Note: `Mapping` import is needed; `from collections.abc import Mapping` already added inline above. If `Mapping` is already imported at the top of types.py, drop the inline import.)

- [ ] **Step 3.2: Run mypy to confirm the types load**

```bash
uv run mypy --strict ralph_executor/sweep/types.py
```

Expected: green.

- [ ] **Step 3.3: Create the failing reconcile test file**

Create `tests/executor/sweep/test_reconcile.py`:

```python
"""Tests for ralph_executor.sweep.reconcile."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterable
from pathlib import Path

import pytest

from ralph_executor.sweep.reconcile import (
    ReconcileError,
    reconcile_all,
    reconcile_orphan,
)
from ralph_executor.sweep.runner import SweepConfig, SweepContext
from ralph_executor.sweep.types import ReconcileAction


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_queue_root(tmp_path: Path) -> Path:
    """A .ralph/ skeleton with empty inbox/current/pending-pr/done/blocked."""
    queue = tmp_path / ".ralph"
    for sub in ("inbox", "current", "pending-pr", "done", "blocked"):
        (queue / sub).mkdir(parents=True)
    return queue


@pytest.fixture
def fake_orphan(fake_queue_root: Path) -> Path:
    """A pending-pr/<PBI-ID>/ dir WITHOUT PR-LINK.md."""
    orphan = fake_queue_root / "pending-pr" / "STAGE-B-PLAN-03"
    orphan.mkdir()
    (orphan / "PBI.md").write_text("---\nid: STAGE-B-PLAN-03\n---\n", encoding="utf-8")
    (orphan / "HISTORY.md").write_text("", encoding="utf-8")
    return orphan


@pytest.fixture
def fake_ctx(fake_queue_root: Path, tmp_path: Path) -> SweepContext:
    scripts_path = tmp_path / "pr_scripts"
    scripts_path.mkdir()
    # The actual lookup_by_branch.py is mocked via subprocess monkeypatch,
    # so the file under scripts_path doesn't need to exist.
    return SweepContext(
        queue_root=fake_queue_root,
        ado_pr_scripts_path=scripts_path,
        config=SweepConfig(
            ralph_author_email="ralph@example.com",
            max_attempts=20,
            stale_threshold=__import__("datetime").timedelta(days=3),
            now=__import__("datetime").datetime.now(tz=__import__("datetime").UTC),
        ),
    )


def _stub_subprocess(
    monkeypatch: pytest.MonkeyPatch,
    stdout: str,
    returncode: int,
    stderr: str = "",
) -> list[list[str]]:
    """Replace subprocess.run inside reconcile with a captured stub.

    Returns a list that will be populated with each argv the production
    code calls subprocess.run with — assertable in the test.
    """
    invocations: list[list[str]] = []

    def _fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        invocations.append(list(argv))
        return subprocess.CompletedProcess(
            args=argv,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
        )

    monkeypatch.setattr("ralph_executor.sweep.reconcile.subprocess.run", _fake_run)
    return invocations


# ---------------------------------------------------------------------------
# reconcile_orphan
# ---------------------------------------------------------------------------


def test_reconcile_orphan_merged_moves_to_done(
    fake_orphan: Path,
    fake_ctx: SweepContext,
    fake_queue_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_subprocess(
        monkeypatch,
        stdout=json.dumps({
            "pr": {
                "state": "merged",
                "url": "https://github.com/emp3thy/ralph/pull/25",
                "pr_id": 25,
                "merged_at": "2026-05-26T18:00:00Z",
            },
            "branch_exists": None,
        }),
        returncode=0,
    )

    action = reconcile_orphan(fake_orphan, fake_ctx)

    assert action == ReconcileAction.MOVED_TO_DONE
    moved = fake_queue_root / "done" / "STAGE-B-PLAN-03"
    assert moved.is_dir()
    assert not fake_orphan.exists()
    pr_link = moved / "PR-LINK.md"
    assert pr_link.is_file()
    assert "https://github.com/emp3thy/ralph/pull/25" in pr_link.read_text(encoding="utf-8")


def test_reconcile_orphan_open_stays_in_pending(
    fake_orphan: Path,
    fake_ctx: SweepContext,
    fake_queue_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_subprocess(
        monkeypatch,
        stdout=json.dumps({
            "pr": {
                "state": "open",
                "url": "https://github.com/emp3thy/ralph/pull/42",
                "pr_id": 42,
                "merged_at": None,
            },
            "branch_exists": None,
        }),
        returncode=0,
    )

    action = reconcile_orphan(fake_orphan, fake_ctx)

    assert action == ReconcileAction.KEEP_PENDING
    assert fake_orphan.is_dir(), "open-PR orphan must stay in pending-pr/"
    pr_link = fake_orphan / "PR-LINK.md"
    assert pr_link.is_file(), "open-PR orphan must get PR-LINK.md so next sweep uses normal path"
    assert "pull/42" in pr_link.read_text(encoding="utf-8")


def test_reconcile_orphan_closed_unmerged_moves_to_blocked(
    fake_orphan: Path,
    fake_ctx: SweepContext,
    fake_queue_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_subprocess(
        monkeypatch,
        stdout=json.dumps({
            "pr": {
                "state": "closed",
                "url": "https://github.com/emp3thy/ralph/pull/7",
                "pr_id": 7,
                "merged_at": None,
            },
            "branch_exists": None,
        }),
        returncode=0,
    )

    action = reconcile_orphan(fake_orphan, fake_ctx)

    assert action == ReconcileAction.MOVED_TO_BLOCKED
    moved = fake_queue_root / "blocked" / "STAGE-B-PLAN-03"
    assert moved.is_dir()
    assert (moved / "PR-LINK.md").is_file()


def test_reconcile_orphan_no_pr_branch_exists_goes_to_blocked(
    fake_orphan: Path,
    fake_ctx: SweepContext,
    fake_queue_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_subprocess(
        monkeypatch,
        stdout=json.dumps({"pr": None, "branch_exists": True}),
        returncode=0,
    )

    action = reconcile_orphan(fake_orphan, fake_ctx)

    assert action == ReconcileAction.MOVED_TO_BLOCKED
    moved = fake_queue_root / "blocked" / "STAGE-B-PLAN-03"
    assert moved.is_dir()
    assert not (moved / "PR-LINK.md").is_file(), "no PR -> no URL to write"


def test_reconcile_orphan_no_pr_branch_missing_goes_to_inbox(
    fake_orphan: Path,
    fake_ctx: SweepContext,
    fake_queue_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_subprocess(
        monkeypatch,
        stdout=json.dumps({"pr": None, "branch_exists": False}),
        returncode=0,
    )

    action = reconcile_orphan(fake_orphan, fake_ctx)

    assert action == ReconcileAction.MOVED_TO_INBOX
    moved = fake_queue_root / "inbox" / "STAGE-B-PLAN-03"
    assert moved.is_dir()
    assert not (moved / "PR-LINK.md").is_file()


def test_reconcile_orphan_api_error_returns_keep_api_error(
    fake_orphan: Path,
    fake_ctx: SweepContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_subprocess(
        monkeypatch,
        stdout="",
        returncode=3,
        stderr="github error: HTTP 500",
    )

    action = reconcile_orphan(fake_orphan, fake_ctx)

    assert action == ReconcileAction.KEEP_API_ERROR
    assert fake_orphan.is_dir(), "API error must leave orphan in place"


def test_reconcile_orphan_validation_error_raises(
    fake_orphan: Path,
    fake_ctx: SweepContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_subprocess(
        monkeypatch,
        stdout="",
        returncode=2,
        stderr="error: GH_TOKEN required",
    )

    with pytest.raises(ReconcileError):
        reconcile_orphan(fake_orphan, fake_ctx)


def test_reconcile_orphan_malformed_json_raises(
    fake_orphan: Path,
    fake_ctx: SweepContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_subprocess(monkeypatch, stdout="not valid json {{{", returncode=0)
    with pytest.raises(ReconcileError):
        reconcile_orphan(fake_orphan, fake_ctx)


def test_reconcile_orphan_dry_run_does_not_move(
    fake_orphan: Path,
    fake_ctx: SweepContext,
    fake_queue_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_subprocess(
        monkeypatch,
        stdout=json.dumps({
            "pr": {
                "state": "merged",
                "url": "https://github.com/emp3thy/ralph/pull/25",
                "pr_id": 25,
                "merged_at": "2026-05-26T18:00:00Z",
            },
            "branch_exists": None,
        }),
        returncode=0,
    )

    action = reconcile_orphan(fake_orphan, fake_ctx, dry_run=True)

    assert action == ReconcileAction.MOVED_TO_DONE  # action is what WOULD happen
    assert fake_orphan.is_dir(), "dry-run must not move the dir"
    assert not (fake_queue_root / "done" / "STAGE-B-PLAN-03").exists()


def test_reconcile_orphan_invokes_lookup_with_correct_args(
    fake_orphan: Path,
    fake_ctx: SweepContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invocations = _stub_subprocess(
        monkeypatch,
        stdout=json.dumps({"pr": None, "branch_exists": False}),
        returncode=0,
    )

    reconcile_orphan(fake_orphan, fake_ctx)

    assert len(invocations) == 1
    argv = invocations[0]
    assert "--branch" in argv
    branch_value = argv[argv.index("--branch") + 1]
    assert branch_value == "ralph/STAGE-B-PLAN-03"
    assert "--include-branch-check" in argv


# ---------------------------------------------------------------------------
# reconcile_all
# ---------------------------------------------------------------------------


def _make_orphan(pending_dir: Path, pbi_id: str) -> Path:
    d = pending_dir / pbi_id
    d.mkdir()
    (d / "PBI.md").write_text(f"---\nid: {pbi_id}\n---\n", encoding="utf-8")
    (d / "HISTORY.md").write_text("", encoding="utf-8")
    return d


def test_reconcile_all_processes_every_orphan(
    fake_queue_root: Path,
    fake_ctx: SweepContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending = fake_queue_root / "pending-pr"
    _make_orphan(pending, "A")
    _make_orphan(pending, "B")
    _make_orphan(pending, "C")

    # Cycle through three different states.
    state_payloads: Iterable[str] = iter([
        json.dumps({"pr": {"state": "merged", "url": "u1", "pr_id": 1, "merged_at": "t"}, "branch_exists": None}),
        json.dumps({"pr": {"state": "open", "url": "u2", "pr_id": 2, "merged_at": None}, "branch_exists": None}),
        json.dumps({"pr": None, "branch_exists": False}),
    ])

    def _fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=argv,
            returncode=0,
            stdout=next(state_payloads),
            stderr="",
        )

    monkeypatch.setattr("ralph_executor.sweep.reconcile.subprocess.run", _fake_run)

    report = reconcile_all(fake_ctx)

    assert set(report.actions.keys()) == {"A", "B", "C"}
    assert ReconcileAction.MOVED_TO_DONE in report.actions.values()
    assert ReconcileAction.KEEP_PENDING in report.actions.values()
    assert ReconcileAction.MOVED_TO_INBOX in report.actions.values()


def test_reconcile_all_isolates_per_pbi_failures(
    fake_queue_root: Path,
    fake_ctx: SweepContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending = fake_queue_root / "pending-pr"
    _make_orphan(pending, "A")
    _make_orphan(pending, "B")

    state_payloads: Iterable[tuple[str, int]] = iter([
        ("", 2),  # A fails validation
        (json.dumps({"pr": None, "branch_exists": False}), 0),  # B succeeds
    ])

    def _fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        out, code = next(state_payloads)
        return subprocess.CompletedProcess(args=argv, returncode=code, stdout=out, stderr="")

    monkeypatch.setattr("ralph_executor.sweep.reconcile.subprocess.run", _fake_run)

    report = reconcile_all(fake_ctx)

    assert "A" in report.errors
    assert report.actions.get("B") == ReconcileAction.MOVED_TO_INBOX


def test_reconcile_all_skips_dirs_with_pr_link(
    fake_queue_root: Path,
    fake_ctx: SweepContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending = fake_queue_root / "pending-pr"
    healthy = _make_orphan(pending, "HEALTHY")
    (healthy / "PR-LINK.md").write_text("PR ID 99\n", encoding="utf-8")
    _make_orphan(pending, "ORPHAN")

    _stub_subprocess(
        monkeypatch,
        stdout=json.dumps({"pr": None, "branch_exists": False}),
        returncode=0,
    )

    report = reconcile_all(fake_ctx)

    assert "ORPHAN" in report.actions
    assert "HEALTHY" not in report.actions
```

- [ ] **Step 3.4: Run the failing tests**

```bash
uv run pytest tests/executor/sweep/test_reconcile.py -v
```

Expected: **all FAIL** at collection time — `ImportError: cannot import 'reconcile' from 'ralph_executor.sweep'`.

- [ ] **Step 3.5: Commit**

```bash
git -C /c/Users/gethi/source/ralph add ralph_executor/sweep/types.py tests/executor/sweep/test_reconcile.py
git -C /c/Users/gethi/source/ralph commit -m "test(sweep): failing tests for reconcile + ReconcileAction/Report types"
```

---

## Task 4: Implement `reconcile.py`

**Files:**
- Create: `ralph_executor/sweep/reconcile.py`

- [ ] **Step 4.1: Write the module skeleton**

Create `ralph_executor/sweep/reconcile.py`:

```python
"""Reconcile orphan ``pending-pr/<PBI-ID>/`` directories.

An "orphan" is a pending-pr directory missing ``PR-LINK.md`` — typically
created by manual gh squash-merges during operator-driven babysit cycles,
or by older PBIs predating PR-LINK.md introduction.

This module is called from two places:

- ``sweep/runner.py``'s ``run()`` loop, automatically, when an orphan
  is found during a sweep pass.
- ``ralph-executor reconcile`` CLI subcommand, on operator demand.

Both paths delegate to ``reconcile_orphan`` per PBI; ``reconcile_all``
is the iteration wrapper used by the CLI.

Subprocess shape: the staged ``pr/scripts/lookup_by_branch.py`` is
invoked via ``subprocess.run``. JSON on stdout, exit 0/2/3 per skill
convention. ``cfg.bot_author_email`` is unrelated here — reconcile
doesn't read PBI ownership, only PR state.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ralph_executor.sweep.runner import SweepContext
from ralph_executor.sweep.types import ReconcileAction, ReconcileReport

log = logging.getLogger(__name__)


class ReconcileError(RuntimeError):
    """Raised on a programming-error response from lookup_by_branch
    (exit 2) or on malformed JSON. API errors (exit 3) do NOT raise —
    they return ``KEEP_API_ERROR`` so a transient host outage doesn't
    crash the sweep pass.
    """


@dataclass(frozen=True)
class _LookupResult:
    """Parsed JSON payload from lookup_by_branch.py."""

    state: str | None        # "open" | "merged" | "closed" | None (no PR)
    url: str | None
    pr_id: int | None
    branch_exists: bool | None
```

- [ ] **Step 4.2: Add the subprocess invocation + JSON parser**

Append to the same file:

```python
def _invoke_lookup(
    ctx: SweepContext,
    branch: str,
    repo: str,
) -> _LookupResult:
    """Run lookup_by_branch.py and parse stdout.

    Raises ReconcileError on exit 2 / malformed JSON.
    Returns ``_LookupResult(state="api_error", ...)`` on exit 3 so the
    caller can map to KEEP_API_ERROR without raising.
    """
    script = ctx.ado_pr_scripts_path / "lookup_by_branch.py"
    proc = subprocess.run(
        [
            sys.executable,
            str(script),
            "--branch",
            branch,
            "--repo",
            repo,
            "--include-branch-check",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode == 2:
        raise ReconcileError(
            f"lookup_by_branch validation error (exit 2): {proc.stderr.strip()}"
        )
    if proc.returncode == 3:
        log.warning(
            "reconcile: lookup_by_branch API error (exit 3) for branch %s: %s",
            branch,
            proc.stderr.strip(),
        )
        return _LookupResult(state="api_error", url=None, pr_id=None, branch_exists=None)
    if proc.returncode != 0:
        raise ReconcileError(
            f"lookup_by_branch unexpected exit {proc.returncode}: {proc.stderr.strip()}"
        )

    try:
        payload: dict[str, Any] = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ReconcileError(
            f"lookup_by_branch returned malformed JSON: {exc} (stdout={proc.stdout!r})"
        ) from exc

    pr = payload.get("pr")
    if pr is None:
        return _LookupResult(
            state=None,
            url=None,
            pr_id=None,
            branch_exists=payload.get("branch_exists"),
        )
    return _LookupResult(
        state=pr["state"],
        url=pr["url"],
        pr_id=int(pr["pr_id"]),
        branch_exists=payload.get("branch_exists"),
    )
```

- [ ] **Step 4.3: Add the per-orphan reconcile function**

Append to the same file:

```python
def _resolve_repo_name(ctx: SweepContext) -> str:
    """Extract the GitHub repo name from ``ctx.queue_root``.

    The queue_root is ``<repo>/.ralph``, so its parent is the repo
    checkout. The directory name is the repo name (ralph's
    one-PBI-per-feature-branch convention).
    """
    return ctx.queue_root.parent.name


def reconcile_orphan(
    pbi_dir: Path,
    ctx: SweepContext,
    *,
    dry_run: bool = False,
) -> ReconcileAction:
    """Reconcile a single orphan pending-pr/ directory.

    Returns the action that WAS applied (or would be applied, when
    ``dry_run=True``). Raises ReconcileError on programming errors.
    """
    pbi_id = pbi_dir.name
    branch = f"ralph/{pbi_id}"
    repo = _resolve_repo_name(ctx)
    queue_root = ctx.queue_root

    result = _invoke_lookup(ctx, branch=branch, repo=repo)

    if result.state == "api_error":
        return ReconcileAction.KEEP_API_ERROR

    if result.state == "open":
        # Stay in pending-pr/, but write PR-LINK.md so the next sweep
        # iteration picks it up via the normal _read_pr_id path.
        if not dry_run:
            _write_pr_link(pbi_dir, url=result.url, pr_id=result.pr_id)
        return ReconcileAction.KEEP_PENDING

    if result.state == "merged":
        dest = queue_root / "done" / pbi_id
        if not dry_run:
            _move_dir(pbi_dir, dest)
            _write_pr_link(dest, url=result.url, pr_id=result.pr_id)
        return ReconcileAction.MOVED_TO_DONE

    if result.state == "closed":
        dest = queue_root / "blocked" / pbi_id
        if not dry_run:
            _move_dir(pbi_dir, dest)
            _write_pr_link(dest, url=result.url, pr_id=result.pr_id)
        return ReconcileAction.MOVED_TO_BLOCKED

    # state is None — no PR exists. Use branch existence to pick dest.
    if result.branch_exists:
        dest = queue_root / "blocked" / pbi_id
        action = ReconcileAction.MOVED_TO_BLOCKED
    else:
        dest = queue_root / "inbox" / pbi_id
        action = ReconcileAction.MOVED_TO_INBOX
    if not dry_run:
        _move_dir(pbi_dir, dest)
    return action


def _move_dir(src: Path, dest: Path) -> None:
    """Atomic-ish move via shutil.move. Wraps OSError to ReconcileError."""
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            raise ReconcileError(f"destination {dest} already exists; refusing to overwrite")
        shutil.move(str(src), str(dest))
    except OSError as exc:
        raise ReconcileError(f"failed to move {src} -> {dest}: {exc}") from exc


def _write_pr_link(pbi_dir: Path, *, url: str | None, pr_id: int | None) -> None:
    """Write PR-LINK.md with the discovered PR id + URL.

    Matches the shape ralph's normal flow writes: the file has a
    parseable ``PR ID <n>`` line so ``_read_pr_id`` in runner.py can
    consume it on the next sweep iteration.
    """
    if url is None or pr_id is None:
        return
    content = (
        f"# PR Link\n\n"
        f"PR ID {pr_id}\n"
        f"URL: {url}\n\n"
        f"_Reconciled by ralph-executor reconcile on {_now_iso()}._\n"
    )
    try:
        (pbi_dir / "PR-LINK.md").write_text(content, encoding="utf-8")
    except OSError as exc:
        raise ReconcileError(f"failed to write PR-LINK.md in {pbi_dir}: {exc}") from exc


def _now_iso() -> str:
    """Wall-clock timestamp for audit. Module-level for monkeypatching."""
    from datetime import UTC, datetime
    return datetime.now(tz=UTC).isoformat()
```

- [ ] **Step 4.4: Add `reconcile_all`**

Append:

```python
def reconcile_all(
    ctx: SweepContext,
    *,
    dry_run: bool = False,
) -> ReconcileReport:
    """Iterate ``.ralph/pending-pr/`` and reconcile every orphan.

    Per-PBI failures are captured in ``report.errors`` without aborting.
    Dirs that already have PR-LINK.md are skipped (sweep handles them
    via the normal path).
    """
    pending_dir = ctx.queue_root / "pending-pr"
    actions: dict[str, ReconcileAction] = {}
    errors: dict[str, str] = {}

    if not pending_dir.is_dir():
        return ReconcileReport(actions={}, errors={})

    for entry in sorted(pending_dir.iterdir()):
        if not entry.is_dir():
            continue
        if (entry / "PR-LINK.md").is_file():
            continue
        pbi_id = entry.name
        try:
            action = reconcile_orphan(entry, ctx, dry_run=dry_run)
            actions[pbi_id] = action
            log.info(
                "reconcile: %s -> %s%s",
                pbi_id,
                action.value,
                " (dry-run)" if dry_run else "",
            )
        except ReconcileError as err:
            errors[pbi_id] = str(err)
            log.warning("reconcile: failed for %s: %s", pbi_id, err)

    return ReconcileReport(actions=actions, errors=errors)
```

- [ ] **Step 4.5: Run the failing tests**

```bash
uv run pytest tests/executor/sweep/test_reconcile.py -v
```

Expected: **all PASS**.

If `test_reconcile_orphan_invokes_lookup_with_correct_args` fails because the argv order differs: the test asserts `--branch` and `--include-branch-check` are present and that the branch value follows `--branch`. The implementation's argv builder in Step 4.2 satisfies both. If something else moved, update the implementation to match the test contract (the test is the spec).

- [ ] **Step 4.6: Run ruff + mypy**

```bash
uv run ruff check ralph_executor/sweep/reconcile.py
uv run mypy --strict ralph_executor/sweep/reconcile.py
```

Expected: green.

- [ ] **Step 4.7: Commit**

```bash
git -C /c/Users/gethi/source/ralph add ralph_executor/sweep/reconcile.py
git -C /c/Users/gethi/source/ralph commit -m "feat(sweep): add reconcile.py for orphan pending-pr/ resolution"
```

---

## Task 5: Wire reconcile into `sweep/runner.py`

**Files:**
- Modify: `ralph_executor/sweep/runner.py:201-206`
- Modify: `tests/executor/sweep/test_runner.py` (existing missing-PR-LINK test)

- [ ] **Step 5.1: Find the existing "missing PR-LINK.md" test**

```bash
grep -n "PR-LINK.md is missing" tests/executor/sweep/test_runner.py
```

If a test exists asserting the old error string, note its name. Update it in Step 5.4.

- [ ] **Step 5.2: Replace the `run()` loop body in `runner.py`**

In `ralph_executor/sweep/runner.py`, find the `run()` function (around line 194). Replace the per-PBI loop body (currently lines 201–206):

```python
    for pbi_dir in pbis:
        try:
            actions.append(_process_pbi(pbi_dir=pbi_dir, ctx=ctx))
        except _SweepPbiError as err:
            errors.append(f"{pbi_dir.name}: {err}")
            log.warning("sweep error for %s: %s", pbi_dir.name, err)
```

with:

```python
    from ralph_executor.sweep.reconcile import (  # local import avoids cycle
        ReconcileAction,
        ReconcileError,
        reconcile_orphan,
    )

    for pbi_dir in pbis:
        if not (pbi_dir / "PR-LINK.md").is_file():
            try:
                action = reconcile_orphan(pbi_dir, ctx)
                if action == ReconcileAction.KEEP_API_ERROR:
                    log.warning(
                        "sweep: reconcile API error for %s; will retry next iteration",
                        pbi_dir.name,
                    )
                else:
                    log.info(
                        "sweep: reconciled %s -> %s",
                        pbi_dir.name,
                        action.value,
                    )
            except ReconcileError as err:
                errors.append(f"{pbi_dir.name}: reconcile error: {err}")
                log.warning("sweep: reconcile failed for %s: %s", pbi_dir.name, err)
            continue

        try:
            actions.append(_process_pbi(pbi_dir=pbi_dir, ctx=ctx))
        except _SweepPbiError as err:
            errors.append(f"{pbi_dir.name}: {err}")
            log.warning("sweep error for %s: %s", pbi_dir.name, err)
```

The local import is intentional: `reconcile.py` imports `SweepContext` from `runner.py`, so importing reconcile at module-load time creates a cycle. Local import inside `run()` breaks it without restructuring.

- [ ] **Step 5.3: Verify the import-cycle hypothesis quickly**

Run:

```bash
uv run python -c "from ralph_executor.sweep import runner, reconcile; print('ok')"
```

Expected: `ok`. If `ImportError: cannot import name 'SweepContext'`, the cycle is real and the local import is necessary. If the line prints `ok` with a top-level import too, you can move the import up — but the local-import shape is correct either way.

- [ ] **Step 5.4: Update or remove the legacy missing-PR-LINK test in test_runner.py**

If Step 5.1 found a test that asserts `"PR-LINK.md is missing; cannot determine PR id"` in sweep errors, rewrite it to:

```python
def test_run_reconciles_orphan_when_pr_link_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sweep no longer surfaces missing PR-LINK.md as a sweep error;
    instead it calls reconcile_orphan and continues."""
    from ralph_executor.sweep.reconcile import ReconcileAction
    # Set up a queue_root with one orphan
    queue = tmp_path / ".ralph"
    for sub in ("inbox", "current", "pending-pr", "done", "blocked"):
        (queue / sub).mkdir(parents=True)
    orphan = queue / "pending-pr" / "ORPHAN-1"
    orphan.mkdir()
    (orphan / "PBI.md").write_text("---\nid: ORPHAN-1\n---\n", encoding="utf-8")

    # Monkeypatch reconcile_orphan at the source module — the local
    # `from ... import reconcile_orphan` in run() re-resolves the name
    # against ralph_executor.sweep.reconcile at call time, so patching
    # the source module works regardless of import shape.
    monkeypatch.setattr(
        "ralph_executor.sweep.reconcile.reconcile_orphan",
        lambda pbi_dir, ctx, **kwargs: ReconcileAction.MOVED_TO_INBOX,
    )

    from datetime import UTC, datetime, timedelta
    from ralph_executor.sweep.runner import SweepConfig, SweepContext, run

    ctx = SweepContext(
        queue_root=queue,
        ado_pr_scripts_path=tmp_path / "pr_scripts_unused",
        config=SweepConfig(
            ralph_author_email="ralph@example.com",
            max_attempts=20,
            stale_threshold=timedelta(days=3),
            now=datetime.now(tz=UTC),
        ),
    )
    (tmp_path / "pr_scripts_unused").mkdir()

    result = run(ctx=ctx)

    assert len(result.errors) == 0, f"orphan should not surface as sweep error: {result.errors}"
    assert all("ORPHAN-1" not in r.pbi_id for r in result.actions), (
        "reconcile path bypasses actions list; orphan should not be in result.actions"
    )
```

(Note: the spec for SweepContext fields used here matches Task 3's `fake_ctx` fixture pattern.)

If Step 5.1 found NO matching test (this codebase may not have one), skip this step and proceed.

- [ ] **Step 5.5: Run the sweep test module**

```bash
uv run pytest tests/executor/sweep/ -v
```

Expected: all green.

- [ ] **Step 5.6: Run ruff + mypy on the touched files**

```bash
uv run ruff check ralph_executor/sweep/runner.py
uv run mypy --strict ralph_executor/sweep/runner.py
```

- [ ] **Step 5.7: Commit**

```bash
git -C /c/Users/gethi/source/ralph add ralph_executor/sweep/runner.py tests/executor/sweep/test_runner.py
git -C /c/Users/gethi/source/ralph commit -m "feat(sweep): wire reconcile into run() for orphan pending-pr/ entries"
```

---

## Task 6: Add `ralph-executor reconcile` CLI subcommand

**Files:**
- Modify: `ralph_executor/cli.py` (around line 165, after `scaffold_parser`)
- Create: `tests/executor/test_cli_reconcile.py`

- [ ] **Step 6.1: Write the failing CLI test**

Create `tests/executor/test_cli_reconcile.py`:

```python
"""Tests for the ``ralph-executor reconcile`` subcommand."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from ralph_executor.cli import main as cli_main


def _make_orphan(pending_dir: Path, pbi_id: str) -> Path:
    d = pending_dir / pbi_id
    d.mkdir()
    (d / "PBI.md").write_text(f"---\nid: {pbi_id}\n---\n", encoding="utf-8")
    return d


@pytest.fixture
def fake_repo_with_orphan(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    queue = repo / ".ralph"
    for sub in ("inbox", "current", "pending-pr", "done", "blocked"):
        (queue / sub).mkdir(parents=True)
    (queue / "config.toml").write_text(
        'git_host = "github"\ngh_owner = "emp3thy"\n',
        encoding="utf-8",
    )
    _make_orphan(queue / "pending-pr", "ORPHAN-1")
    return repo


def test_reconcile_subcommand_calls_reconcile_all(
    fake_repo_with_orphan: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from ralph_executor.sweep.types import ReconcileAction, ReconcileReport

    captured_calls: list[bool] = []

    def _fake_reconcile_all(ctx: object, *, dry_run: bool = False) -> ReconcileReport:
        captured_calls.append(dry_run)
        return ReconcileReport(
            actions={"ORPHAN-1": ReconcileAction.MOVED_TO_INBOX},
            errors={},
        )

    monkeypatch.setattr("ralph_executor.cli.reconcile_all", _fake_reconcile_all)
    monkeypatch.setenv("RALPH_ADO_AUTHOR_EMAIL", "ralph@example.com")
    monkeypatch.setenv("GH_TOKEN", "fake")
    exit_code = cli_main(["reconcile", "--repo", str(fake_repo_with_orphan)])

    assert exit_code == 0
    assert captured_calls == [False]
    out = capsys.readouterr().out
    assert "ORPHAN-1" in out
    assert "moved_to_inbox" in out


def test_reconcile_dry_run_flag_flows_through(
    fake_repo_with_orphan: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ralph_executor.sweep.types import ReconcileAction, ReconcileReport

    captured_calls: list[bool] = []

    def _fake_reconcile_all(ctx: object, *, dry_run: bool = False) -> ReconcileReport:
        captured_calls.append(dry_run)
        return ReconcileReport(actions={}, errors={})

    monkeypatch.setattr("ralph_executor.cli.reconcile_all", _fake_reconcile_all)
    monkeypatch.setenv("RALPH_ADO_AUTHOR_EMAIL", "ralph@example.com")
    monkeypatch.setenv("GH_TOKEN", "fake")
    exit_code = cli_main(
        ["reconcile", "--repo", str(fake_repo_with_orphan), "--dry-run"]
    )

    assert exit_code == 0
    assert captured_calls == [True]
```

- [ ] **Step 6.2: Run failing tests**

```bash
uv run pytest tests/executor/test_cli_reconcile.py -v
```

Expected: FAIL — `argparse error: invalid choice: 'reconcile'` (or similar).

- [ ] **Step 6.3: Add the subparser in `cli.py`**

In `ralph_executor/cli.py`, find the end of the `scaffold_parser` block (around line 175–180). Add a new subparser after it:

```python
    reconcile_parser = subparsers.add_parser(
        "reconcile",
        help=(
            "Reconcile orphan pending-pr/ directories (those without "
            "PR-LINK.md) by looking up their PR via the host API and "
            "moving them to done/blocked/inbox based on PR state."
        ),
    )
    reconcile_group = reconcile_parser.add_mutually_exclusive_group()
    reconcile_group.add_argument(
        "--repo",
        type=Path,
        help="Path to the repo Ralph operates on.",
    )
    reconcile_group.add_argument(
        "--workspace",
        type=str,
        help="Workspace name under $RALPH_HOME.",
    )
    reconcile_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the actions that would be taken without moving any files.",
    )
```

- [ ] **Step 6.4: Add the dispatch + import in cli.py**

At the top of `cli.py`, add the imports for reconcile_all and SweepContext:

```python
from ralph_executor.sweep.reconcile import reconcile_all
from ralph_executor.sweep.runner import SweepConfig, SweepContext
```

In `main()`, find the existing if-elif ladder for subcommand dispatch. After `scaffold` handling, add:

```python
    if args.subcommand == "reconcile":
        return _cmd_reconcile(args)
```

Then add the `_cmd_reconcile` function near the other `_cmd_*` helpers in `cli.py`:

```python
def _cmd_reconcile(args: argparse.Namespace) -> int:
    """Handler for ``ralph-executor reconcile``.

    Loads the cfg via the same path the loop uses, stages host skills
    (so the lookup_by_branch.py script lives at the expected location),
    then calls ``reconcile_all`` and prints a summary table.
    """
    from datetime import UTC, datetime, timedelta

    try:
        cfg = load_config()
    except ConfigError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    _apply_overrides(cfg, args)
    if not cfg.git_host:
        print("error: git_host is not set; cannot reconcile", file=sys.stderr)
        return 2

    scripts_path = prepare_host_environment(cfg)
    ctx = SweepContext(
        queue_root=cfg.repo_path / ".ralph",
        ado_pr_scripts_path=scripts_path,
        config=SweepConfig(
            ralph_author_email=cfg.bot_author_email or "reconcile@ralph.local",
            max_attempts=cfg.max_attempts,
            stale_threshold=timedelta(days=cfg.stale_days),
            now=datetime.now(tz=UTC),
        ),
    )

    report = reconcile_all(ctx, dry_run=args.dry_run)

    _print_reconcile_report(report, dry_run=args.dry_run)
    return 0


def _print_reconcile_report(report: ReconcileReport, *, dry_run: bool) -> None:
    """Print a small table summarising reconcile_all output."""
    prefix = "would: " if dry_run else ""
    if not report.actions and not report.errors:
        print("reconcile: no orphans found in pending-pr/")
        return
    print(f"{'PBI-ID':<40} {'Action':<20}")
    print("-" * 60)
    for pbi_id, action in sorted(report.actions.items()):
        print(f"{pbi_id:<40} {prefix}{action.value}")
    for pbi_id, err in sorted(report.errors.items()):
        print(f"{pbi_id:<40} ERROR: {err}")
    n_moves = sum(1 for a in report.actions.values()
                  if a.name.startswith("MOVED_"))
    n_stays = sum(1 for a in report.actions.values()
                  if a.name.startswith("KEEP_"))
    n_err = len(report.errors)
    print(
        f"\n{len(report.actions)} orphans processed: "
        f"{n_moves} moved, {n_stays} stays, {n_err} errors."
    )
```

Add the `ReconcileReport` import alongside the existing imports:

```python
from ralph_executor.sweep.types import ReconcileReport
```

- [ ] **Step 6.5: Run the failing tests**

```bash
uv run pytest tests/executor/test_cli_reconcile.py -v
```

Expected: **PASS**.

If a test fails because `prepare_host_environment` isn't easily mockable for the test fixture: add a `_skip_host_prep` shortcut for tests by allowing `cfg.git_host == "fake"` to bypass real staging. Or, simpler, monkeypatch `ralph_executor.cli.prepare_host_environment` in the test to return a `Path`.

- [ ] **Step 6.6: Run ruff + mypy**

```bash
uv run ruff check ralph_executor/cli.py
uv run mypy --strict ralph_executor/cli.py
```

- [ ] **Step 6.7: Commit**

```bash
git -C /c/Users/gethi/source/ralph add ralph_executor/cli.py tests/executor/test_cli_reconcile.py
git -C /c/Users/gethi/source/ralph commit -m "feat(cli): add 'ralph-executor reconcile' subcommand"
```

---

## Task 7: Full-suite green + smoke against the 8 real orphans

**Files:**
- None to modify (verification only)

- [ ] **Step 7.1: Sweep the test suite for residual references to the old missing-PR-LINK shape**

```bash
grep -rn "PR-LINK.md is missing" /c/Users/gethi/source/ralph/tests/ 2>&1
grep -rn "cannot determine PR id" /c/Users/gethi/source/ralph/tests/ 2>&1
```

If any test asserts on these strings, update to assert on reconcile behavior instead (mirror Step 5.4's pattern).

- [ ] **Step 7.2: Run the full test suite**

```bash
uv run pytest tests/ -v
```

Expected: all green. Common failure modes:

- A test mocked `_process_pbi` and expected it to be called for orphans. After this PBI, orphans go through `reconcile_orphan` instead and never reach `_process_pbi`. Update the test or the mock.
- A test asserted `result.errors` contained `"PR-LINK.md is missing"`. That error no longer fires; the test must assert the reconcile path.

- [ ] **Step 7.3: Run ruff format check + mypy on the whole package**

```bash
uv run ruff check ralph_executor/ skills/ tests/
uv run ruff format --check ralph_executor/ skills/ tests/
uv run mypy --strict ralph_executor/
```

Expected: all green.

- [ ] **Step 7.4: Smoke against the 8 real orphans (dry-run first)**

From the project root, with `GH_TOKEN` set:

```bash
uv run ralph-executor reconcile --dry-run --repo .
```

Expected output should list all 8 orphans:

```
PBI-ID                                   Action
------------------------------------------------------------
BUG-claude-stdout-streaming-windows      would: moved_to_done
STAGE-B-PLAN-03                          would: moved_to_done
STAGE-B-PLAN-04                          would: moved_to_done
STAGE-B-PLAN-08                          would: moved_to_done
STAGE-B-PLAN-11                          would: keep_pending
STAGE-B-PLAN-18-verifier                 would: moved_to_done
STAGE-B-PLAN-19a-loop-events             would: moved_to_done
STAGE-B-PLAN-20a-worktree                would: keep_pending

8 orphans processed: 6 moved, 2 stays, 0 errors.
```

If any orphan reports `ERROR:` instead of an action, investigate. The expected pattern is 6 merged PRs (move to done) + 2 open PRs (stay, write PR-LINK.md).

- [ ] **Step 7.5: Real reconcile run**

If dry-run looks correct:

```bash
uv run ralph-executor reconcile --repo .
```

Then verify:

```bash
ls .ralph/pending-pr/
# Should now contain only STAGE-B-PLAN-11/ and STAGE-B-PLAN-20a-worktree/, each with PR-LINK.md
ls .ralph/done/ | grep -E 'STAGE-B-PLAN-03|STAGE-B-PLAN-04|STAGE-B-PLAN-08|STAGE-B-PLAN-18-verifier|STAGE-B-PLAN-19a-loop-events|BUG-claude'
# Should list all 6 newly-moved dirs
cat .ralph/pending-pr/STAGE-B-PLAN-11/PR-LINK.md
# Should now exist with PR ID 28
```

- [ ] **Step 7.6: Run ralph for one iteration to confirm sweep no longer warns**

```bash
uv run ralph-executor --once
```

Expected: the sweep log line should now read `sweep: scanned N PBIs (actions=X, errors=0)` (zero orphan errors). If errors > 0 for orphan reasons, something didn't move correctly — investigate the dir state on disk.

- [ ] **Step 7.7: Commit the moved-orphan state**

```bash
git -C /c/Users/gethi/source/ralph add .ralph/
git -C /c/Users/gethi/source/ralph commit -m "chore(queue): reconcile 8 orphan pending-pr/ entries via ralph-executor reconcile"
```

---

## Acceptance (matches spec)

- [x] `skills/pr-github/scripts/lookup_by_branch.py` exists with JSON contract + exit 0/2/3 — Task 2
- [x] `ralph_executor/sweep/reconcile.py` exposes `reconcile_orphan` + `reconcile_all` — Task 4
- [x] Sweep auto-reconciles orphans; missing-PR-LINK no longer surfaces as a sweep error — Task 5
- [x] `ralph-executor reconcile [--dry-run]` exists — Task 6
- [x] PR-LINK.md written retroactively on terminal moves — Task 4 (`_write_pr_link`)
- [x] The 8 current orphans clear on first reconcile — Task 7.5
- [x] pytest / ruff / mypy strict all green — Task 7.2, 7.3
- [x] `responses` library used for skill HTTP mocking — Task 1
- [ ] GitHub issue filed for ADO `lookup_by_branch` follow-up — not in this plan; filed as part of the PBI creation step outside this plan

## Out of scope

- ADO `lookup_by_branch.py` (deferred via follow-up GH issue)
- Reconciling `current/`, `inbox/`, `done/`, `blocked/`, `archive/` — only `pending-pr/`
- Backfilling PR-LINK.md for already-`done/` PBIs from earlier history
- New skill operations beyond `lookup_by_branch`

## Operational notes

- **Branch:** lands on `ralph-queue` via PR to `main`, same shape as `STAGE-B-PLAN-*` PRs.
- **Ralph running?** Reconcile work touches `sweep/runner.py`. If ralph is running against this checkout, halt it before Task 5 and resume after Task 7.6.
- **Local imports:** Task 5 uses a function-local `from ralph_executor.sweep.reconcile import ...` to avoid an import cycle (reconcile imports SweepContext from runner). Step 5.3 verifies the cycle is real before relying on the local-import shape.
