"""End-to-end smoke test: spawn ``claude -p`` against a sample PBI.

This test is **opt-in**. It is skipped unless ``RALPH_PROMPT_SMOKE=1`` is
set in the environment. It exists for engineers iterating on the
``prompt/`` topic tree to verify that Ralph's actual behaviour matches
the assembled standing-prompt's intent.

Prerequisites:

- ``claude`` CLI on ``$PATH`` (Claude Code installed and configured).
- ``ANTHROPIC_API_KEY`` set in the environment.
- Network access to the Anthropic API.
- The test creates a fresh tmp git workspace per run; nothing in the host
  repo is mutated.

Marked ``slow``. If the subprocess invocation proves flaky on a given
host (Windows in particular has had cases where ``claude -p`` blocks on
a permission prompt despite ``--dangerously-skip-permissions``), defer
this test in CI and rely on manual smoke instead -- the structural test
in ``tests/test_prompt_structure.py`` is the load-bearing automated
check.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPT_ROOT = REPO_ROOT / "prompt"
SAMPLE_FEATURE = REPO_ROOT / "samples" / "feature-WI-1234"

SMOKE_ENV_FLAG = "RALPH_PROMPT_SMOKE"
CLAUDE_TIMEOUT_SECONDS = 300  # 5 min upper bound per smoke run


def _claude_available() -> bool:
    return shutil.which("claude") is not None


pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        os.environ.get(SMOKE_ENV_FLAG) != "1",
        reason=(
            f"prompt smoke is opt-in; set {SMOKE_ENV_FLAG}=1 to run "
            "(spawns claude -p as a subprocess)"
        ),
    ),
    pytest.mark.skipif(
        not _claude_available(),
        reason="`claude` CLI not found on $PATH",
    ),
    pytest.mark.skipif(
        not os.environ.get("ANTHROPIC_API_KEY"),
        reason="ANTHROPIC_API_KEY not set",
    ),
]


@pytest.fixture
def tmp_ralph_workspace(tmp_path: Path) -> Path:
    """Build a minimal git workspace with .ralph/current/<PBI-id>/ wired up.

    Returns the workspace root. The workspace contains the sample feature
    PBI copied into ``.ralph/current/WI-1234/`` and the per-topic
    ``prompt/`` tree copied verbatim so the composer can assemble the
    standing prompt at spawn time exactly as production does.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    # Initialise as a git repo so anything Ralph commits has a target.
    subprocess.run(
        ["git", "init", "--quiet", "--initial-branch=main"],
        cwd=workspace,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "ralph-smoke@example.com"],
        cwd=workspace,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Ralph Smoke"],
        cwd=workspace,
        check=True,
    )

    # Copy the prompt/ topic tree into the workspace at the canonical path.
    shutil.copytree(PROMPT_ROOT, workspace / "prompt")

    # Copy the sample feature PBI into .ralph/current/WI-1234/.
    target_pbi = workspace / ".ralph" / "current" / "WI-1234"
    target_pbi.mkdir(parents=True)
    for child in SAMPLE_FEATURE.iterdir():
        if child.is_file():
            shutil.copy2(child, target_pbi / child.name)

    # Commit the initial state so Ralph has a clean baseline.
    subprocess.run(["git", "add", "-A"], cwd=workspace, check=True)
    subprocess.run(
        ["git", "commit", "--quiet", "-m", "smoke: initial workspace"],
        cwd=workspace,
        check=True,
    )

    # Pretend the executor has already created the feature branch.
    subprocess.run(
        ["git", "checkout", "--quiet", "-b", "ralph/WI-1234"],
        cwd=workspace,
        check=True,
    )

    return workspace


def _run_claude(workspace: Path) -> subprocess.CompletedProcess[str]:
    """Spawn ``claude -p`` against the workspace mirroring production.

    Composes the standing prompt for ``feature`` (the sample PBI's type)
    via ``compose_prompt``, writes it to
    ``<workspace>/.ralph/state/standing-prompt-WI-1234.md``, and points
    ``-p`` at that file. This mirrors ``claude_spawn._build_argv`` so the
    smoke exercises the same injection mechanism production uses (a file
    on disk, not an inline ``--append-system-prompt`` argument).
    """
    from ralph_executor.prompt_composer import compose_prompt

    prompt_root = workspace / "prompt"
    # Smoke uses the feature default -- sample is a feature PBI.
    standing = compose_prompt(prompt_root, "feature")

    state_dir = workspace / ".ralph" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    standing_path = state_dir / "standing-prompt-WI-1234.md"
    standing_path.write_text(standing, encoding="utf-8")

    user_message = (
        f"Read {standing_path} for your standing instructions. Then "
        "begin iteration on the PBI in .ralph/current/WI-1234, advance "
        "it by exactly one step, and exit."
    )

    return subprocess.run(
        [
            "claude",
            "-p",
            user_message,
            "--output-format",
            "json",
            "--dangerously-skip-permissions",
        ],
        cwd=workspace,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=CLAUDE_TIMEOUT_SECONDS,
        check=False,
    )


