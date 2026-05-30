# Design: `ralph-new` — author PBIs directly into the ralph-queue

**Date:** 2026-05-30
**Status:** Approved (brainstorm), pending implementation plan
**Author:** gethin (brainstormed with Claude)
**Supersedes:** the GitHub-issue / `ralph-add` submission flow

## 1. Overview

`ralph-new` is a new operator skill that authors a well-formed PBI
directly into the `ralph-queue` repo, with no GitHub issue round-trip.
After this change, `ralph-new` is the **only** submission path: the
existing `ralph-add` + `workitem-fetch-*` skills are retired in the same
PR.

### Motivation

The current submission flow is:

```
operator → file GitHub issue → ralph-add → workitem-fetch-github →
queue clone → executor
```

Three problems:

1. Operators must file a GitHub issue first, even when the work item
   originated in a meeting / Slack / on paper.
2. `workitem-fetch-github` requires `GH_TOKEN` + the issue to be
   correctly labelled (bug / feature / severity-*) for the fetcher to
   classify it; mis-labelled issues silently become wrong-typed PBIs.
3. The Azure DevOps story is a deferred symmetric copy of all of this
   complexity (`workitem-fetch-ado` Phase 2).

Removing GitHub issues from the submission path eliminates all three.

### Audience

BA / PM / operator who knows the work but does not want to file a
GitHub issue first. After this lands, *everyone* uses `ralph-new`.

### Surface

`uv run python skills/ralph-new/scripts/new.py [flags]` — interactive
prompts by default, with `--non-interactive` for CI / scripting.

### Output

A new directory under `<queue_clone>/.ralph/inbox/<auto-slug>/`,
committed and pushed to `emp3thy/ralph-queue` on `main`. The exact file
shape depends on PBI type — see Section 3.

## 2. CLI surface

### Interactive prompt sequence

```text
$ uv run python skills/ralph-new/scripts/new.py
Title: Fix login timeout on Safari
Type [bug/feature]: bug
Severity [critical/high/normal/low] (default: normal): high
Target repo URL: https://github.com/emp3thy/svc-auth
Depends on (comma-separated PBI ids, blank for none): 

  -- bug template prompts --
Symptom (one line): Login form hangs ~30s on Safari then times out
Root cause (suspected, blank line to finish):
  TLS handshake racing against fetch abort signal
  (blank)
Impact (blank line to finish):
  Safari users (~12% of traffic) cannot log in
  (blank)
Acceptance criteria (blank line to finish):
  - Safari login completes within 2s p95
  - Existing Chrome/Firefox flow unaffected
  (blank)

  -- bug REPRODUCE.md prompts --
Environment (blank line to finish):
  macOS 14, Safari 17.4
  (blank)
Steps (blank line to finish):
  1. Open https://app.example.com/login in Safari 17
  2. Enter creds + submit
  3. Wait
  (blank)
Expected (blank line to finish): Auth response within 2s
Actual (blank line to finish): 30s wait then "request failed" toast

Resolved id: FIX-LOGIN-TIMEOUT-ON-SAFARI
About to write: <queue_clone>/.ralph/inbox/FIX-LOGIN-TIMEOUT-ON-SAFARI/
  - BUG.md
  - REPRODUCE.md
  - HISTORY.md
Confirm [y/N]: y
{"pbi_id": "FIX-LOGIN-TIMEOUT-ON-SAFARI", "type": "bug", ...}
```

For features, replace the per-type prompts with:

```text
  -- feature template prompts --
Description (blank line to finish): {free-form prose, multi-line}
Acceptance criteria (blank line to finish): {bulleted list}
Spec doc path (blank to skip): docs/superpowers/specs/...md
Plan doc path (blank to write TODO stub): docs/superpowers/plans/...md
```

### Flags

All optional; each overrides the corresponding prompt. Required fields
that lack both a flag and a prompt answer cause exit 2 under
`--non-interactive`.

| Flag | Description |
|---|---|
| `--title TEXT` | Skip title prompt. |
| `--type {bug,feature}` | Skip type prompt. |
| `--severity {critical,high,normal,low}` | Default `normal`. |
| `--target-repo URL` | HTTPS owner/name URL. |
| `--depends-on ID` | Repeatable. Each id validated for syntax and existence. |
| `--parent-id ID` | Optional. |
| `--id SLUG` | Override auto-generated slug. |
| `--spec-path PATH` | For features: path to spec doc. |
| `--plan-path PATH` | For features: path to plan doc; blank → TODO stub in `PLAN.md`. |
| `--body-file PATH` | Read entire entry-file body from file; skips per-section prompts. |
| `--reproduce-file PATH` | (Bug only) Read entire `REPRODUCE.md` body from file. |
| `--non-interactive` | Fail loudly if any required field is missing. No prompts. |
| `--no-push` | Commit but do not push. |
| `--dry-run` | Print envelope, no clone, no write, no commit. |
| `--check-depends-on` | Force clone under `--dry-run` so existence check runs. |
| `--workspace PATH` | Override `workspace_root`. |
| `--queue-repo URL` | Override `queue_repo`. |
| `--queue-branch BRANCH` | Override `queue_branch`. |

