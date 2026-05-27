# Sweep Auto-Merges Clean PRs — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sweep optionally auto-merges PRs that GitHub reports as `mergeable_state == "clean"`, gated by new TOML flag `auto_merge_clean_prs` (default `false`). Adds `merge_pr` sub-op to `pr-github` (with new exit code `4` for race-condition signal) and exposes raw `mergeable_state` from `show.py`.

**Architecture:** New `merge_pr.py` 7th sub-op in `pr-github`. show.py adds raw `mergeable_state` field alongside its existing cross-host `merge_status`. `PrSnapshot.merge_state: str` carries the raw value. `Action.MERGE_PR` enum value triggers the subprocess merge from sweep's act path. Config layered defaults < TOML < env (defaults `False`). depends_on `SWEEP-RECONCILE-ORPHANS` because both modify `SKILL.md`.

**Tech Stack:** Python 3.12, `requests`, `responses` (test mocking), `subprocess.run`, pytest, ruff, mypy strict.

**Spec:** `docs/superpowers/specs/2026-05-27-sweep-auto-merge-clean-prs-design.md`

---

## Confidence per task

All tasks ≥ 90%. Pre-flight verified against ralph-queue HEAD before writing.

| Task | % | Notes |
|---|---|---|
| 1. show.py exposes raw mergeable_state | 96% | Single field add at `show.py:251`. Test pattern from `tests/skills/test_pr_github.py` (`@responses.activate` + `responses.add`). Direct mirror. |
| 2. merge_pr.py | 91% | REST PUT documented. New exit code 4 deviates from existing 0/2/3 convention. Risk: GitHub's response shape for 405 vs 409 may differ slightly; mitigation: Step 2.3's tests cover BOTH status codes with mocked bodies; the implementation maps either to exit 4. |
| 3. _resolve_bool + auto_merge_clean_prs config knob + stub | 95% | `_resolve_int` (config.py:197) is the direct template. `clean_env` fixture pattern at `test_config_toml.py:23`. |
| 4. PrSnapshot.merge_state + Action.MERGE_PR + pr_state.py parse | 95% | `PrSnapshot` (types.py:50) + `Action` (types.py:70) + `pr_state.py:64-73` constructor — all single-line additions. |
| 5. SweepConfig.auto_merge_clean_prs + loop plumbing | 95% | SweepConfig (runner.py:33) frozen dataclass; field with default goes at end of class. `loop._run_sweep` builds SweepConfig; one extra kwarg. |
| 6. decide_action MERGE_PR branch + act subprocess | 93% | decide_action pattern at runner.py:111-157; act dispatch at runner.py:302-321. Risk: ordering of the new branch relative to existing branches matters; Step 6.2 specifies the exact insertion point and Step 6.5 covers ordering tests. |
| 7. Full-suite gate + smoke | 91% | Could surface existing tests literally constructing `PrSnapshot` or `SweepConfig` without the new kwargs. Step 7.1 grep finds them; defaults make most callers compile without changes. |

---

## File map

| File | Action | Responsibility |
|---|---|---|
| `skills/pr-github/scripts/show.py` | Modify (line 246–263 output dict) | Add `mergeable_state` raw field alongside existing `merge_status` |
| `skills/pr-github/scripts/merge_pr.py` | Create | 7th sub-op. argparse + PUT /pulls/{n}/merge + optional DELETE branch + exit codes 0/2/3/4 |
| `skills/pr-github/SKILL.md` | Modify | Document `merge_pr` operation in the op list |
| `ralph_executor/sweep/types.py` | Modify | Add `Action.MERGE_PR` + `PrSnapshot.merge_state: str` field |
| `ralph_executor/sweep/pr_state.py` | Modify (line 59–74 PrSnapshot constructor) | Parse `mergeable_state` from show.py JSON; default `"unknown"` |
| `ralph_executor/sweep/runner.py` | Modify | Add `SweepConfig.auto_merge_clean_prs`; insert MERGE_PR branch in `decide_action`; add MERGE_PR dispatch in act |
| `ralph_executor/config.py` | Modify | Add `_resolve_bool` helper + `auto_merge_clean_prs` field + resolver call + `_TOML_KNOWN_KEYS` entry |
| `ralph_executor/loop.py` | Modify (`_run_sweep`) | Pass `cfg.auto_merge_clean_prs` to SweepConfig constructor |
| `ralph_executor/setup_cmds.py` | Modify (CONFIG_TOML_STUB) | Document the new key |
| `tests/skills/test_pr_github.py` | Modify | Add show.py mergeable_state test + merge_pr.py test class |
| `tests/executor/sweep/test_pr_state.py` | Modify | Cover merge_state parsing + default |
| `tests/executor/sweep/test_runner.py` | Modify | decide_action MERGE_PR cases + act subprocess cases |
| `tests/executor/test_config_toml.py` | Modify | auto_merge_clean_prs layering + validation |

---

## Task 1: show.py exposes raw `mergeable_state` field

**Files:**
- Modify: `skills/pr-github/scripts/show.py:251` (output dict)
- Modify: `tests/skills/test_pr_github.py` (existing show.py test)

- [ ] **Step 1.1: Find an existing show.py test and add a `mergeable_state` assertion**

```bash
grep -n "def test_show\|def test_pr_github_show" /c/Users/gethi/source/ralph/tests/skills/test_pr_github.py | head -5
```

Pick one passing show.py test that uses `@responses.activate` and asserts on the output JSON. Add an assertion that the new field appears. Pattern (append inside one such test):

```python
# Inside the existing show test that already mocks the /pulls/{n} GET:
# the mock response already returns a PR dict. Make sure it has
# "mergeable_state" set to "clean" (or any GitHub value) and assert:
output = json.loads(result.stdout)
assert output["mergeable_state"] == "clean", output
```

If no existing test asserts on the full output dict, add a new tiny test directly:

```python
@responses.activate
def test_show_emits_mergeable_state_field() -> None:
    """Reconcile needs the raw GitHub mergeable_state alongside the
    cross-host merge_status. Verify show.py emits both."""
    responses.add(
        responses.GET,
        "https://api.github.com/repos/emp3thy/ralph/pulls/42",
        json={
            "number": 42,
            "state": "open",
            "merged": False,
            "title": "Test PR",
            "mergeable": True,
            "mergeable_state": "clean",
            "head": {"ref": "ralph/X"},
            "base": {"ref": "main"},
            "html_url": "https://github.com/emp3thy/ralph/pull/42",
            "draft": False,
            "user": {"login": "emp3thy"},
            "created_at": "2026-05-27T01:00:00Z",
            "closed_at": None,
            "requested_reviewers": [],
        },
        status=200,
    )
    responses.add(
        responses.GET,
        "https://api.github.com/repos/emp3thy/ralph/pulls/42/reviews",
        json=[],
        status=200,
    )
    responses.add(
        responses.GET,
        "https://api.github.com/repos/emp3thy/ralph/commits/HEAD/check-runs",
        json={"check_runs": []},
        status=200,
    )
    # Invoke show.py via the same subprocess pattern other tests in this
    # file use. Copy the helper if needed.
    import subprocess as _sp
    import sys as _sys
    from pathlib import Path as _Path
    script = (
        _Path(__file__).resolve().parents[2]
        / "skills" / "pr-github" / "scripts" / "show.py"
    )
    result = _sp.run(
        [_sys.executable, str(script), "--pr-id", "42", "--repo", "ralph"],
        env={"GH_TOKEN": "fake", "GH_OWNER": "emp3thy", "PATH": os.environ.get("PATH", "")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["mergeable_state"] == "clean"
    assert "merge_status" in payload  # existing field still present
```

