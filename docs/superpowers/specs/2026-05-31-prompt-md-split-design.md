# Prompt split — design

**Date:** 2026-05-31
**Status:** draft, awaiting user review
**Author:** Claude / gethin (brainstorm)

## Summary

Split `prompt/PROMPT.md` (single 460-line file) into a tree of per-topic folders under `prompt/`. Each topic folder either holds a single shared `.md` (applies to all PBI types) or a default `.md` plus optional per-type override subfolders (`bug/`, `pr-feedback/`). A composer assembles the per-type prompt at spawn time and the executor injects it via `--append-system-prompt`. No on-disk runtime artifact.

Override subfolder names match the `PBIType` literal values (`feature`, `bug`, `pr-feedback`) as defined in `ralph_executor/types.py`. Note the hyphen in `pr-feedback`.

The split addresses three concerns:

1. **Editability.** A 460-line file is hard to navigate when the change being made is scoped to one workflow type.
2. **Per-iteration token cost.** Feature and bug iterations currently pay for ~75 lines of feedback-only content (elevated-attention + reviewer-tone) on every spawn. Per-type assembly drops this for feature/bug.
3. **Production-vs-smoke divergence.** Production spawns Claude with `Read ./prompt/PROMPT.md` (relative path, mid-session tool call). The smoke test injects via `--append-system-prompt`. Production wins by switching to the smoke model, which is more attention-robust and eliminates a latent relative-path bug that only worked because ralph currently runs against itself.

## File layout

Topic folders carry a numeric prefix (`01-...09-`) so alpha-sort gives the assembly order. Override subfolders use the PBI type as their name. Override semantics are **strict replace** — the override owns the whole topic's content; it does not merge with the root.

```
prompt/
  01-identity/identity.md                          # shared
  02-read-order/read.md                            # default = feature
  02-read-order/bug/read.md                        # override
  02-read-order/pr-feedback/read.md                   # override
  03-workflow/workflow.md                          # default = feature
  03-workflow/bug/workflow.md                      # bug workflow + inline bug-only memory triggers
  03-workflow/pr-feedback/workflow.md                 # feedback workflow + inline elevated-attention + reviewer-tone + feedback forbiddens + feedback memory trigger
  04-stuck/stuck.md                                # shared
  05-shipping/shipping.md                          # default = feature/bug new-PR + body conventions + post-push HISTORY rule
  05-shipping/pr-feedback/shipping.md                 # override = push to existing branch
  06-memory/memory.md                              # shared: bootstrap + universal triggers + don't-record + field defaults
  07-forbidden/forbidden.md                        # shared: absolute prohibitions only
  08-closing/closing.md                            # shared
```

Three topics have overrides (`02-read-order`, `03-workflow`, `05-shipping`). The other five are pure shared.

## Content placement decisions

Content that today lives at the top level of `PROMPT.md` is mapped to topics as follows.

| Content in PROMPT.md (line range) | New home |
|---|---|
| Header, principles, `$RALPH_PBI_DIR` notes, `target_repo` (1-50) | `01-identity/identity.md` |
| "All PBI types read first" intro + per-type read order (52-107) | `02-read-order/` with overrides |
| "How to work a feature PBI" (109-134) | `03-workflow/workflow.md` (default) |
| "How to work a bug PBI" (136-168) | `03-workflow/bug/workflow.md` |
| "How to work a PR-feedback PBI" (170-202) | `03-workflow/pr-feedback/workflow.md` |
| STUCK.md triggers + structure (204-266) | `04-stuck/stuck.md` |
| Ship PR for features/bugs + PR body conventions + post-push HISTORY rule (268-300, 307-317) | `05-shipping/shipping.md` |
| Ship for PR-feedback (302-305) | `05-shipping/pr-feedback/shipping.md` |
| Elevated-attention categories (319-340) | inlined into `03-workflow/pr-feedback/workflow.md` |
| Reviewer-feedback tone rules (342-393) | inlined into `03-workflow/pr-feedback/workflow.md` |
| Memory bootstrap + universal triggers + don't-record + field defaults (395-411, 416-427) | `06-memory/memory.md` |
| Bug-specific memory triggers (412-413) | inlined into `03-workflow/bug/workflow.md` |
| Feedback-specific memory trigger (415) | inlined into `03-workflow/pr-feedback/workflow.md` |
| "What you never do" — absolute prohibitions (429-446, 454-455) | `07-forbidden/forbidden.md` |
| Feedback-specific forbiddens (447-453) | inlined into `03-workflow/pr-feedback/workflow.md` |
| Closing marker + "now read HISTORY.md" (457-460) | `08-closing/closing.md` |

Workflow-tied operational content (bug-specific memory triggers, feedback-only forbiddens, elevated-attention, reviewer-tone) lives in the relevant workflow override file rather than as separate topics. This keeps the composer rule mechanical and concentrates feedback-only weight in one file.

## Composer

A new module `ralph_executor/prompt_composer.py` exposes a single pure function:

