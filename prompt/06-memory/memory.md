## Recording learnings (better-memory)

This iteration is one-shot: you have no memory across iterations except what you write to better-memory. The executor sets `BETTER_MEMORY_PROJECT` so your observations and retrievals scope correctly to the PBI's target repo — call the tools directly, don't pass `project=`.

### At iteration start

Before reading any PBI files, run both of these:

1. `mcp__better-memory__memory_retrieve` with a broad `query` describing the PBI's surface (e.g. the PBI title + the component or theme). Returns target-repo observations and reflections bucketed by `do` / `dont` / `neutral`.
2. `mcp__better-memory__knowledge_search` with a `query` describing the workflow context (e.g. `"ralph iteration"`, `"git worktree"`, `"PR review thread"`). Standards (cross-project workflow rules under `knowledge-base/standards/`) always surface regardless of project. Pass `project` if you also want project-specific knowledge surfaced.

Read both before deciding how to approach the PBI. Memory may be stale — if a recalled fact conflicts with what you see now, trust current state and let the synthesis layer correct the memory later.

### Inline triggers — observe AS the iteration progresses

Do not batch observations at the end. Write each one immediately, as the trigger fires:

- **After a fix commit lands** that represents a non-obvious gotcha, dependency, or convention. Call `memory_observe` with `outcome="failure"` recording what would have surprised a future reader.
- **Before this iteration exits** (success, push-and-PR, or classify-as-still-going). Sweep your commits and `HISTORY.md` for anything not yet recorded — the iteration may not return, so the sweep is your backstop. If a fact is already covered by an existing observation you retrieved at iteration start, skip it.

Type-specific triggers are documented in the workflow section for the relevant PBI type.

### What NOT to record

- Step-by-step iteration progress (`HISTORY.md` is the right home for that).
- Routine PBI mechanics (claiming, committing, opening a PR).
- Anything already documented in CLAUDE.md or PROMPT.md.
- Speculation — only record facts the iteration actually proved.

### Observation field defaults

Always set `outcome` explicitly. Fill `component` (the subsystem / module / package name) and `theme` (e.g. `bug`, `decision`, `gotcha`, `dependency`) when applicable. Do not embed the project name in `content` — the env-var scoping handles that.

### Autobug PBIs

If the PBI's `BUG.md` frontmatter contains `signature: <hex>`, this is an
autobug — captured automatically by ralph when an earlier iteration
crashed. Your `RALPH_AUTOBUG_DEPTH=1` env var prevents further autobug
emissions while you fix it. Treat it like any other bug PBI; the
signature field is for dedup, not for you to act on.
