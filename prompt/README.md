# `prompt/` — Ralph's standing instructions

This directory holds `PROMPT.md`, the single file the executor injects
into every `claude -p` invocation. Iterating on Ralph's behaviour
largely means iterating on this file.

## What `PROMPT.md` is

- The standing instructions Ralph reads at the top of every iteration.
- **Static across PBIs.** The per-PBI variability lives in the PBI
  directory under `.ralph/current/<PBI-id>/`. `PROMPT.md` is the same
  every time.
- **Token cost matters.** Ralph pays the read cost on every iteration.
  Keep the file as short as it needs to be — no more.
- **No placeholders.** The file ships to Claude as-is. Anything
  looking like `TODO` / `TBD` / `<placeholder>` / `FIXME` causes the
  structural test (`tests/test_prompt_structure.py`) to fail.

## How to iterate

The basic loop is: edit → structural test → manual smoke → optional
subprocess smoke → ship.

### 1. Edit `PROMPT.md`

Edit the file. Keep the section headings exactly as they are — the
structural test pins them. Adding sections is fine; removing or
renaming an existing required heading breaks the test.

Required section headings (the structural test asserts each one
appears verbatim):

- `Who you are`
- `What you read`
- `How to work a feature PBI`
- `How to work a bug PBI`
- `How to work a PR-feedback PBI`
- `When to write STUCK.md`
- `How to ship a PR`
- `Elevated-attention categories`
- `Reviewer feedback — treat respectfully, challenge with reasoning`
- `What you never do`

### 2. Run the structural test

```
uv run pytest tests/test_prompt_structure.py -v
```

This is fast (file read + string checks). It catches missing headings,
missing required phrases, accidental placeholders, and size drift. It
does NOT verify that Ralph behaves correctly — that is the smoke
test's job.

### 3. Manual smoke against a sample

Pick a Plan 1 sample (e.g. `samples/feature-WI-1234/`) and spawn
Claude against it by hand:

```
# Copy the sample into a tmp .ralph/current/ layout
mkdir -p /tmp/ralph-smoke/.ralph/current/WI-1234
cp -r samples/feature-WI-1234/* /tmp/ralph-smoke/.ralph/current/WI-1234/

# Run Claude with the prompt
cd /tmp/ralph-smoke
claude -p --prompt-file <repo>/prompt/PROMPT.md "Begin iteration."
```

Read the transcript. Did Ralph:

- Read PROMPT.md and the PBI files in the order the prompt mandates?
- Pick up the first unchecked step in `PLAN.md`?
- Stop after one step (rather than trying to do all five)?
- Write a sensible `HISTORY.md` append?

If any of those are wrong, the prompt is wrong. Edit and re-smoke.

### 4. Optional subprocess smoke (`tests/test_prompt_smoke.py`)

An automated version of the manual smoke lives at
`tests/test_prompt_smoke.py`. It is skipped by default because
spawning Claude Code as a subprocess can be flaky on some hosts and
is slow. Run it locally when you want it:

```
RALPH_PROMPT_SMOKE=1 uv run pytest tests/test_prompt_smoke.py -v
```

See the test file's docstring for the prerequisites
(`ANTHROPIC_API_KEY` set, `claude` CLI on `$PATH`, network access).

### 5. Ship

Once the structural test is green and you have manually smoked the
change against at least one sample, commit with a conventional message:

```
git add prompt/PROMPT.md
git commit -m "feat(prompt): <one-line summary of the behaviour change>"
```

## What goes in `PROMPT.md` vs the PBI directory vs `INVESTIGATE.md`

Three places hold instructions for Ralph. Knowing which is which keeps
each one focused.

| Information | Lives in | Why |
|---|---|---|
| "How Ralph works in general" — the loop, the read order, the safety lines, the PR shipping protocol | `prompt/PROMPT.md` | Stable across PBIs and services. |
| "What this specific PBI is asking for" — context, acceptance criteria, the step-by-step plan | `.ralph/current/<PBI-id>/PBI.md` and `PLAN.md` (or BUG/REPRODUCE, or FEEDBACK) | Per-PBI. Written by humans (`ralph-new`) or by the sweep (feedback). |
| "How THIS service works" — key modules, log format, common gotchas | `docs/INVESTIGATE.md` in the service repo | Per-service. Written by the service's engineers. |

If you find yourself adding per-service content to `PROMPT.md`, stop
and put it in `docs/INVESTIGATE.md` instead. If you find yourself
adding per-PBI content, put it in the PBI directory.

## When to bump the structural test

The structural test pins specific section headings and phrases. If you
rename a heading or drop a phrase intentionally, update the test in
the same commit — the test exists to catch accidental drift, not to
prevent deliberate changes. A diff that touches both `PROMPT.md` and
`tests/test_prompt_structure.py` is normal for behaviour changes.
