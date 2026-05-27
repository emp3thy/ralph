# Safe PBI add (guard rail + docs)

**Date:** 2026-05-27
**Scope:** Add a guard rail to `skills/ralph-add/scripts/add.py` that refuses to run when ralph is detected to be using the same checkout. Document the canonical add-PBI workflow (separate clone or git worktree) so operators and Claude sessions never repeat the 2026-05-27 incident where a manual commit landed on ralph's active feature branch.
**Status:** Design — pending review.

## Background

On 2026-05-27, while ralph was running in continuous-loop mode against the ralph checkout, a Claude session committed a new PBI's plan doc directly via `git commit` on `ralph-queue`. Between the file write and the commit, ralph switched HEAD to a feature branch (`ralph/SWEEP-RECONCILE-ORPHANS`). The commit landed on the feature branch, was pushed by ralph as part of its work, and ended up co-mingled with PR #31's history. Recovery required a revert on the feature branch plus a cherry-pick to ralph-queue.

Root cause: single-checkout co-tenancy. Ralph and the operator (or Claude) share one working tree. Ralph switches branches mid-iteration. Any external `git commit` lands on whichever branch HEAD points at when the commit fires — not necessarily the branch the operator intended.

This is not an issue for production (ROSA) deployments because ralph runs on its own dedicated clone there. Operators on their own machines have their own clones. The incident happened only because a single Windows checkout was hosting both ralph and the operator workflow.

## Decision

Two-part fix: documentation + guard rail. No plumbing lib (over-scoped given the production shape).

### Documentation

A new runbook `docs/runbooks/safe-pbi-add.md` documents the canonical workflows (separate clone OR git worktree) and links from the bootstrap runbook. `CLAUDE.md` gains a rule binding future Claude sessions to use a worktree/clone, never the running checkout.

### Guard rail

`skills/ralph-add/scripts/add.py` gains a `_detect_ralph_running(repo)` check that runs before any side-effecting work. When triggered, add.py exits `2` with a clear refusal explaining the evidence and the remediation options. A `--force` flag bypasses the guard.

Detection heuristics (OR-combined):

1. **HEAD is a `ralph/*` branch** — ralph's convention for per-PBI feature branches. Strong signal of mid-iteration.
2. **`.ralph/current/` contains at least one PBI dir** — ralph claims one PBI at a time and writes its dir under `current/` during iteration. Catches the case where ralph is between commits but mid-PBI.

False-positive case: operator manually checks out `ralph/foo` for an unrelated reason. Escape hatch: `--force`.

False-negative case: ralph is between PBIs with empty `current/` and HEAD back on `ralph-queue` — short window, effectively indistinguishable from a clean checkout, so safe-by-accident.

## Architecture

**Files (delta):**

```
skills/ralph-add/scripts/add.py        MODIFY (add _detect_ralph_running + --force flag)
docs/runbooks/safe-pbi-add.md          NEW
docs/bootstrap-operator-runbook.md     MODIFY (callout linking to new runbook)
CLAUDE.md                              MODIFY (add "Adding PBIs to ralph" section)

tests/skills/test_ralph_add.py         MODIFY (4 new test cases)
```

### Guard rail code

In `skills/ralph-add/scripts/add.py`, add the helper:

```python
def _detect_ralph_running(repo: Path) -> tuple[bool, str]:
    """Return (is_running, evidence) — True if ralph appears to be using
    this checkout right now. Evidence string is operator-readable."""
    try:
        head = _run_git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
    except subprocess.CalledProcessError:
        head = ""
    if head.startswith("ralph/"):
        return True, f"HEAD is on feature branch {head!r}"

    current_dir = repo / ".ralph" / "current"
    if current_dir.is_dir():
        for entry in current_dir.iterdir():
            if entry.is_dir():
                return True, f"PBI claimed in .ralph/current/{entry.name}"

    return False, ""
```

Invocation point: in `main()` after argument parsing, before `_checkout_queue_branch` or any other side-effecting call:

```python
if not args.force:
    is_running, evidence = _detect_ralph_running(repo)
    if is_running:
        print(_refusal_message(evidence), file=sys.stderr)
        return 2
```

`_refusal_message` returns the multi-line string from Section 2 of the design (header + evidence + 4 numbered remediation options including `--force`).

`--force` flag added to the argparse alongside the existing flags:

```python
parser.add_argument(
    "--force",
    action="store_true",
    help="Bypass the ralph-running detection (use only after halting ralph).",
)
```

### Refusal message shape

```
error: ralph appears to be running in this checkout.
Evidence: {evidence}.

Adding a PBI here will race with ralph and may corrupt branch state
(your commit can land on the wrong branch, requiring revert + cherry-pick
to recover).

Use one of these instead:

  1. A separate clone (recommended for ROSA-style workflows):
       git clone <repo-url> /tmp/ralph-add
       cd /tmp/ralph-add
       git checkout ralph-queue
       <re-run ralph-add here>

  2. A git worktree from this repo:
       git worktree add ../ralph-queue-wt ralph-queue
       cd ../ralph-queue-wt
       <re-run ralph-add here>

  3. Halt ralph first, then add here.

  4. If you know what you're doing, re-run with --force.
```

