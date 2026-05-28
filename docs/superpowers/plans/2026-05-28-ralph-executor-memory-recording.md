# Ralph executor memory recording — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make ralph executor's per-PBI subagent record observations to better-memory in the correct project scope (PBI's `target_repo`), with inline triggers tuned for the one-shot iteration lifecycle.

**Architecture:** Adds an environment-variable override (`BETTER_MEMORY_PROJECT`) to better-memory's project resolver; ralph's `claude_spawn` sets it from `pbi.target_info.name` so subprocess observations land in the target repo's project. `prompt/PROMPT.md` gets a new "Recording learnings" section with inline triggers and iteration-start retrieval calls. A one-shot curation pass promotes ralph-runtime workflow reflections into `~/.better-memory/knowledge-base/standards/ralph-runtime.md` (standards directory is the cross-project bucket; entries surface in `knowledge.search` regardless of project).

**Tech Stack:** Python 3.12 (better-memory + ralph), pytest, ruff. Two repos touched: `C:\Users\gethi\source\better-memory` and `C:\Users\gethi\source\ralph`.

**Spec:** `docs/superpowers/specs/2026-05-28-ralph-executor-memory-recording-design.md`

---

## File structure

| Repo | File | Action | Responsibility |
|------|------|--------|----------------|
| better-memory | `better_memory/config.py` | Modify | Add env-var branch to `project_name()` |
| better-memory | `tests/test_config.py` | Modify | New tests for env-var precedence |
| better-memory | `website/configuration.md` | Modify | Document the new override |
| ralph | `ralph_executor/claude_spawn.py` | Modify | Inject `BETTER_MEMORY_PROJECT` env var |
| ralph | `tests/executor/test_claude_spawn.py` | Modify | New tests for env injection |
| ralph | `prompt/PROMPT.md` | Modify | Add "Recording learnings" section |
| better-memory | `knowledge-base/standards/ralph-runtime.md` | Create | One-shot curation: promoted workflow rules |

---

## Task 1: better-memory env-var override (TDD)

**Repo:** `C:\Users\gethi\source\better-memory`

**Confidence:** 95% — mirrors the existing `.better-memory` file-override branch exactly; resolver function is small, isolated, well-tested.

**Files:**
- Modify: `better_memory/config.py` — `project_name()` function (lines 136-177)
- Modify: `tests/test_config.py` — add tests alongside existing `test_project_name_*`

- [ ] **Step 1: Create the feature branch**

```bash
cd /c/Users/gethi/source/better-memory
git checkout -b memory-project-env-override
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_config.py`:

```python
def test_project_name_env_var_wins_over_file_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BETTER_MEMORY_PROJECT env var takes precedence over .better-memory file."""
    cwd = tmp_path / "proj"
    cwd.mkdir()
    (cwd / ".better-memory").write_text("from-file\n", encoding="utf-8")
    monkeypatch.setenv("BETTER_MEMORY_PROJECT", "from-env")
    assert project_name(cwd) == "from-env"


def test_project_name_env_var_wins_over_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """BETTER_MEMORY_PROJECT env var takes precedence over git resolution."""
    repo = tmp_path / "myrepo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    monkeypatch.setenv("BETTER_MEMORY_PROJECT", "from-env")
    assert project_name(repo) == "from-env"


def test_project_name_empty_env_var_falls_through_to_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty BETTER_MEMORY_PROJECT is treated as unset; file override still wins."""
    cwd = tmp_path / "proj"
    cwd.mkdir()
    (cwd / ".better-memory").write_text("from-file\n", encoding="utf-8")
    monkeypatch.setenv("BETTER_MEMORY_PROJECT", "")
    assert project_name(cwd) == "from-file"


def test_project_name_whitespace_only_env_var_falls_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whitespace-only BETTER_MEMORY_PROJECT is treated as unset."""
    cwd = tmp_path / "proj"
    cwd.mkdir()
    (cwd / ".better-memory").write_text("from-file\n", encoding="utf-8")
    monkeypatch.setenv("BETTER_MEMORY_PROJECT", "   \t\n  ")
    assert project_name(cwd) == "from-file"


def test_project_name_env_var_stripped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Surrounding whitespace in BETTER_MEMORY_PROJECT is stripped."""
    monkeypatch.setenv("BETTER_MEMORY_PROJECT", "  scoped-name  \n")
    assert project_name(tmp_path) == "scoped-name"
```