### Slug generation

`title → uppercase → strip punctuation → join word runs with hyphens →
truncate at 80 chars → strip leading/trailing hyphens`.

Examples:

| Title | Slug |
|---|---|
| `Fix login timeout on Safari` | `FIX-LOGIN-TIMEOUT-ON-SAFARI` |
| `Fix bug: Safari hangs (#42)` | `FIX-BUG-SAFARI-HANGS-42` |
| `   trailing   spaces   ` | `TRAILING-SPACES` |

Collision check against `inbox/`, `current/`, `pending-pr/` in the
queue clone. On collision, append `-2`, `-3`, … `-10`. Overflow → exit
2 with `error: too many collisions on '<slug>'; rerun with --id
<unique-slug>`.

## 3. PBI file layout (validated against `prompt/PROMPT.md` and
`ralph_executor/queue/filesystem.py`)

### Frontmatter (identical for both types)

```yaml
---
id: <SLUG>
type: <bug | feature>
status: inbox
severity: <critical | high | normal | low>
attempts: 0
created_at: <ISO-8601 UTC>
updated_at: <ISO-8601 UTC>
depends_on: []                       # list of other PBI ids
target_repo: <https://host/owner/name>
parent_id: <other-slug>              # only if --parent-id given
---
```

Frontmatter is parsed by `ralph_executor.queue.filesystem.parse_pbi_directory`.
Required fields: `id`, `type`, `severity`, `attempts`, `created_at`,
`updated_at`. Optional: `depends_on` (defaults `[]`), `parent_id`,
`status`. `target_repo` is enforced as required at claim time by
`ralph_executor.loop._read_target_repo_from_pbi`.

### Bug directory layout

```
<id>/
├── BUG.md          # frontmatter + bug body
├── REPRODUCE.md    # repro recipe
└── HISTORY.md      # constant template; executor appends iteration logs
```

**`BUG.md` body sections (in order):**

```markdown
# {title}

## Symptom
{one-line summary}

## Root cause (suspected)
{operator's hypothesis at submission time}

## Impact
{who is affected, scope, justification of severity}

## Acceptance criteria
- {testable criterion}
- {testable criterion}
```

**`REPRODUCE.md` body sections (in order):**

```markdown
# Reproduce: {title}

## Environment
{OS, runtime, versions, configuration relevant to the bug}

## Steps
1. ...
2. ...
3. ...

## Expected
{what should happen}

## Actual
{what actually happens}
```

### Feature directory layout

```
<id>/
├── PBI.md          # frontmatter + feature body
├── PLAN.md         # pointer to docs/superpowers/plans/, or TODO stub
└── HISTORY.md      # constant template; executor appends iteration logs
```

**`PBI.md` body (free-form prose + AC + pointers):**

```markdown
# {title}

{free-form description prose, one or more paragraphs}

Spec: `{spec-path-or-TBD}`
Plan: `{plan-path-or-TBD}`

## Acceptance criteria
- {testable criterion}
- {testable criterion}

See `PLAN.md` for the implementation plan pointer.
```

**`PLAN.md` — path supplied:**

```markdown
# Plan: {title}

See `{plan-path}` for the canonical implementation plan task list.

Execute the plan's tasks in order. The plan is the authoritative source
— this `PLAN.md` is just a pointer.
```

**`PLAN.md` — path blank (TODO stub):**

```markdown
# Plan: {title}

TODO: write the implementation plan in
`docs/superpowers/plans/<file>.md` and update this file to point at it.
```

### `HISTORY.md` (constant for both types)

```markdown
<!-- Executor appends attempt records here. Do not delete — required by the PBI directory schema. -->
```

### Empty-section behaviour

For any prompted section (BUG.md sections, REPRODUCE.md sections,
feature description), blank input renders the heading followed by
`_Not provided_` (italic). The section never vanishes — reviewers can
spot gaps at a glance.

## 4. Module layout + scope of deletions

### New module layout

```
skills/ralph-new/
├── SKILL.md
└── scripts/
    ├── new.py           CLI + prompts + orchestration
    ├── writer.py        render_frontmatter, write_pbi_dir,
    │                    HISTORY_TEMPLATE constant
    ├── templates.py     BUG / REPRODUCE / PBI / PLAN body templates
    ├── slugify.py       title → slug + collision resolution
    └── validate.py      target_repo, severity, type, id, parent_id,
                         depends_on validation
```

Each file ≤200 lines. `new.py` orchestrates; the other four are pure
functions covered by unit tests.

