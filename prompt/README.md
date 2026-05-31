# `prompt/` — Ralph's standing instructions

This directory holds the per-topic source files that compose Ralph's
standing instructions. The executor calls `compose_prompt` at spawn
time to assemble a per-PBI-type string, writes it to
`<queue>/.ralph/state/standing-prompt-<pbi.id>.md`, and tells Claude
to read that file via the `-p` directive. (Inline injection via
`--append-system-prompt` was tried and reverted — the ~16 KB assembled
feature prompt exceeds the Windows `cmd.exe` 8191-char limit. The
file-based mechanism works on every platform.) There is no single
`PROMPT.md` file — the content lives under topic folders.

## Layout

```
prompt/
  01-identity/identity.md          # shared
  02-read-order/
    read.md                        # default (feature)
    bug/read.md
    pr-feedback/read.md
  03-workflow/
    workflow.md                    # default (feature)
    bug/workflow.md
    pr-feedback/workflow.md
  04-stuck/stuck.md                # shared
  05-shipping/
    shipping.md                    # default (feature/bug)
    pr-feedback/shipping.md
  06-memory/memory.md              # shared
  07-forbidden/forbidden.md        # shared
  08-closing/closing.md            # shared
```

Topic folders carry a numeric prefix; alpha-sort gives the assembly
order. Override subfolder names match the `PBIType` literal values in
`ralph_executor/types.py` (`bug`, `pr-feedback`). `feature` has no
override — it is the default at the root of each topic.

## Composer rule

For each topic folder, the composer picks the override subfolder
matching the current PBI type if present, otherwise the topic root.
Override semantics are **strict replace** — the override owns the
whole topic's content; it does not merge with the root.

## How to iterate

1. **Edit** the relevant topic file. Locate the topic that owns the
   content you want to change — use the layout above as a map.
2. **Run lints + structural tests.**

   ```
   uv run pytest tests/test_prompt_topic_shape.py tests/test_prompt_no_duplication.py tests/test_prompt_structure.py tests/test_prompt_contents.py tests/test_prompt_composer.py -v
   ```

3. **Optional opt-in smoke** against the sample feature PBI:

   ```
   RALPH_PROMPT_SMOKE=1 uv run pytest tests/test_prompt_smoke.py -v
   ```

4. **Commit** with a conventional message:

   ```
   git add prompt/0N-topic/
   git commit -m "feat(prompt): <one-line summary of the behaviour change>"
   ```

## What goes in `prompt/` vs the PBI directory vs `INVESTIGATE.md`

| Information | Lives in | Why |
|---|---|---|
| "How Ralph works in general" — the loop, the read order, the safety lines, the PR shipping protocol | `prompt/0N-topic/` | Stable across PBIs and services. |
| "What this specific PBI is asking for" — context, acceptance criteria, the step-by-step plan | `.ralph/current/<PBI-id>/PBI.md` and `PLAN.md` (or `BUG.md`/`REPRODUCE.md`, or `FEEDBACK.md`) | Per-PBI. Written by humans (`ralph-new`) or by the sweep (feedback). |
| "How THIS service works" — key modules, log format, common gotchas | `docs/INVESTIGATE.md` in the service repo | Per-service. Written by the service's engineers. |

## Single-source-of-truth invariant

If you find the same rule appearing in two override files of the same
topic, the lint will fail — hoist that content to the topic root
instead, where it applies to all types. If a topic has shared content
plus per-type tweaks, draw the topic boundary finer: split into two
topics (one shared, one with overrides). Never duplicate.

## When to bump the structural test

The structural test (`tests/test_prompt_structure.py`) pins specific
headings and phrases per PBI type. If you rename a heading or drop a
phrase intentionally, update the test in the same commit.