If `subprocess` and `pytest` aren't already imported at the top of `tests/test_config.py`, add them.

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd /c/Users/gethi/source/better-memory
uv run pytest tests/test_config.py -k env_var -v
```

Expected: 5 FAILs (env-var branch doesn't exist yet).

- [ ] **Step 4: Implement the env-var branch**

In `better_memory/config.py`, modify `project_name()` — insert the new branch BEFORE the `.better-memory` file check (lines 162-170). Update the docstring to reflect the new resolution order.

Replace lines 136-177 with:

```python
def project_name(cwd: Path | None = None) -> str:
    """Return the canonical project name for ``cwd`` (defaults to ``Path.cwd()``).

    Resolution order:
    1. ``BETTER_MEMORY_PROJECT`` environment variable, stripped. Empty/whitespace-only
       values fall through to the next branch. This is the subprocess-scoping signal
       — e.g. ralph's executor sets it per-iteration so subagent observations land
       in the PBI's target_repo regardless of the worktree's cwd.
    2. ``<cwd>/.better-memory`` override file: first non-empty stripped line.
       The ``.better-memory`` override is checked only at ``cwd``, not at
       ancestors — the override is a deliberate per-directory signal.
    3. ``git rev-parse --git-common-dir`` (handles worktrees: returns the main
       repo's .git directory). Project name = parent dir's ``.name``.
    4. ``"general"`` if no git tree is found or git is unavailable.

    Used uniformly by knowledge search, observation writes/reads, episode
    scoping, the UI panel filter, and hook payloads — every subsystem that
    buckets state by project must call this helper, never construct the
    name inline.

    The env-var and override-file branches are intentionally **not** memoized
    so editing them mid-session takes effect immediately. The git resolution
    is memoized by absolute path via :func:`_resolve_git_project`.
    """
    fn = "project_name"
    with _diag.trace(fn):
        _diag.step(fn, "check_env_var")
        env_value = os.environ.get("BETTER_MEMORY_PROJECT", "").strip()
        if env_value:
            _diag.step(fn, "env_var_returned", value=env_value)
            return env_value

        _diag.step(fn, "resolve_cwd")
        cwd = cwd if cwd is not None else Path.cwd()
        _diag.step(fn, "cwd_resolved", cwd=str(cwd))

        override = cwd / ".better-memory"
        _diag.step(fn, "check_override_file", path=str(override))
        if override.is_file():
            _diag.step(fn, "override_found_reading")
            text = override.read_text(encoding="utf-8").strip()
            if text:
                first = text.splitlines()[0].strip()
                _diag.step(fn, "override_returned", value=first)
                return first

        _diag.step(fn, "calling_resolve_git_project")
        cwd_resolved = str(cwd.resolve())
        _diag.step(fn, "cwd_resolve_done", value=cwd_resolved)
        result = _resolve_git_project(cwd_resolved) or "general"
        _diag.step(fn, "returning", value=result)
        return result