### Files / skills removed in the same PR

| Path | Why |
|---|---|
| `skills/ralph-add/` | Retired. `ralph-new` is the only submission path. |
| `skills/workitem-fetch-github/` | Only consumer was `ralph-add`. |
| `skills/workitem-fetch-mock/` | Only consumer was `ralph-add`. |
| `tests/skills/test_ralph_add.py` | Tests dead skill. |
| `tests/skills/test_workitem_fetch_github.py` | Tests dead skill. |
| `tests/skills/test_workitem_fetch_mock.py` | Tests dead skill. |

### Files modified (not removed)

| Path | Change |
|---|---|
| `ralph_executor/host_select.py` | Drop `workitem-fetch-<host>` staging. Keep `pr-<host>` staging. |
| `tests/test_host_select.py` | Remove `workitem-fetch` staging tests. Keep `pr` staging tests. Add a regression guard test that `workitem-fetch` is NOT staged. |
| `prompt/PROMPT.md` | Replace the stale `target_repo` paragraph (see Section 8). |
| `docs/runbooks/ralph-setup.md` | Replace the `ralph-add` operator-skill subsection with `ralph-new`. Remove `--via-mcp` references. |
| `README.md` | Update any `ralph-add` / `workitem-fetch` mentions. |

PR-skill staging (`pr-github` / `pr-ado`) is untouched.

## 5. Validation + error handling

### Field validation

| Field | Rule | On failure |
|---|---|---|
| `title` | non-empty, ≤200 chars, no `\n` `\r` | exit 2 |
| `type` | `bug` or `feature` (no `pr-feedback` — sweep-only) | exit 2 |
| `severity` | one of `critical` / `high` / `normal` / `low` | exit 2 |
| `target_repo` | HTTPS URL, host present, `<owner>/<name>` path, no `\n`/`\r` | exit 2 |
| `parent_id` | matches id regex if given | exit 2 |
| `depends_on` (syntax) | each id matches `^[A-Z0-9][A-Z0-9-]*[A-Z0-9]$`, 2–80 chars | exit 2 |
| `slug` (auto) | post-generation: 2–80 chars, regex above, doesn't start/end with `-` | exit 2 |

### `depends_on` existence check

After the queue clone is acquired (Section 6, flow step 2), each
`depends_on` id is looked up across all states:

```python
STATES = ("inbox", "current", "pending-pr", "blocked", "done", "archive")
def exists(dep_id):
    return any((queue_clone / ".ralph" / s / dep_id).is_dir() for s in STATES)
```

On miss → exit 2 with `error: depends_on id '<dep_id>' not found in
queue (searched inbox / current / pending-pr / blocked / done /
archive)`.