The literal `<repo-url>` is documentation-style; not auto-detected from the repo's `origin` URL (would add complexity for no benefit when the operator already knows their remote).

### Documentation deliverables

**`docs/runbooks/safe-pbi-add.md`** — new file, ~80–100 lines:

- **TL;DR**: never add a PBI from ralph's running checkout
- **Why**: single-checkout + ralph's branch switching = race. References the 2026-05-27 incident commit hashes (`7c65ae4`, revert `56ce005`, cherry-pick `ef3d0e8`) as the concrete example
- **Workflow A — separate clone** (preferred for ROSA-style / multi-machine setups): full command sequence
- **Workflow B — git worktree** (preferred on a single dev machine): full command sequence including cleanup
- **What ralph-add now refuses**: documents the guard rail behavior + the `--force` flag
- **For Claude sessions**: link to the CLAUDE.md rule

**`docs/bootstrap-operator-runbook.md`** — modify: insert a callout near where ralph-add is introduced, pointing at the new runbook.

**`CLAUDE.md`** — modify: add a top-level rule under a new "Adding PBIs to ralph" heading. Concise version of the runbook plus the worktree command. Tells future Claude sessions to use a worktree before any PBI-add work.

## Testing

```python
# tests/skills/test_ralph_add.py

def test_add_refuses_when_head_is_ralph_feature_branch(
    fake_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fake repo on ralph/PBI-FAKE branch -> add.py exits 2 with refusal."""
    # Set up a git repo on a ralph/PBI-FAKE branch.
    # Invoke add.py via subprocess (mock the workitem fetch).
    # Assert exit code 2 and stderr contains:
    #   - "ralph appears to be running"
    #   - "ralph/PBI-FAKE"


def test_add_refuses_when_current_dir_has_pbi(
    fake_git_repo: Path
) -> None:
    """Even on ralph-queue with HEAD clean, if .ralph/current/<X>/ exists,
    refuse."""
    # Repo on ralph-queue, .ralph/current/SOMETHING/ exists.
    # Invoke add.py. Assert exit code 2 + stderr mentions current/.


def test_add_proceeds_with_force_flag(
    fake_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--force bypasses the guard rail."""
    # Same setup as the refusal test, but pass --force.
    # Mock the actual workitem-fetch + git commit operations.
    # Assert add.py proceeds past the guard rail (does not exit 2 at
    # the detection point).


def test_add_proceeds_normally_when_no_evidence_of_ralph(
    fake_git_repo: Path
) -> None:
    """Clean ralph-queue checkout with empty .ralph/current/ -> add.py
    runs its normal path. Pre-existing happy-path test should still pass."""
```

## Acceptance

- `skills/ralph-add/scripts/add.py` has `_detect_ralph_running` returning `tuple[bool, str]`
- `add.py` main() calls the check BEFORE any side-effecting work; exits 2 + refusal message when triggered
- `--force` flag added; bypasses the guard
- Refusal message contains evidence string + 4 numbered remediation options including `--force`
- `docs/runbooks/safe-pbi-add.md` exists with both canonical workflows
- `docs/bootstrap-operator-runbook.md` has a callout linking to the new runbook
- `CLAUDE.md` has the new "Adding PBIs to ralph" section
- 4 tests in `tests/skills/test_ralph_add.py`: refuse-on-ralph-branch, refuse-on-current-dir, force-bypass, happy-path-clean
- pytest / ruff / mypy strict all green
- depends_on: `[]`

## Out of scope

- Plumbing-safe writer (would require ~400 lines of new code; YAGNI for production where separate clones make this irrelevant)
- `ralph-executor add-pbi` CLI subcommand (existing skill covers the path; sibling CLI would duplicate)
- Auto-creating worktrees (`--via-worktree` flag) — docs tell operators how
- Process-level detection (`pgrep ralph-executor`) — platform-specific; file heuristics cover real cases
- Cleaning up the historical 2026-05-27 incident (already recovered via revert + cherry-pick)
- Updating `ralph_executor`'s own queue mutations (ralph owns its branch; race is only with external writers)

## Risks

| Risk | Mitigation |
|---|---|
| Operator manually checks out `ralph/foo` → guard rail false-positive | `--force` escape hatch named in the refusal message |
| Ralph between PBIs with empty `current/` → guard rail false-negative | Tolerable: in that state the checkout is effectively clean; a same-instant commit wouldn't race because ralph isn't about to switch branches |
| Operator on a fresh clone with no `.ralph/current/` directory yet | Heuristic 2 returns False harmlessly; add.py proceeds |
| Documentation drift over time | All three doc deliverables are short, link to each other; CLAUDE.md edit is one section |
| Operator on Windows where the git subprocess for `rev-parse` could stall | Wrap `_run_git` in the existing timeout path used elsewhere in the skill; refusal-detection happens locally with no network |