```python
def compose_prompt(prompt_root: Path, pbi_type: str) -> str:
    """Assemble standing prompt for a given PBI type.

    Walks topic folders under prompt_root in alpha order. For each topic,
    uses the override subfolder matching pbi_type if present, otherwise
    the topic root .md file. Concatenates all chosen files into a single
    string and returns it.

    Raises PromptComposeError if a topic folder has neither a root .md
    nor a matching override, or if pbi_type is not in VALID_TYPES.
    """
    if pbi_type not in VALID_TYPES:
        raise PromptComposeError(f"unknown pbi_type {pbi_type!r}")
    sections: list[str] = []
    for topic in sorted(prompt_root.iterdir()):
        if not topic.is_dir():
            continue
        override = topic / pbi_type
        chosen = override if override.is_dir() else topic
        md_files = sorted(chosen.glob("*.md"))
        if not md_files:
            raise PromptComposeError(f"no .md in {chosen}")
        for md in md_files:
            sections.append(md.read_text(encoding="utf-8"))
    return "\n\n".join(sections)
```

**Inputs.** `prompt_root` is the absolute path to the `prompt/` directory; `pbi_type` is one of `feature`, `bug`, `pr-feedback` (the `PBIType` literal values).

**Output.** A single UTF-8 string with topic sections separated by blank lines.

**Errors.** `PromptComposeError` on missing content for any topic or unknown PBI type. Surfaces fast at spawn time before Claude is launched.

**Scope.** Pure function. No side effects. No disk writes. No env reads. No caching.

**Module constants.**

```python
VALID_TYPES = frozenset({"feature", "bug", "pr-feedback"})
```

The same set is referenced by `tests/test_prompt_topic_shape.py`. Both import from the composer module so the set has a single home.

## Spawn integration

`ralph_executor/claude_spawn.py::_build_argv` changes:

```python
# before
"-p", f"Read ./prompt/PROMPT.md and work the PBI in {pbi_dir}. Follow the standing instructions."

# after
standing = compose_prompt(cfg.prompt_root, pbi.type)
# ...
"--append-system-prompt", standing,
"-p", f"Work the PBI in {pbi_dir}. Follow your standing instructions.",
```

`prompt_root` is computed at the call site: `prompt_root = _queue_repo_root(cfg) / "prompt"`. No `ExecutorConfig` change — the queue worktree path is already derived by `_queue_repo_root(cfg)` (see `loop.py`), and `prompt/` ships in the queue repo. Tests pass `prompt_root` explicitly to `compose_prompt` so they can use tmp_path-rooted fake trees.

**Two behavioural deltas.**

1. Standing prompt arrives as system context, not via a mid-session `Read` call. Claude cannot forget to read it.
2. The `./prompt/PROMPT.md` relative-path reference disappears. The current production line only resolves when ralph runs against itself; for an external target_repo without a `prompt/` directory, the Read would fail today. This change incidentally fixes that latent bug.

## Lifecycle

The composed prompt is per-spawn ephemeral:

- **Created** immediately before `subprocess.Popen` in `spawn_claude_p`.
- **Lifetime** equals the claude subprocess lifetime. String lives in argv of parent + child.
- **Destroyed** by GC after subprocess exit.
- **No disk artifact.** No temp file. No cleanup. No `.gitignore` entry.
- **Recompose every spawn.** Multi-iteration PBIs recompose on each spawn so source-file edits land without restart.

## Tests

### Existing tests reworked

- `tests/test_prompt_structure.py` — assert required headings/phrases against the **assembled** prompt for each PBI type, not against a single file.
- `tests/test_prompt_contents.py` — same pattern; load via composer per type and assert load-bearing phrases.
- `tests/test_prompt_smoke.py` — fixture copies `prompt/` topic tree instead of `prompt/PROMPT.md`. Spawn already uses `--append-system-prompt`; behaviour now matches production.

### New tests

- `tests/test_prompt_composer.py` — unit tests for `compose_prompt`:
  - feature uses root files for divergent topics
  - bug uses bug override files for `02-read-order`, `03-workflow`
  - feedback assembled prompt contains "Reviewer feedback" and "Elevated-attention" (inlined)
  - ordering matches numeric prefix
  - raises on empty topic folder
  - raises on unknown PBI type
- `tests/test_prompt_no_duplication.py` — anti-duplication lint:
  - For each topic with multiple override subfolders, pairwise compare non-trivial lines (length > 60).
  - Fails if substantial content appears in two override files of the same topic — should have been hoisted to root.
- `tests/test_prompt_topic_shape.py` — folder convention lint:
  - Every topic folder name matches `^\d{2}-[a-z][a-z0-9-]*$`.
  - Each topic has at least one root `.md` or at least one valid override subfolder.
  - Override subfolder names are in `{"bug", "pr-feedback"}`.
  - Override subfolders are non-empty.

### Test responsibility map

| Concern | Test file |
|---|---|
| Topic folder convention | `test_prompt_topic_shape.py` |
| Composer behaviour | `test_prompt_composer.py` |
| Assembled prompt has required headings + phrases per type | `test_prompt_structure.py` |
| Load-bearing wording (target_repo required, no stale phrases) | `test_prompt_contents.py` |
| No copy-paste between override files | `test_prompt_no_duplication.py` |
| End-to-end smoke | `test_prompt_smoke.py` |