Under `--dry-run`, the existence check is skipped by default (the
clone isn't acquired). A warning prints on stderr:
`dry-run: skipping depends_on existence check (requires queue clone)`.
Pass `--check-depends-on` to force the clone.

### Collision handling

Slug `FOO-BAR` exists in queue → try `FOO-BAR-2`, `FOO-BAR-3`, …
`FOO-BAR-10`. All 10 collide → exit 2.

### Queue-clone errors

| Symptom | Behaviour |
|---|---|
| Clone fails (auth, missing branch) | `QueueCloneError` to stderr, exit 2 |
| Push fails (non-FF, network) | exit 2 with hint to retry; local commit preserved (rerun with `--no-push` then `git push` manually) |

### Exit codes

| Code | Meaning |
|---|---|
| `0` | PBI written and pushed (or committed when `--no-push`) |
| `2` | Validation error, collision overflow, queue / auth error, `--non-interactive` missing required field |
| `130` | Operator pressed Ctrl+C; no partial commit (signal handler aborts in-place) |

## 6. Flow

1. Validate field syntax (all fields, no clone touch).
2. Acquire queue clone (clone or fast-forward pull).
3. Resolve slug + collision check.
4. Validate `depends_on` existence in the queue.
5. Confirm intent (interactive `y/N` prompt; skipped when
   `--non-interactive`).
6. Render frontmatter + bodies + `HISTORY.md`.
7. Write the directory.
8. `git add` + commit (`chore(queue): add <id>`, matching the existing
   `ralph-add` convention regardless of type).
9. `git push origin main` (unless `--no-push`).
10. Print JSON envelope, exit 0.

Each step's failure mode is documented in Section 5.

## 7. Testing strategy

Five test files, all using `pytest` + `monkeypatch` for `builtins.input`
and the existing `queue_env` fixture pattern. The fixture (bare-remote
queue with seeded `main` + tmp workspace) is lifted from
`tests/skills/test_ralph_add.py` (being deleted) into
`tests/conftest.py`.

### `tests/skills/test_ralph_new_slugify.py`

- `test_slug_basic`
- `test_slug_strips_punctuation`
- `test_slug_truncated_at_80`
- `test_slug_collapses_whitespace`
- `test_slug_collision_appends_suffix`
- `test_slug_collision_overflow_exits_2`
- `test_slug_rejects_empty_title`
- `test_slug_rejects_punctuation_only`

### `tests/skills/test_ralph_new_writer.py`

- `test_render_frontmatter_feature_full`
- `test_render_frontmatter_bug_no_parent_id`
- `test_render_frontmatter_depends_on_default_empty_list`
- `test_render_frontmatter_depends_on_serialises_correctly`
- `test_history_template_constant_matches_expected_marker`
- `test_write_pbi_dir_feature_creates_PBI_PLAN_HISTORY` (exactly 3 files)
- `test_write_pbi_dir_bug_creates_BUG_REPRODUCE_HISTORY` (exactly 3 files)
- `test_bug_template_sections_order`
- `test_feature_template_sections_order`
- `test_reproduce_template_sections_order`
- `test_empty_section_renders_not_provided_placeholder`
- `test_plan_pointer_when_path_supplied`
- `test_plan_stub_when_path_blank`

### `tests/skills/test_ralph_new_validate.py`

- `test_validate_target_repo_*` (happy + each rejection branch)
- `test_validate_pbi_id_*` (happy + reject lowercase, leading hyphen,
  too short, too long)
- `test_validate_severity_*`
- `test_validate_type_*` (reject `pr-feedback`)
- `test_validate_depends_on_syntax_*`

### `tests/skills/test_ralph_new_e2e.py` (uses `queue_env`)

- `test_full_run_bug_writes_three_files_and_pushes` (assert commit
  message is `chore(queue): add <id>`)
- `test_full_run_feature_writes_three_files_with_plan_pointer`
- `test_full_run_feature_writes_three_files_with_plan_stub`
- `test_depends_on_existence_check_passes` (seed `done/PRECURSOR/`)
- `test_depends_on_existence_check_fails_exit_2`
- `test_depends_on_existence_check_searches_all_states`
- `test_slug_collision_appends_suffix_end_to_end`
- `test_dry_run_no_clone_no_commit_envelope_printed`
- `test_dry_run_with_check_depends_on_clones_and_validates`
- `test_non_interactive_missing_title_exits_2`
- `test_target_repo_invalid_url_exits_2`
- `test_parent_id_threaded_into_frontmatter`
- `test_ctrl_c_during_prompt_exits_130_no_commit`
- `test_no_push_commits_but_does_not_push`

### `tests/test_host_select.py` (modify)

- Delete `workitem-fetch_*` staging tests.
- Keep all `pr-*` staging tests.
- Add `test_workitem_fetch_no_longer_staged` regression guard.

### `tests/test_prompt_contents.py` (new, tiny)

- `test_target_repo_documented_as_required` — assert
  `prompt/PROMPT.md` contains the string `MUST carry a` `target_repo`
  to prevent the stale wording sneaking back.

## 8. `prompt/PROMPT.md` edit

Replace the current paragraph (lines 46–49 at time of writing):

```
Every PBI's frontmatter also carries a `target_repo` field — an HTTPS
URL of the repo the PBI applies to (e.g.
`https://github.com/emp3thy/ralph`). This is informational metadata for
now; the loop does not yet act on it.
```

with:

```
Every PBI's frontmatter MUST carry a `target_repo` field — an HTTPS URL
of the repo this PBI applies to (e.g. `https://github.com/emp3thy/ralph`).
This is the canonical source of truth for which repo to operate on. The
executor clones `target_repo` into the multi-repo workspace at claim time;
your current working directory for this iteration is that clone.
```

## 9. Out of scope / future work

- **Per-org template customisation:** not in v1 (YAGNI). Move to Jinja
  templates only if a second consumer asks for it.
- **MCP entry point:** defer to v2; same surface as the Reserved
  `ralph-add --via-mcp` flag that is being removed.
- **Attachment support:** not in v1. PBIs needing attachments today
  use `ralph-add` + GitHub issues; that path is going away. If
  attachment-bearing PBIs become a real need post-launch, design v2 of
  the skill to take a `--attach FILE` flag and base64-encode into a
  sibling `ATTACHMENTS/` directory.
- **`pr-feedback` PBIs:** sweep-generated. `ralph-new` deliberately
  rejects `type=pr-feedback`.

## 10. Migration notes

Operators currently scripted on `ralph-add` must migrate. The replacement
commands (illustrative):

| Old | New |
|---|---|
| `python skills/ralph-add/scripts/add.py --work-item 42 --target-repo URL` | `python skills/ralph-new/scripts/new.py --non-interactive --title "..." --type ... --severity ... --target-repo URL --body-file body.md` |

Existing PBIs already in the queue are unaffected; the layout and
frontmatter contract are unchanged. Only the *submission tool* changes.