- [ ] **Step 1.2: Run the new test — verify it fails**

```bash
uv run pytest tests/skills/test_pr_github.py::test_show_emits_mergeable_state_field -v
```

Expected: FAIL with `KeyError: 'mergeable_state'` or `AssertionError`.

- [ ] **Step 1.3: Add the field to show.py output dict**

Open `skills/pr-github/scripts/show.py`. Find the `output: dict[str, Any] = {` block starting at line 246. After the `"merge_status": _map_merge_status(pr),` line (line 251), insert:

```python
            "merge_status": _map_merge_status(pr),
            "mergeable_state": str(pr.get("mergeable_state") or "unknown"),
```

The `or "unknown"` falls back when GitHub returns `null` / missing (async merge-check not yet complete).

- [ ] **Step 1.4: Run the test — verify it passes**

```bash
uv run pytest tests/skills/test_pr_github.py::test_show_emits_mergeable_state_field -v
```

Expected: PASS.

- [ ] **Step 1.5: Run the full pr-github test module + ruff + mypy**

```bash
uv run pytest tests/skills/test_pr_github.py -v
uv run ruff check skills/pr-github/scripts/show.py
uv run mypy --strict skills/pr-github/scripts/show.py
```

Expected: all green.

- [ ] **Step 1.6: Commit**

```bash
git -C /c/Users/gethi/source/ralph add skills/pr-github/scripts/show.py tests/skills/test_pr_github.py
git -C /c/Users/gethi/source/ralph commit -m "feat(pr-github): expose raw mergeable_state in show.py output"
```

---

## Task 2: merge_pr.py

**Files:**
- Create: `skills/pr-github/scripts/merge_pr.py`
- Modify: `skills/pr-github/SKILL.md` (add to op list)
- Modify: `tests/skills/test_pr_github.py` (append merge_pr test cases)

- [ ] **Step 2.1: Helper for invoking merge_pr.py as subprocess (test side)**

Append to `tests/skills/test_pr_github.py`:

```python
# ---------------------------------------------------------------------------
# merge_pr
# ---------------------------------------------------------------------------

_MERGE_SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "skills" / "pr-github" / "scripts" / "merge_pr.py"
)


def _run_merge(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    base_env = {
        "GH_TOKEN": "fake-token",
        "GH_OWNER": "emp3thy",
        "PATH": os.environ.get("PATH", ""),
    }
    if env is not None:
        base_env.update(env)
    return subprocess.run(
        [sys.executable, str(_MERGE_SCRIPT), *args],
        capture_output=True,
        text=True,
        env=base_env,
        check=False,
    )
```

- [ ] **Step 2.2: Write the failing test cases**

Append:

```python
@responses.activate
def test_merge_pr_success_squashes_and_deletes_branch() -> None:
    responses.add(
        responses.PUT,
        "https://api.github.com/repos/emp3thy/ralph/pulls/42/merge",
        json={"merged": True, "sha": "abc123", "message": "OK"},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://api.github.com/repos/emp3thy/ralph/pulls/42",
        json={"head": {"ref": "ralph/X"}},
        status=200,
    )
    responses.add(
        responses.DELETE,
        "https://api.github.com/repos/emp3thy/ralph/git/refs/heads/ralph/X",
        status=204,
    )
    result = _run_merge("--pr-id", "42", "--repo", "ralph")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload == {
        "merged": True,
        "sha": "abc123",
        "pr_id": 42,
        "branch_deleted": True,
    }


@responses.activate
def test_merge_pr_no_delete_branch_flag() -> None:
    responses.add(
        responses.PUT,
        "https://api.github.com/repos/emp3thy/ralph/pulls/42/merge",
        json={"merged": True, "sha": "abc123", "message": "OK"},
        status=200,
    )
    # NO mock for DELETE — if merge_pr calls it, responses will raise.
    result = _run_merge("--pr-id", "42", "--repo", "ralph", "--no-delete-branch")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["branch_deleted"] is False


@responses.activate
def test_merge_pr_405_returns_exit_4() -> None:
    """405 Method Not Allowed: GitHub refused (e.g., required check now red)."""
    responses.add(
        responses.PUT,
        "https://api.github.com/repos/emp3thy/ralph/pulls/42/merge",
        json={"message": "Required status check is failing"},
        status=405,
    )
    result = _run_merge("--pr-id", "42", "--repo", "ralph")
    assert result.returncode == 4
    payload = json.loads(result.stdout)
    assert payload["merged"] is False
    assert payload["pr_id"] == 42
    assert "Required status check" in payload["reason"]


@responses.activate
def test_merge_pr_409_returns_exit_4() -> None:
    """409 Conflict: branch is out of date / merge conflict."""
    responses.add(
        responses.PUT,
        "https://api.github.com/repos/emp3thy/ralph/pulls/42/merge",
        json={"message": "Pull request is not mergeable"},
        status=409,
    )
    result = _run_merge("--pr-id", "42", "--repo", "ralph")
    assert result.returncode == 4
    payload = json.loads(result.stdout)
    assert payload["merged"] is False


@responses.activate
def test_merge_pr_500_returns_exit_3() -> None:
    responses.add(
        responses.PUT,
        "https://api.github.com/repos/emp3thy/ralph/pulls/42/merge",
        json={"message": "Internal server error"},
        status=500,
    )
    result = _run_merge("--pr-id", "42", "--repo", "ralph")
    assert result.returncode == 3
    assert "github error" in result.stderr


def test_merge_pr_missing_gh_token_exits_2() -> None:
    result = _run_merge(
        "--pr-id", "42", "--repo", "ralph",
        env={"GH_TOKEN": "", "GH_OWNER": "emp3thy"},
    )
    assert result.returncode == 2
    assert "GH_TOKEN" in result.stderr


def test_merge_pr_missing_pr_id_exits_2() -> None:
    result = _run_merge("--repo", "ralph")
    assert result.returncode == 2


@responses.activate
def test_merge_pr_method_rebase() -> None:
    """--merge-method=rebase puts rebase in the PUT body."""
    captured_bodies: list[dict[str, str]] = []

    def _capture(request: object) -> tuple[int, dict[str, str], str]:
        body = json.loads(request.body or "{}")  # type: ignore[attr-defined]
        captured_bodies.append(body)
        return (200, {}, json.dumps({"merged": True, "sha": "abc"}))

    responses.add_callback(
        responses.PUT,
        "https://api.github.com/repos/emp3thy/ralph/pulls/42/merge",
        callback=_capture,
    )
    responses.add(
        responses.GET,
        "https://api.github.com/repos/emp3thy/ralph/pulls/42",
        json={"head": {"ref": "ralph/X"}},
        status=200,
    )
    responses.add(
        responses.DELETE,
        "https://api.github.com/repos/emp3thy/ralph/git/refs/heads/ralph/X",
        status=204,
    )

    result = _run_merge(
        "--pr-id", "42", "--repo", "ralph", "--merge-method", "rebase"
    )
    assert result.returncode == 0, result.stderr
    assert captured_bodies == [{"merge_method": "rebase"}]
```