## Invariants enforced by the design

1. **Single source of truth per rule.** Each rule lives in exactly one file. Override semantics are full replace (not merge). Anti-duplication lint deterministically fails the build if substantial lines appear in multiple type-overrides of the same topic. Drift between override files is structurally impossible.
2. **Folder structure encodes policy.** `ls prompt/` shows divergence at a glance. Topic-shape lint enforces the convention.
3. **Composer is mechanical.** One rule (root unless matching override) covers every case. No conditional logic embedded in source files.
4. **Topic-boundary discipline.** Each topic folder is either fully shared (root file only, no override subfolders) or fully overridden per type (root file = default + per-type subfolders). There is no partial override: an override file replaces the root for that type, never extends it. If a topic genuinely has shared content plus per-type tweaks, draw the topic boundary finer — split into two topics (one shared, one with per-type overrides). Enforces the single-source-of-truth invariant at the source-file level.

## Residual risks (one-time, retired after merge)

- **R1. Topic-content split errors.** Mechanical extraction is hand-work; risk of misplaced phrase or wrong topic boundary. Mitigation: rewritten structure + contents tests assert every required heading and phrase appears in each assembled type. Manual diff of assembled-feature vs current `PROMPT.md` as a sanity check before merge. Risk window is the split PR itself.
- **R2. Injection mechanism shift.** Production switches from "Read mid-session" to system-prompt injection. The smoke test already uses this path; production aligns with smoke, doesn't diverge from it. Manual 3-PBI smoke run (feature + bug + feedback) on real claude before declaring green. After 3 successful real iterations, risk is retired.

## Extension points the design enables

- **E1. New PBI types are additive.** Adding `spike` later means bumping `VALID_TYPES` in the topic-shape lint and creating override subfolders only in topics where spike diverges from the default. Topics where spike behaves like a feature get no subfolder — free reuse. The `VALID_TYPES` gate forces a deliberate sweep when a type is added; no silent fall-through.
- **E2. Per-topic versioning.** Topic folders are independent units. A future A/B of reviewer-tone variants lives in `07-reviewer/` (split out from workflow if needed) without touching other topics.
- **E3. Future composer enrichment.** Composer has a single stable signature. Adding caching, hot-reload, or per-target-repo overrides happens in one file with no source-format change.

## Edge cases — explicit decisions

| Case | Decision |
|---|---|
| Unknown PBI type | Composer raises `PromptComposeError`. No silent fall-through. |
| Topic with neither root nor matching override | Composer raises. |
| Empty topic folder | Topic-shape lint + composer both fail loudly. |
| Source file edited mid-iteration | In-flight subprocess holds prior argv string. Next spawn picks up the edit. No hot-reload. |
| Argv length on Windows | Smoke already passes ~14KB via `--append-system-prompt`. `subprocess.Popen` bypasses cmd.exe; the 8KB cmd limit does not apply. Fallback if a future split exceeds ~30KB: write to temp file, point flag at it. |
| Non-UTF-8 content | Composer reads with `encoding="utf-8"` explicitly; bad encoding raises at compose time. |

## Out of scope

- **Source-file location for multi-target.** Composer reads from `cfg.prompt_root`; default is the queue worktree's `prompt/`. Whether to relocate for multi-target operation is a separate question.
- **Per-target-repo overrides.** Target repos shipping their own prompt overrides (e.g. project-specific reviewer tone) is an E3 extension, not part of this split.
- **Composer caching.** Recompose per spawn is sub-millisecond; add caching only if iteration cadence justifies it.
- **Hot-reload of source edits.** Edits land on next spawn. No mid-iteration reload mechanism.

## Migration — single PR

Single PR. No staging needed:

1. Create `prompt/0N-topic/` folders. Split `prompt/PROMPT.md` content per the content-placement table above. Delete `prompt/PROMPT.md`.
2. Add `ralph_executor/prompt_composer.py` with `compose_prompt`.
3. Update `ralph_executor/claude_spawn.py::_build_argv` to compute `prompt_root` via `_queue_repo_root(cfg) / "prompt"`, call `compose_prompt`, and pass the result via `--append-system-prompt`; shrink `-p` instruction.
4. Rewrite three existing tests; add three new tests.
5. Update `prompt/README.md` to document the topic-folder convention + composer rule.

`PBI.type` already exists on the dataclass (`Literal["feature", "bug", "pr-feedback"]` in `ralph_executor/types.py`); no schema change needed.

In-flight PBIs (in `current/`) keep working — their next iteration spawns with the new mechanism over the same logical content.

## Estimated impact

| Iteration type | Today (lines) | After split (lines) | Saved |
|---|---|---|---|
| feature | 460 | ~335 | ~27% |
| bug | 460 | ~345 | ~25% |
| feedback | 460 | ~375 | ~18% |

Feedback saves least because elevated-attention + reviewer-tone (~75 lines) was previously paid by every iteration; now drops for feature/bug, stays only for feedback.