```

- [ ] **Step 5: Run new tests to verify pass**

```bash
uv run pytest tests/test_config.py -k env_var -v
```

Expected: 5 PASSes.

- [ ] **Step 6: Run full config test suite to check no regression**

```bash
uv run pytest tests/test_config.py -v
```

Expected: all PASS.

- [ ] **Step 7: Lint + typecheck**

```bash
uv run ruff check better_memory/config.py tests/test_config.py
uv run mypy better_memory/config.py
```

Expected: clean.

- [ ] **Step 8: Commit**

```bash
git add better_memory/config.py tests/test_config.py
git commit -m "feat(config): BETTER_MEMORY_PROJECT env var overrides project name resolution"
```

---

## Task 2: better-memory — document the override

**Repo:** `C:\Users\gethi\source\better-memory`

**Confidence:** 95% — documentation drift fix mirroring an established table format.

**Files:**
- Modify: `website/configuration.md` — env var table + override section

- [ ] **Step 1: Add row to env-var table**

Open `website/configuration.md`. Find the env-var table near the top (the one with `BETTER_MEMORY_HOME` / `BETTER_MEMORY_EMBEDDINGS_BACKEND` / `BETTER_MEMORY_AUTO_PRUNE`). Add a row:

```markdown
| `BETTER_MEMORY_PROJECT` | unset | Force the project name for all calls in this process. Highest-priority project-resolution signal — overrides both `.better-memory` file and git-derived name. Designed for subprocess scoping (e.g. ralph's executor sets it per-iteration so subagent observations land in the PBI's target_repo regardless of the worktree's cwd). Empty/whitespace-only values are treated as unset. |
```

- [ ] **Step 2: Update the per-cwd override section**

Find the existing `.better-memory` override section (around line 22-33, with the `echo "my-project" > .better-memory` example). Update the preceding prose to reflect the new resolution order. Replace the section's intro with:

```markdown
## Project name resolution

Resolution priority, highest first:

1. **`BETTER_MEMORY_PROJECT` env var** — for subprocess scoping. Stripped; empty/whitespace-only values fall through.
2. **`.better-memory` file** — first non-empty stripped line of `<cwd>/.better-memory`. Checked only at cwd, not at ancestors.
3. **Git repo name** — via `git rev-parse --git-common-dir`, walks parents; handles worktrees by returning the main repo's name.
4. **`"general"`** — fallback when no git tree is found.

### Per-cwd file override

To pin a project name for a directory:

```bash
echo "my-project" > .better-memory
```

This applies uniformly to knowledge search, observation writes/reads, episode scoping, and the UI panel filter.
```

- [ ] **Step 3: Commit**

```bash
git add website/configuration.md
git commit -m "docs(configuration): document BETTER_MEMORY_PROJECT env var override"
```

---

## Task 3: better-memory — push + open PR

**Repo:** `C:\Users\gethi\source\better-memory`

**Confidence:** 95% — standard PR open.

- [ ] **Step 1: Push the branch**

```bash
cd /c/Users/gethi/source/better-memory
git push -u origin memory-project-env-override
```

- [ ] **Step 2: Open the PR**

```bash
gh pr create --title "feat(config): BETTER_MEMORY_PROJECT env var override" --body "$(cat <<'EOF'
## Summary

- Adds `BETTER_MEMORY_PROJECT` environment variable as the highest-priority project-name resolution signal. Stripped; empty/whitespace-only values fall through.
- Resolution order is now: env var > `.better-memory` file > git repo name > `"general"`.
- Designed for subprocess scoping: ralph's executor will set this per-iteration so the subagent's observations land in the PBI's `target_repo` regardless of which worktree cwd it runs in.

## Test plan

- [x] New tests in `tests/test_config.py` covering env-var precedence over both file override and git resolution, plus empty/whitespace handling and stripping.
- [x] Full `tests/test_config.py` suite passes (no regression).
- [x] `ruff check` and `mypy` clean.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Record PR URL**

Note the returned PR URL — Task 4's claude_spawn change depends on this PR being merged (or at least available) before the env-var set on ralph's side has any effect.

---

## Task 4: ralph claude_spawn env injection (TDD)

**Repo:** `C:\Users\gethi\source\ralph`

**Confidence:** 92% — mirrors the existing `GH_OWNER` pattern exactly (lines 549-550 of `claude_spawn.py`). Slight risk: the test must use the same fake-claude infrastructure as `test_spawn_sets_gh_owner_env_from_target_info`; lift = quote the existing test verbatim as a template.

**Files:**
- Modify: `ralph_executor/claude_spawn.py` — extend the `pbi.target_info is not None` block (lines 549-550)
- Modify: `tests/executor/test_claude_spawn.py` — add tests alongside `test_spawn_sets_gh_owner_env_from_target_info` (line 94) and `test_spawn_omits_gh_owner_when_target_info_absent` (line 125)

- [ ] **Step 1: Create the feature branch**

```bash
cd /c/Users/gethi/source/ralph
git checkout -b ralph-executor-memory-recording
```

- [ ] **Step 2: Write the failing tests**

Add to `tests/executor/test_claude_spawn.py`, near the existing GH_OWNER tests:

```python
def test_spawn_sets_better_memory_project_env_from_target_info(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    fake_claude_binary: Path,
    tmp_path: Path,
) -> None:
    """env['BETTER_MEMORY_PROJECT'] = pbi.target_info.name so subagent
    observations land in the target_repo's project scope rather than the
    worktree-derived cwd project."""
    import json
    from dataclasses import replace

    from ralph_executor.url_utils import TargetRepoInfo

    pbi = _setup_current_pbi(cfg_for_repo, fake_repo)
    pbi = replace(
        pbi,
        target_info=TargetRepoInfo(host="github.com", owner="orgA", name="growatt-monitor"),
    )

    env_dump = tmp_path / "env.json"
    write_claude_script(
        fake_claude_binary,
        "import json, os, pathlib\n"
        f"pathlib.Path({str(env_dump)!r}).write_text(json.dumps(dict(os.environ)))\n",
    )
    spawn_claude_p(cfg_for_repo, pbi)
    env = json.loads(env_dump.read_text())
    assert env["BETTER_MEMORY_PROJECT"] == "growatt-monitor"


def test_spawn_omits_better_memory_project_when_target_info_absent(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    fake_claude_binary: Path,
    tmp_path: Path,
) -> None:
    """Without pbi.target_info (legacy single-target mode), the env var
    must NOT be set — the subprocess falls back to cwd-derived project
    resolution, which is correct behaviour for that mode."""
    import json

    pbi = _setup_current_pbi(cfg_for_repo, fake_repo)
    assert pbi.target_info is None

    env_dump = tmp_path / "env.json"
    write_claude_script(
        fake_claude_binary,
        "import json, os, pathlib\n"
        f"pathlib.Path({str(env_dump)!r}).write_text(json.dumps(dict(os.environ)))\n",
    )
    spawn_claude_p(cfg_for_repo, pbi)
    env = json.loads(env_dump.read_text())
    assert "BETTER_MEMORY_PROJECT" not in env
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd /c/Users/gethi/source/ralph
uv run pytest tests/executor/test_claude_spawn.py -k better_memory_project -v
```

Expected: 2 FAILs (assertion error — env var not set).

- [ ] **Step 4: Implement the env injection**

Open `ralph_executor/claude_spawn.py`. Find the existing block at lines 549-550:

```python
    if pbi.target_info is not None:
        env["GH_OWNER"] = pbi.target_info.owner
```

Replace with:

```python
    if pbi.target_info is not None:
        env["GH_OWNER"] = pbi.target_info.owner
        # Per-PBI better-memory project scope so the subagent's observations
        # land in the target repo's project rather than the cwd-derived
        # worktree path. Requires BETTER_MEMORY_PROJECT support in
        # better-memory's project resolver (see better-memory PR
        # memory-project-env-override).
        env["BETTER_MEMORY_PROJECT"] = pbi.target_info.name
```

- [ ] **Step 5: Run tests to verify pass**

```bash
uv run pytest tests/executor/test_claude_spawn.py -k better_memory_project -v
```

Expected: 2 PASSes.

- [ ] **Step 6: Run full claude_spawn suite to check no regression**

```bash
uv run pytest tests/executor/test_claude_spawn.py -v
```

Expected: all PASS.

- [ ] **Step 7: Lint**

```bash
uv run ruff check ralph_executor/claude_spawn.py tests/executor/test_claude_spawn.py
```

Expected: clean.

- [ ] **Step 8: Commit**

```bash
git add ralph_executor/claude_spawn.py tests/executor/test_claude_spawn.py
git commit -m "feat(claude_spawn): set BETTER_MEMORY_PROJECT per-PBI so subagent observations land in target_repo scope"
```

---

## Task 5: ralph PROMPT.md — recording learnings section

**Repo:** `C:\Users\gethi\source\ralph`

**Confidence:** 92% — content is verbatim below; risk is placement-within-PROMPT and ensuring the section composes with existing instructions. Lift: verbatim section text + exact insertion point.

**Files:**
- Modify: `prompt/PROMPT.md` — append new "Recording learnings (better-memory)" section near the existing process-discipline content

- [ ] **Step 1: Inspect current PROMPT.md structure**

```bash
cd /c/Users/gethi/source/ralph
grep -n '^## ' prompt/PROMPT.md
```

Expected: list of `##` section headings — pick the position just before any final "Style" / "Forbidden" section if present; otherwise append at end of file.

- [ ] **Step 2: Append the section**

Add this content at the chosen insertion point of `prompt/PROMPT.md`:

```markdown
## Recording learnings (better-memory)

This iteration is one-shot: you have no memory across iterations except what you write to better-memory. The executor sets `BETTER_MEMORY_PROJECT` so your observations and retrievals scope correctly to the PBI's target repo — call the tools directly, don't pass `project=`.

### At iteration start

Before reading any PBI files, run both of these:

1. `mcp__better-memory__memory_retrieve` with a broad `query` describing the PBI's surface (e.g. the PBI title + the component or theme). Returns target-repo observations and reflections bucketed by `do` / `dont` / `neutral`.
2. `mcp__better-memory__knowledge_search` with a `query` describing the workflow context (e.g. `"ralph iteration"`, `"git worktree"`, `"PR review thread"`). Standards (cross-project workflow rules under `knowledge-base/standards/`) always surface regardless of project. Pass `project` if you also want project-specific knowledge surfaced.

Read both before deciding how to approach the PBI. Memory may be stale — if a recalled fact conflicts with what you see now, trust current state and let the synthesis layer correct the memory later.

### Inline triggers — observe AS the iteration progresses

Do not batch observations at the end. Write each one immediately, as the trigger fires:

- **After `REPRODUCE.md` confirms the failure signature.** If the reproduction signal is non-obvious (specific input, environment variable, race condition, ordering), call `mcp__better-memory__memory_observe` with `outcome="neutral"` recording how you triggered the failure.
- **After you identify the root cause in `HISTORY.md`.** Call `memory_observe` with `outcome="failure"` recording the cause and the diagnostic path that surfaced it. Include component / file paths in `component`.
- **After a fix commit lands** that represents a non-obvious gotcha, dependency, or convention. Call `memory_observe` with `outcome="failure"` recording what would have surprised a future reader.
- **After a BugBot or reviewer-fix commit.** (Global CLAUDE.md mandatory trigger.) Call `memory_observe` with `outcome="failure"` recording the gap the fix closed — the fix's existence proves it was non-obvious. Do this BEFORE moving on to the next reviewer comment.
- **Before this iteration exits** (success, push-and-PR, or classify-as-still-going). Sweep your commits and `HISTORY.md` for anything not yet recorded — the iteration may not return, so the sweep is your backstop. If a fact is already covered by an existing observation you retrieved at iteration start, skip it.

### What NOT to record

- Step-by-step iteration progress (`HISTORY.md` is the right home for that).
- Routine PBI mechanics (claiming, committing, opening a PR).
- Anything already documented in CLAUDE.md or PROMPT.md.
- Speculation — only record facts the iteration actually proved.

### Observation field defaults

Always set `outcome` explicitly. Fill `component` (the subsystem / module / package name) and `theme` (e.g. `bug`, `decision`, `gotcha`, `dependency`) when applicable. Do not embed the project name in `content` — the env-var scoping handles that.
```

- [ ] **Step 3: Verify the file renders sanely**

```bash
head -1 prompt/PROMPT.md
grep -c '^## Recording learnings' prompt/PROMPT.md
```

Expected: file still starts with its original heading; new section count = 1.

- [ ] **Step 4: Commit**

```bash
git add prompt/PROMPT.md
git commit -m "docs(prompt): add Recording learnings section with iteration-start retrieval and inline observe triggers"
```

---

## Task 6: ralph — push + open PR

**Repo:** `C:\Users\gethi\source\ralph`

**Confidence:** 95% — standard PR open.

- [ ] **Step 1: Push the branch**

```bash
cd /c/Users/gethi/source/ralph
git push -u origin ralph-executor-memory-recording
```

- [ ] **Step 2: Open the PR**

```bash
gh pr create --title "feat(executor): per-PBI memory recording for subagent iterations" --body "$(cat <<'EOF'
## Summary

- `claude_spawn` sets `BETTER_MEMORY_PROJECT=<repo-name>` in the subagent's subprocess env when `pbi.target_info` is present, so observations land in the PBI's target_repo project scope rather than the worktree's cwd-derived project.
- `prompt/PROMPT.md` gains a "Recording learnings (better-memory)" section with iteration-start retrieval calls (`memory_retrieve` plus `knowledge_search`; the standards directory always surfaces in knowledge results regardless of project) and CLAUDE.md-style inline observe triggers tuned to the ralph one-shot lifecycle.

Depends on the better-memory PR adding the `BETTER_MEMORY_PROJECT` env-var resolver branch — until that lands the env var is harmlessly ignored.

## Test plan

- [x] New tests in `tests/executor/test_claude_spawn.py` cover env injection from `pbi.target_info.name` and omission when `target_info is None`.
- [x] Full `tests/executor/test_claude_spawn.py` suite passes.
- [x] `ruff check` clean.
- [ ] Verify against a live PBI iteration once the better-memory PR is merged: observations from a ralph iteration on a non-ralph target_repo should appear under that target_repo's project in the better-memory UI.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Task 7: One-shot curation pass — promote ralph-runtime reflections

**Target:** local user-data directory `~/.better-memory/knowledge-base/standards/` (not a repo) — no PR. The MCP server auto-reindexes on startup (mtime-driven), so writing the file + restarting better-memory's MCP server picks the new entries up.

**Confidence:** 94% — APIs verified (`ReflectionService.retire(reflection_id=...)` at `better_memory/services/reflection.py:1362`; `KnowledgeService.reindex()` runs at MCP startup; knowledge-base path = `~/.better-memory/knowledge-base/standards/`). Remaining 6% is per-reflection promote/keep judgement (candidate-list table below makes this concrete; ambiguous → keep in project scope).

**Candidate reflections to promote** (drawn from current `ralph` project; verify each is still in better-memory before retiring):

| Reflection id | Title | Promote? |
|---------------|-------|----------|
| `0dda832cbec346c29e5e81e3cb2113f9` | Windows: don't `git worktree remove` from a shell whose CWD is or was inside the worktree | YES — generic worktree-on-Windows hazard, fires across projects |
| `fc2ee30d154d46e3af52b43d0e012767` | Create the feature branch at task start, before making changes | YES — universal workflow rule |
| `1f8af6e130d44b739c6c88b1fcf3e169` | After replying to a PR review thread, mark it resolved | YES — applies to any GitHub PR work |
| `f21feb94276d4ddaa93e5f2edd29e7d7` | Render brainstorming/plans in visual companion AND surface assumptions explicitly | YES — cross-project preference |
| `e4d4f4da8bf244d2b6450a7187ff1004` | Apply confidence scoring to implementation plans | YES — cross-project planning discipline |
| `2202addd219a40be87276164dd1897e8` | Cross-read spec prose and example-code blocks for contradictions before paste | YES — universal spec hygiene |
| `fa2ddd177e944a4c999d4bff431b2525` | Spec-provided test code is not lint-clean by default | YES — universal hygiene |
| `f0089012126a47d6b21a1aadaaba7b6c` | Keep the user-choice question consistent with the architecture you just described | YES — universal brainstorming rule |
| `0da0e9ffae4c4ef7b0208d2952d69203` | Ralph executor: feature-branch checkout of PBI HISTORY.md from ralph-queue | NO — ralph codebase specific; stays in `ralph` project |
| `98056ebc80464d34a0c2e95b18882e41` | Keep website and README in sync with every code change | NO — phrased for better-memory; stays in `better-memory` project (move scope if needed) |

- [ ] **Step 1: Confirm each candidate reflection's current text**

For each `YES`-row reflection id above, fetch the verbatim title / use_cases / hints from the live DB so the curated standards entry preserves accurate text. Open a Python shell against the better-memory venv:

```bash
cd /c/Users/gethi/source/better-memory
uv run python
```

Then in the REPL (paste once per id, or loop):

```python
import sqlite3, json
from pathlib import Path
db = sqlite3.connect(Path.home() / ".better-memory" / "memory.db")
db.row_factory = sqlite3.Row
for r in db.execute("SELECT id, title, status, use_cases, hints, polarity, tech FROM reflections WHERE id = ?", ("0dda832cbec346c29e5e81e3cb2113f9",)):
    print(dict(r))
```

If `status` is `superseded` or `retired`, drop that row from the promotion list. Save the live `title`, `use_cases`, and `hints` for use in Step 2.

- [ ] **Step 2: Create the curated standards file**

Write `~/.better-memory/knowledge-base/standards/ralph-runtime.md`. One section per promoted reflection. Use the verbatim title and hints fetched in Step 1 — don't paraphrase or reorder. Layout:

```markdown
# Ralph runtime workflow rules

Cross-project rules for any Claude session operating inside (or as) a ralph
iteration — including subagent iterations spawned by `ralph_executor.claude_spawn`.

Originally captured as project-scoped reflections in the `ralph` project;
promoted to `scope=standard` so they surface in `knowledge.search` regardless
of which project the subagent is currently scoped to.

## Windows: don't `git worktree remove` from a shell whose CWD is or was inside the worktree

**When this applies:** <use_cases verbatim from reflection 0dda832cbec346c29e5e81e3cb2113f9>

- <hint 1 verbatim>
- <hint 2 verbatim>
- ...

## Create the feature branch at task start, before making changes

**When this applies:** <use_cases verbatim from reflection fc2ee30d154d46e3af52b43d0e012767>

- <hint 1 verbatim>
- ...

[... continue for each YES-row reflection, in the order listed in the candidate-list table ...]
```

- [ ] **Step 3: Retire the promoted reflections**

For each promoted reflection id, mark it retired via the service-layer helper. In the better-memory venv:

```bash
cd /c/Users/gethi/source/better-memory
uv run python
```

Then:

```python
import sqlite3
from pathlib import Path
from better_memory.services.reflection import ReflectionService

conn = sqlite3.connect(Path.home() / ".better-memory" / "memory.db")
conn.row_factory = sqlite3.Row
svc = ReflectionService(conn=conn)

for rid in [
    "0dda832cbec346c29e5e81e3cb2113f9",
    "fc2ee30d154d46e3af52b43d0e012767",
    "1f8af6e130d44b739c6c88b1fcf3e169",
    "f21feb94276d4ddaa93e5f2edd29e7d7",
    "e4d4f4da8bf244d2b6450a7187ff1004",
    "2202addd219a40be87276164dd1897e8",
    "fa2ddd177e944a4c999d4bff431b2525",
    "f0089012126a47d6b21a1aadaaba7b6c",
]:
    svc.retire(reflection_id=rid)
    print(f"retired {rid}")
conn.commit()
```

(If `ReflectionService` requires extra constructor args, fall back to direct SQL: `UPDATE reflections SET status = 'retired', updated_at = datetime('now') WHERE id = ?`.)

- [ ] **Step 4: Restart the MCP server to reindex**

The knowledge-base reindex runs automatically at MCP server startup (mtime-driven, fast). Disconnect / reconnect the better-memory MCP in the IDE host (or restart the editor) so the new standards file is picked up.

- [ ] **Step 5: Verify the standards entry surfaces in search**

In a fresh Claude session (or via the MCP tool from this session), call:

```
mcp__better-memory__knowledge_search query="git worktree remove windows"
```

Expected: a result pointing at `standards/ralph-runtime.md` with rank near the top.

Fallback if search returns no results: open the better-memory UI (the running URL at the start of this session) and confirm the file appears under the Knowledge tab.

- [ ] **Step 6: (Optional) PR a copy of `ralph-runtime.md` upstream**

If the cross-project ralph-runtime rules are valuable to anyone else running ralph against better-memory, consider opening a PR against `better-memory` to seed `knowledge-base/standards/ralph-runtime.md` as a starter — but this is optional and outside the critical path. The local copy in `~/.better-memory/` already satisfies the recording-and-retrieval goal.

---

## Out of scope

- Multi-project `memory_retrieve` (alternative C2 from the spec).
- Changes to executor sweep / triage / promote subsystems.
- Cross-iteration memory persistence beyond what better-memory provides.