def test_prompt_smoke_feature_iteration(tmp_ralph_workspace: Path) -> None:
    """Ralph reads the sample feature PBI and advances it by one step.

    Assertions are deliberately loose -- Claude's exact wording varies
    between runs. We assert on observable side effects and high-signal
    content, not on prose.
    """
    workspace = tmp_ralph_workspace

    # Capture HISTORY.md content BEFORE Ralph runs. The fixture sample
    # already contains a non-empty placeholder comment, so a bare
    # truthiness check on the post-iteration content would silently pass
    # even if Ralph appended nothing. Compare against this baseline.
    history_path = workspace / ".ralph" / "current" / "WI-1234" / "HISTORY.md"
    history_before = history_path.read_text(encoding="utf-8")

    try:
        result = _run_claude(workspace)
    except subprocess.TimeoutExpired as exc:
        pytest.fail(
            f"claude -p exceeded {CLAUDE_TIMEOUT_SECONDS}s timeout. "
            f"Partial stdout: {exc.stdout!r}\n"
            f"Partial stderr: {exc.stderr!r}"
        )

    # 1. Subprocess exits cleanly.
    assert result.returncode == 0, (
        f"claude -p exited with {result.returncode}.\n"
        f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )

    # 2. JSON output is parseable.
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(f"claude -p stdout was not valid JSON: {exc}\nstdout:\n{result.stdout}")

    # 3. The session produced a non-empty assistant response.
    assert payload, "claude -p returned an empty JSON payload"

    # 4. The PBI files Ralph SHOULD have touched were touched.
    plan = workspace / ".ralph" / "current" / "WI-1234" / "PLAN.md"
    assert history_path.is_file(), "HISTORY.md should still exist"
    history_after = history_path.read_text(encoding="utf-8")
    assert history_after != history_before, (
        "HISTORY.md was not modified during the iteration "
        "(content identical to the pre-iteration baseline)"
    )
    assert len(history_after) > len(history_before), (
        "HISTORY.md shrunk during the iteration "
        f"(was {len(history_before)} bytes, now {len(history_after)} bytes)"
    )

    # 5. At least one PLAN.md checkbox should be marked done OR a
    # new test file should exist (depending on which step Ralph picked
    # up -- both are valid first steps).
    assert plan.is_file(), "PLAN.md was deleted or moved during the iteration"
    plan_text = plan.read_text(encoding="utf-8")
    step_one_done = "- [x] 1." in plan_text or "- [X] 1." in plan_text
    test_file_created = (workspace / "tests" / "test_health.py").is_file()
    assert step_one_done or test_file_created, (
        "Expected either PLAN.md step 1 checked off OR "
        "tests/test_health.py created -- neither happened.\n"
        f"PLAN.md:\n{plan_text}"
    )

    # 6. Ralph should NOT have written STUCK.md on the happy path.
    stuck = workspace / ".ralph" / "current" / "WI-1234" / "STUCK.md"
    assert not stuck.exists(), (
        "Ralph wrote STUCK.md on a happy-path sample -- the prompt is "
        "likely too defensive. STUCK.md contents:\n" + stuck.read_text(encoding="utf-8")
    )


def test_prompt_smoke_respects_one_step_discipline(
    tmp_ralph_workspace: Path,
) -> None:
    """Ralph advances ONE step per iteration, not all five.

    Skipped if the previous smoke test created the same workspace
    artifacts -- this is a second assertion in fixture form rather than
    a duplicate run.
    """
    workspace = tmp_ralph_workspace
    try:
        result = _run_claude(workspace)
    except subprocess.TimeoutExpired as exc:
        pytest.fail(
            f"claude -p exceeded {CLAUDE_TIMEOUT_SECONDS}s timeout. "
            f"Partial stdout: {exc.stdout!r}\n"
            f"Partial stderr: {exc.stderr!r}"
        )
    assert result.returncode == 0

    plan_path = workspace / ".ralph" / "current" / "WI-1234" / "PLAN.md"
    assert plan_path.is_file(), "PLAN.md was deleted or moved during the iteration"
    plan_text = plan_path.read_text(encoding="utf-8")

    done_count = plan_text.count("- [x]") + plan_text.count("- [X]")
    # Single-step discipline: at most one box should be checked off
    # per iteration. (Zero is also valid -- Ralph may have written
    # tests but not flipped the box yet.)
    assert done_count <= 1, (
        f"Ralph checked off {done_count} PLAN.md boxes in a single "
        "iteration. Single-step discipline is broken.\n"
        f"PLAN.md:\n{plan_text}"
    )