- [ ] **Step 2.3: Run the failing tests**

```bash
uv run pytest tests/skills/test_pr_github.py -k "merge_pr" -v
```

Expected: **all FAIL** with `FileNotFoundError` (merge_pr.py doesn't exist).

- [ ] **Step 2.4: Implement `skills/pr-github/scripts/merge_pr.py`**

Create the file:

```python
"""``pr-github merge_pr`` -- merge a PR by id (squash / merge / rebase).

REST endpoints used:
    PUT  /repos/{owner}/{repo}/pulls/{pr_number}/merge
        docs: https://docs.github.com/en/rest/pulls/pulls#merge-a-pull-request
    GET  /repos/{owner}/{repo}/pulls/{pr_number}
        (used to look up the source branch when --delete-branch is set)
    DELETE /repos/{owner}/{repo}/git/refs/heads/{branch}
        (only when --delete-branch; failure non-fatal — merge already done)

Used by ralph_executor.sweep.runner when cfg.auto_merge_clean_prs is True
and pr.merge_state == "clean".

Exit codes:
    0  merged successfully
    2  validation / arg / auth error
    3  GitHub HTTP error (5xx / network / rate-limit)
    4  GitHub refused the merge (405 / 409) — race: state changed since
       the predicate was evaluated. Sweep treats this as "not ready,
       retry next iteration".

The exit-4 deviation from the standard 0/2/3 convention is intentional;
sweep needs to distinguish a transient ralph-side problem (which surfaces
to the operator) from a stale predicate (which retries silently).
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
    parse_pr_id,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="merge_pr",
        description=(
            "Merge a PR via GitHub REST. Used by sweep when auto-merge "
            "is enabled and the PR is mergeable per GitHub."
        ),
    )
    parser.add_argument("--pr-id", required=True, help="Pull request number.")
    parser.add_argument("--repo", required=True, help="GitHub repository name (without owner).")
    parser.add_argument(
        "--merge-method",
        choices=("squash", "merge", "rebase"),
        default="squash",
        help="GitHub merge method (default: squash).",
    )
    parser.add_argument(
        "--no-delete-branch",
        action="store_true",
        help="Skip the DELETE call on the source ref after merge.",
    )
    return parser


def _merge(client: Any, repo: str, pr_id: int, method: str) -> dict[str, Any]:
    """PUT /pulls/{n}/merge. Returns the parsed JSON on success.

    Raises HttpError on 5xx. Raises FatalError-shaped via exit-4 for 405/409
    (caller checks exit code).
    """
    path = f"repos/{client.owner}/{repo}/pulls/{pr_id}/merge"
    response = client.session.put(
        f"https://api.github.com/{path}",
        headers=client._headers(),
        json={"merge_method": method},
        timeout=30,
    )
    if response.status_code in (405, 409):
        # Map to exit 4 via a sentinel; main() inspects.
        return {"_refused": True, "_reason": _extract_message(response)}
    if response.status_code >= 400:
        raise HttpError(
            f"PUT /{path}: HTTP {response.status_code}: {response.text}"
        )
    body = response.json()
    if not isinstance(body, dict):
        raise HttpError(f"PUT /{path} returned non-dict: {type(body).__name__}")
    return body


def _lookup_source_branch(client: Any, repo: str, pr_id: int) -> str:
    """GET /pulls/{n} to discover the source branch name for deletion."""
    path = f"repos/{client.owner}/{repo}/pulls/{pr_id}"
    response = client.session.get(
        f"https://api.github.com/{path}",
        headers=client._headers(),
        timeout=30,
    )
    if response.status_code >= 400:
        raise HttpError(
            f"GET /{path}: HTTP {response.status_code}: {response.text}"
        )
    payload = response.json()
    head = payload.get("head") if isinstance(payload, dict) else None
    if not isinstance(head, dict):
        raise HttpError(f"GET /{path}: missing or invalid 'head' field")
    ref = head.get("ref")
    if not isinstance(ref, str) or not ref:
        raise HttpError(f"GET /{path}: missing 'head.ref'")
    return ref


def _delete_branch(client: Any, repo: str, branch: str) -> bool:
    """DELETE /git/refs/heads/{branch}. Returns True on 204."""
    path = f"repos/{client.owner}/{repo}/git/refs/heads/{branch}"
    response = client.session.delete(
        f"https://api.github.com/{path}",
        headers=client._headers(),
        timeout=30,
    )
    # 204 = deleted, 422 = already gone. Both treated as success.
    return response.status_code in (204, 422)


def _extract_message(response: Any) -> str:
    """Pull GitHub's human-readable error message from a response body."""
    try:
        body = response.json()
        if isinstance(body, dict):
            msg = body.get("message")
            if isinstance(msg, str):
                return msg
    except (ValueError, KeyError, AttributeError):
        pass
    return response.text[:200] if response.text else ""


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        pr_id = parse_pr_id(args.pr_id)
        if not args.repo.strip():
            raise FatalError("--repo must be non-empty")
        repo = args.repo.strip()
        client = load_client()
    except FatalError as err:
        return fail(str(err))

    try:
        merge_result = _merge(client, repo, pr_id, args.merge_method)
    except HttpError as err:
        return handle_http_error(err)

    if merge_result.get("_refused"):
        print(json.dumps({
            "merged": False,
            "reason": str(merge_result["_reason"]),
            "pr_id": pr_id,
        }))
        return 4

    branch_deleted = False
    if not args.no_delete_branch:
        try:
            branch = _lookup_source_branch(client, repo, pr_id)
            branch_deleted = _delete_branch(client, repo, branch)
        except HttpError as err:
            # Merge already succeeded — log + continue. Don't fail exit 0.
            print(f"warning: branch delete failed: {err}", file=sys.stderr)

    print(json.dumps({
        "merged": True,
        "sha": str(merge_result.get("sha", "")),
        "pr_id": pr_id,
        "branch_deleted": branch_deleted,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2.5: Update `skills/pr-github/SKILL.md` op list**

Open `skills/pr-github/SKILL.md`. Find the section that lists the operations. Append a row / bullet for `merge_pr` mirroring the existing format. (Exact format depends on the current SKILL.md shape; pattern-match the existing entries.)

Run:

```bash
grep -n "create_pr\|show\.py\|read_threads" /c/Users/gethi/source/ralph/skills/pr-github/SKILL.md | head -10
```

to find where to insert the new operation entry.

- [ ] **Step 2.6: Run the failing tests — verify they pass**

```bash
uv run pytest tests/skills/test_pr_github.py -k "merge_pr" -v
```

Expected: **8 PASS**.

- [ ] **Step 2.7: Run ruff + mypy**

```bash
uv run ruff check skills/pr-github/scripts/merge_pr.py
uv run mypy --strict skills/pr-github/scripts/merge_pr.py
```

Expected: green.

- [ ] **Step 2.8: Commit**

```bash
git -C /c/Users/gethi/source/ralph add skills/pr-github/scripts/merge_pr.py skills/pr-github/SKILL.md tests/skills/test_pr_github.py
git -C /c/Users/gethi/source/ralph commit -m "feat(pr-github): add merge_pr sub-op with exit 4 for refused merges"
```

---

## Task 3: `_resolve_bool` + `auto_merge_clean_prs` config knob

**Files:**
- Modify: `ralph_executor/config.py`
- Modify: `ralph_executor/setup_cmds.py` (`CONFIG_TOML_STUB`)
- Modify: `tests/executor/test_config_toml.py`

- [ ] **Step 3.1: Add the env var to `clean_env` cleanup**

In `tests/executor/test_config_toml.py`, find the `clean_env` fixture (around line 23). Add `"RALPH_AUTO_MERGE_CLEAN_PRS"` to the tuple of env vars to delete.

- [ ] **Step 3.2: Add the failing config tests**

Append to `tests/executor/test_config_toml.py`:

```python
def test_auto_merge_clean_prs_defaults_false(clean_env: Path) -> None:
    """The new knob must default false so existing operators see no
    behaviour change after this PBI lands."""
    cfg = load_config()
    assert cfg.auto_merge_clean_prs is False


def test_auto_merge_clean_prs_from_toml_true(clean_env: Path) -> None:
    _write_toml(clean_env, "auto_merge_clean_prs = true\n")
    cfg = load_config()
    assert cfg.auto_merge_clean_prs is True


def test_auto_merge_clean_prs_env_wins_over_toml(
    clean_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_toml(clean_env, "auto_merge_clean_prs = false\n")
    monkeypatch.setenv("RALPH_AUTO_MERGE_CLEAN_PRS", "true")
    cfg = load_config()
    assert cfg.auto_merge_clean_prs is True


def test_auto_merge_clean_prs_env_false_overrides_toml_true(
    clean_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_toml(clean_env, "auto_merge_clean_prs = true\n")
    monkeypatch.setenv("RALPH_AUTO_MERGE_CLEAN_PRS", "false")
    cfg = load_config()
    assert cfg.auto_merge_clean_prs is False


def test_auto_merge_clean_prs_env_garbage_rejected(
    clean_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RALPH_AUTO_MERGE_CLEAN_PRS", "maybe")
    with pytest.raises(ConfigError, match="auto_merge_clean_prs"):
        load_config()


def test_auto_merge_clean_prs_toml_non_bool_rejected(clean_env: Path) -> None:
    _write_toml(clean_env, 'auto_merge_clean_prs = "yes"\n')
    with pytest.raises(ConfigError, match="auto_merge_clean_prs"):
        load_config()
```

- [ ] **Step 3.3: Run failing tests**

```bash
uv run pytest tests/executor/test_config_toml.py -k "auto_merge_clean_prs" -v
```

Expected: **6 FAIL** — `AttributeError: 'ExecutorConfig' object has no attribute 'auto_merge_clean_prs'`.

- [ ] **Step 3.4: Add `_resolve_bool` helper to `config.py`**

In `ralph_executor/config.py`, after the existing `_resolve_float` helper (around line 232), insert:

```python
def _resolve_bool(
    *, name: str, env_name: str, toml_value: Any, default: bool, source_label: str
) -> bool:
    """env > toml > default for a bool-valued knob.

    Env accepts "true"/"false"/"1"/"0" (case-insensitive). Anything else
    is a ConfigError. TOML must be a bona-fide bool; strings rejected.
    """
    raw_env = os.environ.get(env_name)
    if raw_env is not None and raw_env.strip() != "":
        s = raw_env.strip().lower()
        if s in ("true", "1"):
            return True
        if s in ("false", "0"):
            return False
        raise ConfigError(
            f"{env_name}={raw_env!r} is not a valid bool: expected true/false/1/0"
            f" (knob: {name})"
        )
    if toml_value is None:
        return default
    if not isinstance(toml_value, bool):
        raise ConfigError(
            f"{source_label}: {name} must be a bool, got {type(toml_value).__name__}"
        )
    return toml_value
```

- [ ] **Step 3.5: Add `auto_merge_clean_prs` to `_TOML_KNOWN_KEYS`**

In `ralph_executor/config.py`, find `_TOML_KNOWN_KEYS = frozenset({` (around line 59). Add `"auto_merge_clean_prs"` to the set (alphabetical / grouped placement at your discretion).

- [ ] **Step 3.6: Add the field to `ExecutorConfig`**

In `ralph_executor/config.py`, find `class ExecutorConfig:` (around line 87). Add at the end of the dataclass (after the last existing field, with default — defaults at end avoid the no-defaults-after-defaults dataclass rule):

```python
    # Sweep auto-merge clean PRs. Default False; opt-in only.
    auto_merge_clean_prs: bool = False
```

- [ ] **Step 3.7: Add the resolver call in `load_config`**

Find the end of the existing resolver calls in `load_config()` (just before `return ExecutorConfig(...)`). Add:

```python
    auto_merge_clean_prs = _resolve_bool(
        name="auto_merge_clean_prs",
        env_name="RALPH_AUTO_MERGE_CLEAN_PRS",
        toml_value=toml_overrides.get("auto_merge_clean_prs"),
        default=False,
        source_label=source_label,
    )
```

Pass it to the `ExecutorConfig(...)` constructor:

```python
    return ExecutorConfig(
        # ... existing kwargs ...
        auto_merge_clean_prs=auto_merge_clean_prs,
    )
```

- [ ] **Step 3.8: Update `CONFIG_TOML_STUB`**

In `ralph_executor/setup_cmds.py`, append to `CONFIG_TOML_STUB`:

```
#
# Auto-merge clean PRs. When true, sweep merges any PR whose GitHub
# mergeable_state is "clean" (CI green + required approvals met + no
# conflicts + branch up-to-date). Default false for safety. Env override:
# RALPH_AUTO_MERGE_CLEAN_PRS.
# auto_merge_clean_prs = false
```

- [ ] **Step 3.9: Run the tests — verify they pass**

```bash
uv run pytest tests/executor/test_config_toml.py -k "auto_merge_clean_prs" -v
```

Expected: **6 PASS**.

- [ ] **Step 3.10: Run ruff + mypy**

```bash
uv run ruff check ralph_executor/config.py ralph_executor/setup_cmds.py
uv run mypy --strict ralph_executor/config.py
```

- [ ] **Step 3.11: Commit**

```bash
git -C /c/Users/gethi/source/ralph add ralph_executor/config.py ralph_executor/setup_cmds.py tests/executor/test_config_toml.py
git -C /c/Users/gethi/source/ralph commit -m "feat(config): add _resolve_bool + auto_merge_clean_prs knob (default false)"
```

---

## Task 4: `PrSnapshot.merge_state` + `Action.MERGE_PR` + parse in `pr_state.py`

**Files:**
- Modify: `ralph_executor/sweep/types.py:50-67` (PrSnapshot) + line 70 (Action)
- Modify: `ralph_executor/sweep/pr_state.py:59-74` (PrSnapshot constructor)
- Modify: `tests/executor/sweep/test_pr_state.py`

- [ ] **Step 4.1: Add failing tests in test_pr_state.py**

Append to `tests/executor/sweep/test_pr_state.py`:

```python
def test_pr_state_parses_mergeable_state_from_show_payload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """PrSnapshot.merge_state must carry the raw GitHub value from show.py."""
    from ralph_executor.sweep import pr_state

    fake_scripts = tmp_path / "pr_scripts"
    fake_scripts.mkdir()
    # Create show.py + read_threads.py stubs that print the captured payload.
    (fake_scripts / "show.py").write_text(
        "import json, sys\n"
        "print(json.dumps({\n"
        "  'pr_id': 42, 'title': 't', 'status': 'active', 'ci_status': 'succeeded',\n"
        "  'source_branch': 'ralph/X', 'target_branch': 'main',\n"
        "  'reviewers': [], 'url': 'https://example/42',\n"
        "  'mergeable_state': 'clean',\n"
        "}))\n",
        encoding="utf-8",
    )
    (fake_scripts / "read_threads.py").write_text(
        "import json\nprint(json.dumps([]))\n",
        encoding="utf-8",
    )

    snapshot = pr_state.fetch(pr_id=42, skill_scripts_path=fake_scripts)
    assert snapshot.merge_state == "clean"


def test_pr_state_defaults_merge_state_to_unknown_when_absent(
    tmp_path: Path,
) -> None:
    """Defensive default: show.py from an older deploy may not emit the
    field. PrSnapshot.merge_state must be 'unknown' in that case."""
    from ralph_executor.sweep import pr_state

    fake_scripts = tmp_path / "pr_scripts"
    fake_scripts.mkdir()
    (fake_scripts / "show.py").write_text(
        "import json\n"
        "print(json.dumps({\n"
        "  'pr_id': 42, 'title': 't', 'status': 'active', 'ci_status': 'succeeded',\n"
        "  'source_branch': 'r', 'target_branch': 'main', 'reviewers': [], 'url': '',\n"
        "}))\n",  # NO mergeable_state field
        encoding="utf-8",
    )
    (fake_scripts / "read_threads.py").write_text(
        "import json\nprint(json.dumps([]))\n",
        encoding="utf-8",
    )

    snapshot = pr_state.fetch(pr_id=42, skill_scripts_path=fake_scripts)
    assert snapshot.merge_state == "unknown"
```

(Note: on Windows, the test runner may need a shebang on the stub files; check existing `test_pr_state.py` for the helper that wraps subprocess invocation. If the existing tests use a `.cmd` wrapper or similar, mirror that pattern.)

- [ ] **Step 4.2: Run failing tests**

```bash
uv run pytest tests/executor/sweep/test_pr_state.py -k "mergeable_state or merge_state" -v
```

Expected: FAIL — either `TypeError: __init__() got an unexpected keyword argument 'merge_state'` or `AssertionError`.

- [ ] **Step 4.3: Add `Action.MERGE_PR` to `sweep/types.py`**

In `ralph_executor/sweep/types.py`, find `class Action(StrEnum):` (line 70). Append:

```python
class Action(StrEnum):
    MOVE_TO_DONE = "move-to-done"
    MOVE_TO_BLOCKED_ABANDONED = "move-to-blocked-abandoned"
    MOVE_TO_INBOX_RETRY = "move-to-inbox-retry"
    MOVE_TO_BLOCKED_MAX_ATTEMPTS = "move-to-blocked-max-attempts"
    CREATE_FEEDBACK_PBI = "create-feedback-pbi"
    PING_REVIEWER = "ping-reviewer"
    NOOP = "noop"
    MERGE_PR = "merge-pr"
```

- [ ] **Step 4.4: Add `merge_state` field to `PrSnapshot`**

In the same file (`sweep/types.py`), find `class PrSnapshot:` (line 50). Append the new field at the end of the dataclass (the field has a default to keep existing positional constructions working):

```python
@dataclass(frozen=True)
class PrSnapshot:
    pr_id: int
    title: str
    pr_status: PrStatus
    ci_status: CiStatus
    source_branch: str
    target_branch: str
    reviewers: tuple[str, ...]
    threads: tuple[ThreadSnapshot, ...]
    last_activity_at: datetime
    url: str
    # Raw GitHub mergeable_state (clean / unstable / behind / blocked /
    # dirty / has_hooks / draft / unknown). "unknown" when show.py
    # didn't emit the field (pre-rollout defensive default).
    merge_state: str = "unknown"
```

- [ ] **Step 4.5: Update `pr_state.py:fetch` to parse `mergeable_state`**

In `ralph_executor/sweep/pr_state.py`, find the `return PrSnapshot(` block (around line 59). Add `merge_state=` to the kwargs:

```python
    return PrSnapshot(
        pr_id=int(show_dict.get("pr_id") or pr_id),
        title=str(show_dict.get("title", "")),
        pr_status=_coerce_pr_status(show_dict.get("status")),
        ci_status=_coerce_ci_status(show_dict.get("ci_status")),
        source_branch=str(show_dict.get("source_branch", "")),
        target_branch=str(show_dict.get("target_branch", "main")),
        reviewers=tuple(str(r) for r in (show_dict.get("reviewers") or ()) if isinstance(r, str)),
        threads=threads,
        last_activity_at=last_activity_at,
        url=str(show_dict.get("url", "")),
        merge_state=str(show_dict.get("mergeable_state") or "unknown"),
    )
```

- [ ] **Step 4.6: Run the tests**

```bash
uv run pytest tests/executor/sweep/test_pr_state.py -k "mergeable_state or merge_state" -v
```

Expected: **2 PASS**.

- [ ] **Step 4.7: Run ruff + mypy on the touched files**

```bash
uv run ruff check ralph_executor/sweep/types.py ralph_executor/sweep/pr_state.py
uv run mypy --strict ralph_executor/sweep/types.py ralph_executor/sweep/pr_state.py
```

- [ ] **Step 4.8: Commit**

```bash
git -C /c/Users/gethi/source/ralph add ralph_executor/sweep/types.py ralph_executor/sweep/pr_state.py tests/executor/sweep/test_pr_state.py
git -C /c/Users/gethi/source/ralph commit -m "feat(sweep): PrSnapshot.merge_state + Action.MERGE_PR enum"
```

---

## Task 5: `SweepConfig.auto_merge_clean_prs` + loop plumbing

**Files:**
- Modify: `ralph_executor/sweep/runner.py:33-55` (SweepConfig dataclass)
- Modify: `ralph_executor/loop.py` (`_run_sweep`)
- Modify: `tests/executor/test_loop.py` or wherever `_run_sweep` is tested

- [ ] **Step 5.1: Add field to `SweepConfig`**

In `ralph_executor/sweep/runner.py`, find `class SweepConfig:` (line 33). Add the new field at the end (defaults at end keep existing positional constructions working):

```python
@dataclass(frozen=True)
class SweepConfig:
    """All non-default parameters the sweep needs.
    ...
    """

    ralph_author_email: str
    max_attempts: int
    stale_threshold: timedelta
    now: datetime
    # When True, decide_action returns MERGE_PR for any open PR whose
    # merge_state == "clean". Default False; opt-in only.
    auto_merge_clean_prs: bool = False
```

(The existing `__post_init__` validation does not need changes — bool fields don't trigger any existing rule.)

- [ ] **Step 5.2: Plumb `cfg.auto_merge_clean_prs` from `loop._run_sweep`**

In `ralph_executor/loop.py`, find the `SweepConfig(...)` construction in `_run_sweep`. After this PBI's predecessor (`CONFIG-PROMOTE-SWEEP-KNOBS`) lands, the construction looks roughly like:

```python
    sweep_cfg = SweepConfig(
        ralph_author_email=cfg.bot_author_email,
        max_attempts=cfg.max_attempts,
        stale_threshold=timedelta(days=cfg.stale_days),
        now=datetime.now(tz=UTC),
    )
```

Add the new kwarg:

```python
    sweep_cfg = SweepConfig(
        ralph_author_email=cfg.bot_author_email,
        max_attempts=cfg.max_attempts,
        stale_threshold=timedelta(days=cfg.stale_days),
        now=datetime.now(tz=UTC),
        auto_merge_clean_prs=cfg.auto_merge_clean_prs,
    )
```

If `CONFIG-PROMOTE-SWEEP-KNOBS` hasn't landed yet (this plan assumes depends_on chain holds: that PBI ships first), the existing kwargs are `RALPH_ADO_AUTHOR_EMAIL` + `RALPH_STALE_DAYS` env reads. The new kwarg `auto_merge_clean_prs=cfg.auto_merge_clean_prs` slots in identically.

- [ ] **Step 5.3: Add a regression test that the value flows through**

Append to `tests/executor/test_loop.py`:

```python
def test_run_sweep_passes_auto_merge_flag_to_sweep_config(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When cfg.auto_merge_clean_prs is True it must reach SweepConfig."""
    from dataclasses import replace
    from ralph_executor.queue.filesystem import FilesystemQueueSource
    from ralph_executor.loop import _run_sweep

    captured: dict[str, object] = {}

    class _SpySweepConfig:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(
        "ralph_executor.sweep.runner.SweepConfig",
        _SpySweepConfig,
    )
    monkeypatch.setattr("ralph_executor.sweep.run", lambda ctx: None)
    monkeypatch.setattr(
        "ralph_executor.loop._pr_skill_scripts_path",
        lambda cfg: fake_repo,
    )

    cfg = replace(cfg_for_repo, bot_author_email="r@x.test", auto_merge_clean_prs=True)
    _run_sweep(cfg, FilesystemQueueSource(cfg))

    assert captured.get("auto_merge_clean_prs") is True
```

- [ ] **Step 5.4: Run tests + mypy + ruff**

```bash
uv run pytest tests/executor/test_loop.py -k "auto_merge" -v
uv run ruff check ralph_executor/loop.py ralph_executor/sweep/runner.py
uv run mypy --strict ralph_executor/loop.py ralph_executor/sweep/runner.py
```

Expected: PASS + green.

- [ ] **Step 5.5: Commit**

```bash
git -C /c/Users/gethi/source/ralph add ralph_executor/sweep/runner.py ralph_executor/loop.py tests/executor/test_loop.py
git -C /c/Users/gethi/source/ralph commit -m "feat(sweep): SweepConfig.auto_merge_clean_prs + loop plumbing"
```

---

## Task 6: `decide_action` MERGE_PR branch + act path subprocess

**Files:**
- Modify: `ralph_executor/sweep/runner.py` (`decide_action` around line 111-157, act around 302-321)
- Modify: `tests/executor/sweep/test_runner.py`

- [ ] **Step 6.1: Add failing tests for `decide_action`**

Append to `tests/executor/sweep/test_runner.py`:

```python
def _build_pr_snapshot(merge_state: str = "unknown") -> "PrSnapshot":
    """Helper: build a minimal PrSnapshot for decide_action tests."""
    from datetime import UTC, datetime
    from ralph_executor.sweep.types import PrSnapshot

    return PrSnapshot(
        pr_id=42,
        title="t",
        pr_status="active",
        ci_status="succeeded",
        source_branch="ralph/X",
        target_branch="main",
        reviewers=(),
        threads=(),
        last_activity_at=datetime.now(tz=UTC),
        url="https://example/42",
        merge_state=merge_state,
    )


def test_decide_action_merge_pr_when_flag_on_and_clean() -> None:
    from datetime import UTC, datetime, timedelta
    from ralph_executor.sweep.runner import SweepConfig, decide_action
    from ralph_executor.sweep.types import Action

    config = SweepConfig(
        ralph_author_email="r@x",
        max_attempts=20,
        stale_threshold=timedelta(days=3),
        now=datetime.now(tz=UTC),
        auto_merge_clean_prs=True,
    )
    pr = _build_pr_snapshot(merge_state="clean")
    decision = decide_action(pr=pr, attempts=0, last_seen_comment_ids=set(), config=config)
    assert decision.action == Action.MERGE_PR


def test_decide_action_no_merge_when_flag_off() -> None:
    from datetime import UTC, datetime, timedelta
    from ralph_executor.sweep.runner import SweepConfig, decide_action
    from ralph_executor.sweep.types import Action

    config = SweepConfig(
        ralph_author_email="r@x",
        max_attempts=20,
        stale_threshold=timedelta(days=3),
        now=datetime.now(tz=UTC),
        auto_merge_clean_prs=False,
    )
    pr = _build_pr_snapshot(merge_state="clean")
    decision = decide_action(pr=pr, attempts=0, last_seen_comment_ids=set(), config=config)
    assert decision.action != Action.MERGE_PR


def test_decide_action_no_merge_when_state_not_clean() -> None:
    from datetime import UTC, datetime, timedelta
    from ralph_executor.sweep.runner import SweepConfig, decide_action
    from ralph_executor.sweep.types import Action

    config = SweepConfig(
        ralph_author_email="r@x",
        max_attempts=20,
        stale_threshold=timedelta(days=3),
        now=datetime.now(tz=UTC),
        auto_merge_clean_prs=True,
    )
    for state in ("blocked", "behind", "dirty", "unstable", "unknown"):
        pr = _build_pr_snapshot(merge_state=state)
        decision = decide_action(
            pr=pr, attempts=0, last_seen_comment_ids=set(), config=config
        )
        assert decision.action != Action.MERGE_PR, f"state={state} wrongly triggered MERGE_PR"


def test_decide_action_completed_pr_still_moves_to_done_not_merge() -> None:
    """If GitHub already says completed (PR merged externally), MOVE_TO_DONE
    takes precedence over MERGE_PR even when both predicates would fire."""
    from datetime import UTC, datetime, timedelta
    from ralph_executor.sweep.runner import SweepConfig, decide_action
    from ralph_executor.sweep.types import Action, PrSnapshot

    config = SweepConfig(
        ralph_author_email="r@x",
        max_attempts=20,
        stale_threshold=timedelta(days=3),
        now=datetime.now(tz=UTC),
        auto_merge_clean_prs=True,
    )
    pr = PrSnapshot(
        pr_id=42, title="t", pr_status="completed", ci_status="succeeded",
        source_branch="ralph/X", target_branch="main", reviewers=(), threads=(),
        last_activity_at=datetime.now(tz=UTC), url="", merge_state="clean",
    )
    decision = decide_action(
        pr=pr, attempts=0, last_seen_comment_ids=set(), config=config
    )
    assert decision.action == Action.MOVE_TO_DONE
```

- [ ] **Step 6.2: Implement the `decide_action` branch**

In `ralph_executor/sweep/runner.py`, find `def decide_action(`. Locate the "if pr.pr_status == 'completed'" branch (around line 111). Insert the new MERGE_PR branch immediately AFTER it (so completed-external still wins):

```python
    if pr.pr_status == "completed":
        return Decision(action=Action.MOVE_TO_DONE, reason="PR merged (completed)")

    # Auto-merge: when explicitly enabled and GitHub itself reports clean,
    # initiate the merge subprocess. Race-safe: merge_pr handles 405/409.
    if config.auto_merge_clean_prs and pr.merge_state == "clean":
        return Decision(action=Action.MERGE_PR, reason="PR clean; auto-merging")

    # ... existing branches (CI red, feedback, stale, etc.) ...
```

- [ ] **Step 6.3: Run decide_action tests — verify they pass**

```bash
uv run pytest tests/executor/sweep/test_runner.py -k "decide_action_merge_pr or decide_action_no_merge or decide_action_completed_pr_still" -v
```

Expected: **4 PASS**.

- [ ] **Step 6.4: Add failing tests for the act path**

Append to `tests/executor/sweep/test_runner.py`:

```python
def test_act_merge_pr_success_moves_to_done(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When merge_pr.py returns exit 0, the PBI moves to done/ with
    the audit string 'PR auto-merged by sweep'."""
    import json
    import subprocess
    from datetime import UTC, datetime, timedelta
    from ralph_executor.sweep import runner as runner_mod
    from ralph_executor.sweep.runner import SweepConfig, SweepContext
    from ralph_executor.sweep.types import Action, Decision

    queue = tmp_path / ".ralph"
    for sub in ("pending-pr", "done", "blocked", "inbox", "current"):
        (queue / sub).mkdir(parents=True)
    pbi = queue / "pending-pr" / "PBI-1"
    pbi.mkdir()
    (pbi / "PBI.md").write_text("---\nid: PBI-1\n---\n", encoding="utf-8")
    (pbi / "HISTORY.md").write_text("", encoding="utf-8")
    (pbi / "PR-LINK.md").write_text("PR ID 42\n", encoding="utf-8")

    scripts_path = tmp_path / "pr_scripts"
    scripts_path.mkdir()

    def _fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=argv,
            returncode=0,
            stdout=json.dumps({
                "merged": True, "sha": "abc", "pr_id": 42, "branch_deleted": True,
            }),
            stderr="",
        )

    monkeypatch.setattr("ralph_executor.sweep.runner.subprocess.run", _fake_run)

    ctx = SweepContext(
        queue_root=queue,
        ado_pr_scripts_path=scripts_path,
        config=SweepConfig(
            ralph_author_email="r@x",
            max_attempts=20,
            stale_threshold=timedelta(days=3),
            now=datetime.now(tz=UTC),
            auto_merge_clean_prs=True,
        ),
    )
    decision = Decision(action=Action.MERGE_PR, reason="PR clean; auto-merging")

    # Build the minimal sidecar + snapshot required by act() — copy the
    # pattern from existing act tests in this file.
    snapshot = _build_pr_snapshot(merge_state="clean")
    from ralph_executor.sweep.state import SweepSidecar
    sidecar = SweepSidecar(last_seen_comment_ids=frozenset())

    runner_mod._act(
        pbi_dir=pbi,
        decision=decision,
        snapshot=snapshot,
        sidecar=sidecar,
        ctx=ctx,
    )

    assert (queue / "done" / "PBI-1").is_dir()
    assert not pbi.exists()
    # HISTORY.md inside the moved dir should mention the audit string
    history = (queue / "done" / "PBI-1" / "HISTORY.md").read_text(encoding="utf-8")
    assert "auto-merged by sweep" in history


def test_act_merge_pr_exit_4_stays_in_pending(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Race: exit 4 means GitHub refused. PBI stays in pending-pr/."""
    import json
    import subprocess
    from datetime import UTC, datetime, timedelta
    from ralph_executor.sweep import runner as runner_mod
    from ralph_executor.sweep.runner import SweepConfig, SweepContext
    from ralph_executor.sweep.state import SweepSidecar
    from ralph_executor.sweep.types import Action, Decision

    queue = tmp_path / ".ralph"
    for sub in ("pending-pr", "done"):
        (queue / sub).mkdir(parents=True)
    pbi = queue / "pending-pr" / "PBI-1"
    pbi.mkdir()
    (pbi / "PBI.md").write_text("---\nid: PBI-1\n---\n", encoding="utf-8")
    (pbi / "HISTORY.md").write_text("", encoding="utf-8")
    (pbi / "PR-LINK.md").write_text("PR ID 42\n", encoding="utf-8")

    def _fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=argv,
            returncode=4,
            stdout=json.dumps({"merged": False, "reason": "stale state", "pr_id": 42}),
            stderr="",
        )

    monkeypatch.setattr("ralph_executor.sweep.runner.subprocess.run", _fake_run)

    ctx = SweepContext(
        queue_root=queue,
        ado_pr_scripts_path=tmp_path / "pr_scripts",
        config=SweepConfig(
            ralph_author_email="r@x", max_attempts=20,
            stale_threshold=timedelta(days=3), now=datetime.now(tz=UTC),
            auto_merge_clean_prs=True,
        ),
    )
    (tmp_path / "pr_scripts").mkdir()

    runner_mod._act(
        pbi_dir=pbi,
        decision=Decision(action=Action.MERGE_PR, reason="x"),
        snapshot=_build_pr_snapshot(merge_state="clean"),
        sidecar=SweepSidecar(last_seen_comment_ids=frozenset()),
        ctx=ctx,
    )

    assert pbi.is_dir(), "exit 4 must leave PBI in pending-pr/"
    assert not (queue / "done" / "PBI-1").exists()
```

(Note: act function name in current code is `_act` based on the file structure I've read. If the actual symbol is different, run `grep -n 'def _act\|def act' ralph_executor/sweep/runner.py` and adjust the patch path.)

- [ ] **Step 6.5: Implement the act path**

In `ralph_executor/sweep/runner.py`, find the act dispatch (around line 302). After the existing `if a is Action.MOVE_TO_DONE` branch, add (before the `MOVE_TO_BLOCKED_*` branch):

```python
    if a is Action.MOVE_TO_DONE:
        _move_with_history(pbi_dir, qr / "done" / pbi_dir.name, decision.reason, ctx)
    elif a is Action.MERGE_PR:
        _do_merge_pr(pbi_dir=pbi_dir, snapshot=snapshot, ctx=ctx)
    elif a in (Action.MOVE_TO_BLOCKED_ABANDONED, Action.MOVE_TO_BLOCKED_MAX_ATTEMPTS):
        _move_with_history(pbi_dir, qr / "blocked" / pbi_dir.name, decision.reason, ctx)
    # ... rest unchanged ...
```

Then add the `_do_merge_pr` helper at the bottom of `runner.py` (above `_move_with_history`):

```python
def _do_merge_pr(
    *,
    pbi_dir: Path,
    snapshot: PrSnapshot,
    ctx: SweepContext,
) -> None:
    """Invoke pr/merge_pr.py for the PBI's PR. On success move to done/.

    Exit 0  -> PBI moves to done/ with HISTORY.md "PR auto-merged by sweep"
    Exit 4  -> PBI stays (race: state changed); log INFO; retry next iter
    Exit 3  -> PBI stays; log WARNING; retry next iter
    Exit 2  -> raise _SweepPbiError (programming bug in skill or env)
    """
    import json as _json
    import subprocess as _sp
    import sys as _sys

    script = ctx.ado_pr_scripts_path / "merge_pr.py"
    proc = _sp.run(
        [
            _sys.executable,
            str(script),
            "--pr-id", str(snapshot.pr_id),
            "--repo", ctx.queue_root.parent.name,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode == 0:
        try:
            payload = _json.loads(proc.stdout)
        except _json.JSONDecodeError as exc:
            raise _SweepPbiError(
                f"merge_pr returned malformed JSON: {exc} (stdout={proc.stdout!r})"
            ) from exc
        if not payload.get("merged"):
            raise _SweepPbiError(
                f"merge_pr exit 0 but payload says merged=False: {payload}"
            )
        _move_with_history(
            pbi_dir,
            ctx.queue_root / "done" / pbi_dir.name,
            "PR auto-merged by sweep",
            ctx,
        )
        log.info("sweep: auto-merged %s (PR #%d)", pbi_dir.name, snapshot.pr_id)
        return
    if proc.returncode == 4:
        try:
            payload = _json.loads(proc.stdout)
            reason = str(payload.get("reason", "(no reason)"))
        except _json.JSONDecodeError:
            reason = proc.stdout[:200] or "(empty stdout)"
        log.info(
            "sweep: merge_pr refused for %s (PR #%d): %s",
            pbi_dir.name, snapshot.pr_id, reason,
        )
        return
    if proc.returncode == 3:
        log.warning(
            "sweep: merge_pr API error for %s (PR #%d): %s",
            pbi_dir.name, snapshot.pr_id, proc.stderr.strip(),
        )
        return
    # Exit 2 (or anything else): programming bug / auth missing.
    raise _SweepPbiError(
        f"merge_pr exit {proc.returncode} for {pbi_dir.name}: {proc.stderr.strip()}"
    )
```

- [ ] **Step 6.6: Run the act tests**

```bash
uv run pytest tests/executor/sweep/test_runner.py -k "act_merge_pr" -v
```

Expected: **2 PASS**.

- [ ] **Step 6.7: Run ruff + mypy on runner.py**

```bash
uv run ruff check ralph_executor/sweep/runner.py
uv run mypy --strict ralph_executor/sweep/runner.py
```

- [ ] **Step 6.8: Commit**

```bash
git -C /c/Users/gethi/source/ralph add ralph_executor/sweep/runner.py tests/executor/sweep/test_runner.py
git -C /c/Users/gethi/source/ralph commit -m "feat(sweep): decide_action MERGE_PR branch + _do_merge_pr act path"
```

---

## Task 7: Full-suite green + smoke

**Files:** none modified directly; verification + cleanup.

- [ ] **Step 7.1: Sweep tests for PrSnapshot / SweepConfig literal constructions**

```bash
grep -rn "PrSnapshot(" /c/Users/gethi/source/ralph/tests/ 2>&1 | head
grep -rn "SweepConfig(" /c/Users/gethi/source/ralph/tests/ 2>&1 | head
```

For every match: if the test passes positional args, the new `merge_state` (PrSnapshot) and `auto_merge_clean_prs` (SweepConfig) fields have defaults so it should still compile. If the test passes keyword args and asserts on the full set, add the new kwargs explicitly (`merge_state="unknown"` and `auto_merge_clean_prs=False`).

- [ ] **Step 7.2: Run full pytest**

```bash
uv run pytest tests/ -v
```

Expected: all green. Common failure modes:

- A test that asserts on the FULL output dict of `show.py` (Task 1's new `mergeable_state` field): update the expected dict.
- A test that mocks `subprocess.run` for sweep and didn't expect merge_pr being called: not possible unless `cfg.auto_merge_clean_prs` is True; that flag defaults False, so existing tests shouldn't trigger.

- [ ] **Step 7.3: Run ruff + mypy across the whole package**

```bash
uv run ruff check ralph_executor/ skills/ tests/
uv run ruff format --check ralph_executor/ skills/ tests/
uv run mypy --strict ralph_executor/
```

Expected: all green.

- [ ] **Step 7.4: Manual smoke (optional, requires GH_TOKEN with merge permission)**

In a scratch repo or against the actual ralph-queue:

```bash
# Pin the flag on for one iteration:
$env:RALPH_AUTO_MERGE_CLEAN_PRS = "true"
uv run ralph-executor --once
# Then unset
$env:RALPH_AUTO_MERGE_CLEAN_PRS = "false"
```

Verify a known-clean PR gets merged + branch deleted. Run again with the flag off and confirm no merges happen.

If you want to dry-run without merging, set `auto_merge_clean_prs=true` and add `--once` then immediately inspect logs — the merge subprocess will fire on any clean PR. For real safety, keep the flag off until you're confident.

- [ ] **Step 7.5: Commit the moved-orphan state (if smoke moved anything)**

```bash
git -C /c/Users/gethi/source/ralph status
# If sweep moved any PBI from pending-pr/ to done/, commit those moves:
git -C /c/Users/gethi/source/ralph add .ralph/
git -C /c/Users/gethi/source/ralph commit -m "chore(queue): smoke-test auto-merge flag (merged N clean PRs)"
```

---

## Acceptance (matches spec)

- [x] `skills/pr-github/scripts/show.py` emits raw `mergeable_state` field — Task 1
- [x] `skills/pr-github/scripts/merge_pr.py` exists with contract from spec, exit 0/2/3/4 — Task 2
- [x] `SKILL.md` documents `merge_pr` operation — Task 2.5
- [x] `ExecutorConfig.auto_merge_clean_prs: bool = False`, layered defaults < TOML < env — Task 3
- [x] `_resolve_bool` helper added — Task 3.4
- [x] `PrSnapshot.merge_state: str` carries GitHub's raw value — Task 4
- [x] `Action.MERGE_PR` enum value — Task 4
- [x] `pr_state.py` parses `mergeable_state` with `"unknown"` default — Task 4
- [x] `SweepConfig.auto_merge_clean_prs: bool = False` — Task 5
- [x] `loop._run_sweep` passes `cfg.auto_merge_clean_prs` to SweepConfig — Task 5
- [x] `decide_action` returns MERGE_PR only when flag on AND merge_state=="clean" — Task 6
- [x] `_do_merge_pr` invokes merge_pr.py; exit 0 moves to done/ with "PR auto-merged by sweep" — Task 6
- [x] `CONFIG_TOML_STUB` documents the key — Task 3.8
- [x] pytest / ruff / mypy strict all green — Task 7

## Out of scope

- ADO `merge_pr.py` (deferred — same tracker as ADO `lookup_by_branch`)
- Merge methods other than the GitHub three (squash/merge/rebase)
- Auto-merge for `unstable` / `has_hooks` states
- `--force` / `--admin` bypass paths
- HISTORY.md format changes beyond the new reason string

## Operational notes

- **Branch:** lands on `ralph-queue` via PR to `main`.
- **depends_on:** `SWEEP-RECONCILE-ORPHANS` (both modify `SKILL.md`).
- **Ralph running?** Task 5 touches `loop.py`; Task 6 touches sweep act path. If ralph is running against this checkout, halt it before Task 5 and resume after Task 7.
- **Default-false:** existing operators upgrading to this version see zero behavior change unless they explicitly set the flag.
