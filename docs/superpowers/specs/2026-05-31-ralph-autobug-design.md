# ralph autobug — design

**Date:** 2026-05-31
**Status:** Implemented (v1) — commits `8d5ba80..b629d26` on branch `ralph/RALPH-AUTOBUG-V1-IMPLEMENTATION`.
**Author:** Claude / gethin (brainstorming session)

## Summary

When the ralph executor or its Claude subprocess crashes, the executor emits a bug PBI to the ralph queue capturing what happened. Operator triages it like any other bug; the next ralph iteration that claims it works the fix.

Two trigger classes ship in v1: **Python crashes inside `ralph_executor/`** and **Claude subprocess crashes (non-zero exit or timeout)**. Cycle-detector signals and `STUCK.md` writes are deferred to v2.

The bug PBIs land in `inbox/` as normal `type: bug` PBIs (entry file `BUG.md`, required siblings `REPRODUCE.md` and `HISTORY.md`, matching the existing bug shape). They carry the required bug-PBI frontmatter (`id`, `type`, `severity`, `attempts`, `created_at`, `updated_at`, `target_repo`) plus autobug-specific extras (`signature`, `trigger_kind`, `occurrences`, `first_seen`, `last_seen`, optional `regression_of`, `triggering_pbi`, `ralph_sha`) that the dedup mechanism uses to find existing entries. There is no separate folder, no separate dashboard panel, no `source` marker — the presence of `signature: <hex>` distinguishes crash-captured bugs from human-authored ones.

Three independent fuses protect against runaway emission: a rate limit (5 writes per 10 min), a recursion guard (`RALPH_AUTOBUG_DEPTH` env var), and the dedup mechanism itself (which collapses repeat crashes onto an existing PBI). All emission is no-op-safe: an autobug failure never propagates back to the executor and never masks the original crash signal.

## Goals

1. Capture every executor and Claude-subprocess crash as a structured, deduplicated bug PBI in the ralph queue.
2. Surface crash recurrence (`occurrences` counter) so operator sees "this happened 47 times" rather than 47 duplicate PBIs.
3. Detect regressions: a previously-closed crash that reappears within 30 days is filed as a fresh PBI cross-linked to the original via `regression_of`.
4. Never mask the original crash signal — the operator's terminal and exit code always reflect the real crash, not an autobug-machinery failure.
5. Bounded blast radius: three independent fuses ensure a flapping bug cannot flood the queue.
6. No new operator surface: enabling autobug is one TOML key (default on); the bug PBIs look like any other bug PBI.

## Non-goals

- Cycle-detector signal triggers (`signature_recurrence`, `whack_a_mole`, etc.) — deferred to v2 after observing real autobug behaviour.
- `STUCK.md` triggers — STUCK is PBI-quality signal, not ralph-defect signal.
- Crashes inside target-repo code or CI failures — those flow through the existing PR-feedback sweep path.
- Separate dashboard / panel for autobugs — they are bug PBIs, listed alongside other bugs.
- Cross-instance dedup in multi-ralph deployments — addressed as a known limitation; revisit when multi-ralph lands.
- Auto-fixing the bug. Autobug only files; ralph's normal iteration loop works the fix.

## Architecture

### Module layout

A new package, mirroring the shape of `ralph_executor/sweep/`:

```
ralph_executor/autobug/
  __init__.py           # exports: detect_python_crash, detect_subprocess_crash
  detect.py             # trigger router: dedup + fuses + emit orchestration
  signature.py          # signature hashing (Python + subprocess variants)
  dedup.py              # scan queue state for matching signature
  compose.py            # build BUG.md + REPRODUCE.md (defensive, never raises)
  emit.py               # queue write + push_with_rebase
  fuses.py              # rate_check + recursion_check + rollup helpers
```

```
tests/executor/autobug/
  conftest.py
  test_signature.py
  test_signature_corpus.py     # real-stderr corpus regression test
  test_dedup.py
  test_fuses.py
  test_compose.py
  test_emit.py
  test_detect.py
  fixtures/stderr_corpus/      # bucketed real stderr blobs
tests/executor/autobug/integration/
  test_python_crash_e2e.py
  test_subprocess_crash_e2e.py
  test_recursion_guard_e2e.py
```

### Public API

Only two functions exported:

- `autobug.detect_python_crash(exc: BaseException, context: Context) -> None`
- `autobug.detect_subprocess_crash(exit_code: int, stderr: str, command: list[str], context: Context) -> None`

Both are no-op-safe: they never raise. `Context` is a frozen dataclass carrying `queue_root`, `state_dir`, `env`, `now`, `ralph_sha`, `bot_author_email`, and optionally `triggering_pbi_id`.

### Detection hook sites

Three places fire the public API:

1. **Top-level try/except in `cli.py`** — wraps `run_loop(cfg)`. Catches any unhandled Python exception that escapes the loop. Always-on; covers startup crashes, config-load crashes, and any uncaught exception in the loop body.
2. **`claude_spawn` error-event classifier** — after the existing `error` classification on subprocess non-zero exit or timeout, calls `detect_subprocess_crash`. Always-on; reuses the existing crash-classification path.
3. **Explicit wires at internal `except` blocks in `loop.py`** — at each catch site that converts a real error into a locally-handled outcome (e.g. `QueueLockError` → log + skip iteration), wire `detect_python_crash`. The wire is case-by-case; control-flow exceptions like `StopIteration` are NOT wired. The spec lists the wired sites in §"Integration with existing modules".

The detection wrapper at every site is itself try-wrapped so an autobug failure never propagates back into the executor's control flow.

### Data flow

A single emission flows through:

1. **Signature** — hash exception class + frames (Python) or exit code + normalised last-stderr-line (subprocess).
2. **Recursion check** — `RALPH_AUTOBUG_DEPTH >= 1` → log WARNING, return.
3. **Dedup lookup** — scan `inbox/`, `current/`, `pending-pr/` for matching signature; if not found, scan `done/` for matches closed within 30 days.
4. **Branch by dedup result**:
   - `bump_existing` → increment occurrences on existing PBI, append trace, write + push. No rate-limit check (bumps are cheap).
   - `reopen_regression` → check rate limit, then emit fresh PBI with `regression_of: <old-id>`.
   - `new` → check rate limit, then emit fresh PBI.
5. **Rate-limit overflow** → append to `<queue>/.ralph/state/autobug-suppressed.log` and return.

## Bug PBI directory and frontmatter

Autobug PBIs ARE bug PBIs. They use the existing bug shape (entry file `BUG.md`, required siblings per `prompt/02-read-order/bug/read.md`) so the executor's queue parser (`ralph_executor/queue/filesystem.py::parse_pbi_directory`) and the bug workflow (`prompt/03-workflow/bug/workflow.md`) both accept them with one minor amendment to the workflow (§"Bug workflow amendment" below).

### Directory

```
inbox/autobug-a3f2c9-001/
  BUG.md          # entry file — title, stacktrace, env snapshot, recent events, triggering PBI ref
  REPRODUCE.md    # observed stacktrace + skeleton; explicitly marked "starting point, not a recipe"
  HISTORY.md      # initially empty (or excerpt from triggering PBI when autobug_include_history=true)
```

The set matches the bug PBI requirement: `BUG.md`, `REPRODUCE.md`, `HISTORY.md`. No `INVESTIGATE.md` is shipped — that's a per-service repo-root file maintained by the service owner, not a per-PBI artefact.

### Frontmatter on BUG.md

Required fields (enforced by `parse_pbi_directory`):

```yaml
---
id: autobug-a3f2c9-001
type: bug
severity: critical              # python_crash → critical, subprocess_crash → high (TOML-tunable)
status: inbox                   # rewritten by movements._rewrite_status on every move
attempts: 0                     # fresh PBI; bumps preserve existing value; reopens reset to 0
created_at: 2026-05-31T14:23:01Z
updated_at: 2026-05-31T14:23:01Z
target_repo: <ralph repo URL>   # PBI-1 schema requirement; parser is lenient but downstream code expects it
---
```

Autobug-specific extras (parser ignores unknown keys; line-by-line `_rewrite_status` preserves them across moves):

```yaml
signature: a3f2c9d8e1bc...      # full SHA-256 hex; id uses 6-char prefix
trigger_kind: python_crash      # | subprocess_crash
occurrences: 1                  # bumped in place on dedup match
first_seen: 2026-05-31T14:23:01Z
last_seen: 2026-05-31T14:23:01Z
regression_of: null             # set on reopen from done/, otherwise null
triggering_pbi: WI-247          # null for startup / pre-claim crashes
ralph_sha: 51cc97a              # ralph executor SHA at crash time
```

`severity` values are constrained to the existing `Severity` Literal: `critical` / `high` / `normal` / `low`. `type` must be `bug` to match the `BUG.md` entry-file convention (parser raises `QueueError` on type/entry-file mismatch).

### REPRODUCE.md shape

The autobug emits a REPRODUCE.md with an explicit "starting point" header. It contains the observed crash artefacts AND a marker telling the iteration that converting these into runnable reproduction commands IS part of the work, not grounds for STUCK.md.

```markdown
# REPRODUCE — autobug-a3f2c9-001

> AUTOBUG NOTE: This bug was auto-captured at crash time. The content
> below is what we observed, NOT a confirmed reproduction recipe. Your
> iteration's job is to convert these observations into runnable
> reproduction commands and append them to this file. If after
> investigation the crash is genuinely non-deterministic (race
> condition, OOM under load, network blip), write STUCK.md per the
> bug workflow's contradictory-evidence trigger — do not stack
> speculative fixes.

## Observed

Trigger: python_crash (or subprocess_crash)
Exit code: <N>  (subprocess only)

### Stacktrace

```
<full traceback>
```

### Environment snapshot

- OS: <platform>
- Python: <version>
- Ralph SHA: <sha>
- Claude CLI: <version>
- Relevant env vars (secrets redacted): <list>

## Suggested reproduction approach

- Inspect <throw_file>:<throw_line> for the failing call path
- Reproduce the env conditions captured above
- See triggering PBI <triggering_pbi> in done/ for context (HISTORY excerpt is in HISTORY.md if available)
```

### Bug workflow amendment

`prompt/03-workflow/bug/workflow.md` step 2 currently reads:

> Reproduce. Run the commands in `REPRODUCE.md`. Confirm you observe the failure signature documented in `BUG.md`. If you cannot reproduce, write `STUCK.md` ("cannot reproduce: <what I saw instead>") and stop.

Amend step 2 to add a signature-aware conditional:

> **If `BUG.md` frontmatter contains `signature: <hex>`** (autobug-emitted), REPRODUCE.md may contain only observed artefacts, not runnable commands. Your iteration's first task is to build the reproduction from the stacktrace + env snapshot. The "cannot reproduce → STUCK" rule applies only after a documented investigation attempt, not on initial read.
>
> **Otherwise** (human-authored bug): the existing rule stands — REPRODUCE.md must contain runnable commands; if they don't fire, STUCK.md immediately.

This is a one-paragraph amendment to one file in `prompt/03-workflow/bug/workflow.md`. No new prompt topic folder, no parser changes.

### ID scheme

`autobug-<short-sig>-<seq>` where:
- `<short-sig>` = first 6 hex chars of full signature hash (16M namespace; collision rate negligible at expected volume).
- `<seq>` = `001`, `002`, ... incremented when a same-`<short-sig>` PBI already exists in `done/` more than 30 days old (regression-from-old or short-sig collision case).

The full `signature` lives in frontmatter; the short prefix is for filesystem readability only. Dedup always queries the full `signature`, never the short prefix.

### Bump semantics (open-state match)

1. Read existing PBI's BUG.md frontmatter (line-based, not via the PBI dataclass — preserves autobug extras).
2. Increment `occurrences`; update `last_seen`. `attempts` left untouched (only the iteration loop bumps that).
3. Append new stacktrace + timestamp under a `## Trace <N>` heading in `BUG.md` body.
4. Commit + push via existing `push_with_rebase` as `bot_author_email`.
5. Do NOT emit a new PBI.

### Reopen semantics (done/ match within 30 days)

1. Generate a fresh PBI ID with `<seq>` incremented.
2. Set `regression_of: <old-id>` in new frontmatter; set `attempts: 0`, `created_at` / `updated_at` = now.
3. `occurrences: 1`, `first_seen` = now.
4. Write to `inbox/` (NOT moved from done/) — fresh entry, cross-linked.
5. Rationale: the old PBI carries its own `HISTORY.md` from the iteration that closed it. Mixing old history with a new occurrence is confusing.

### Roundtrip safety

`ralph_executor/queue/movements.py::_rewrite_status` is line-based: it scans the frontmatter block, replaces `status:` and `updated_at:` lines in place, and **preserves every other line verbatim**. It does NOT round-trip through the `PBI` dataclass and so does NOT lose unknown frontmatter keys.

This means autobug's extras (`signature`, `occurrences`, etc.) survive `move_inbox_to_current`, `move_current_to_pending_pr`, and `move_*_to_done`. Verified by reading `_rewrite_status` directly.

The bump and reopen paths bypass `_rewrite_status` entirely — autobug owns its own writes to `BUG.md` and uses the same line-level technique to update `last_seen` and `occurrences` without losing other fields.

If a future change to movements switches to dataclass-roundtrip serialisation, autobug extras will silently disappear. Mitigation: an integration test (`test_roundtrip_preserves_extras`) writes an autobug PBI, runs it through `move_inbox_to_current` → `move_current_to_pending_pr` → `move_pending_pr_to_done`, then asserts every autobug-specific frontmatter key is still present in the final file.

## Signature computation

### Python crash

```python
def python_crash_signature(exc: BaseException) -> str:
    throw, caller = _two_deepest_user_frames(exc.__traceback__)
    payload = (
        f"{type(exc).__qualname__}:"
        f"{throw.filename}:{throw.name}:{throw.lineno}|"
        f"{caller.filename}:{caller.name}:{caller.lineno}"
    )
    return hashlib.sha256(payload.encode()).hexdigest()
```

`_two_deepest_user_frames` walks the traceback and selects the two deepest frames whose filename is under `ralph_executor/`. Stdlib and site-packages frames are skipped.

**Edge cases:**
- Only one user frame found (crash in `cli.main` directly, or module-level) → `caller=_SENTINEL`; payload becomes `<ExcClass>:<throw>|<none>`.
- No user frames found (pure stdlib crash) → fall back to `<ExcClass>:<top_stdlib_frame>|<none>` and log WARN that signature is unstable.

Both edge cases have explicit unit tests.

### Subprocess crash

```python
def subprocess_crash_signature(exit_code: int, stderr: str) -> str:
    last_line = _last_nonempty_line(stderr)
    normalised = _normalise(last_line)
    payload = f"{exit_code}:{normalised}"
    return hashlib.sha256(payload.encode()).hexdigest()
```

### Normalisation pattern set

`_normalise` applies these regex substitutions **in order, longest/most-specific first**, replacing each match with a fixed token so the normalised string stays human-readable:

| # | Pattern | Token | Reason |
|---|---|---|---|
| 1 | `[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}` | `<UUID>` | Request / session IDs |
| 2 | `\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})?` | `<TS>` | ISO timestamps |
| 3 | `\b\d{10,}\b` | `<EPOCH>` | Epoch seconds (≥10 digits) |
| 4 | `(?:[A-Z]:\\|/)[^\s:]+` | `<PATH>` | Absolute paths (Windows + POSIX) |
| 5 | `ralph/[A-Z0-9][A-Z0-9_-]*` | `<BRANCH>` | Per-PBI branches |
| 6 | `\b(WI-\d+\|autobug-[a-f0-9]+-\d+)\b` | `<PBI>` | PBI IDs |
| 7 | `\b[0-9a-f]{7,40}\b` | `<SHA>` | Git commit SHAs |
| 8 | `0x[0-9a-f]+` | `<ADDR>` | Hex memory addresses |
| 9 | `:line \d+\|at line \d+` | `:line <N>` | Line-number suffixes |

Patterns 1, 2, 4, 5 must run before 7 — UUIDs and SHAs share hex characters; paths and timestamps would partially match the SHA pattern.

### Corpus regression test (BLOCKING)

`tests/executor/autobug/test_signature_corpus.py` reads `fixtures/stderr_corpus/<bucket>/*.txt` and asserts:

- All files within the same bucket directory hash to the same signature.
- Files in different buckets hash to different signatures.

Buckets are operator-curated: a one-off `scripts/build_autobug_corpus.py` walks local `events.db` files, extracts `error`-class events, dumps stderr blobs into `fixtures/stderr_corpus/raw/`. Operator (human) sorts blobs into bucket subdirectories based on root cause before merging.

Without a corpus the test runs with synthetic placeholders. The spec requires the operator to produce a real corpus before raising `autobug_rate_max` above the default 5 — running blind on synthetic fixtures is acceptable for the first week but not for steady-state operation.

## Dedup

### Lookup function

```python
@dataclass(frozen=True)
class DedupResult:
    kind: Literal["new", "bump_existing", "reopen_regression"]
    existing_pbi_id: str | None = None

def lookup(signature: str, queue_root: Path, now: datetime) -> DedupResult:
    for state in ("inbox", "current", "pending-pr"):
        match = _find_by_signature(queue_root / state, signature)
        if match:
            return DedupResult("bump_existing", existing_pbi_id=match.id)
    cutoff = now - timedelta(days=30)
    match = _find_by_signature_since(queue_root / "done", signature, cutoff)
    if match:
        return DedupResult("reopen_regression", existing_pbi_id=match.id)
    return DedupResult("new")
```

`_find_by_signature` reads frontmatter only — no body scan. Cost is `O(N_open_pbis)` filesystem reads. At queue sizes <1k this is sub-second.

`_find_by_signature_since` filters by `closed_at` (the queue's existing `move_to_done` stamp). Retention requirement: `done/` PBIs must survive at least 30 days for regression detection to function. If queue cleanup later prunes done/ aggressively, this assumption breaks silently.

No persistent index. If queue size grows past ~10k, revisit with SQLite keyed on signature — premature for v1.

## Fuses

### Recursion guard

```python
def recursion_check(env: Mapping[str, str]) -> bool:
    """True = allowed to emit; False = recursion guard tripped."""
    depth = int(env.get("RALPH_AUTOBUG_DEPTH", "0"))
    return depth < 1
```

`claude_spawn._build_env` sets `RALPH_AUTOBUG_DEPTH=1` in the spawn env when the claimed PBI's frontmatter has a `signature:` key. The subagent inherits the marker; any crash detection inside the subagent's subprocess sees `depth=1` and refuses.

The executor process itself working an autobug PBI does NOT have `RALPH_AUTOBUG_DEPTH` set — only the subagent does. So if `loop.py` crashes while handling an autobug PBI, autobug correctly emits a new bug for the loop crash. This is intentional: the recursion guard exists to stop the *subagent* from triggering nested autobugs while trying to fix an existing one, not to stop the executor from reporting its own bugs.

**Fragility:** the contract relies on env-var inheritance through the spawn chain. A future refactor that strips spawn env, or an MCP tool the subagent shells out to that strips env, would break the guard. A dedicated regression test (`test_recursion_marker_propagates`) introspects the actual env the inner subprocess receives — do not rely on manual review of env-merging code.

### Rate limit

```python
@dataclass
class RateLimitConfig:
    max_writes: int = 5
    window: timedelta = timedelta(minutes=10)

def rate_check(state_dir: Path, cfg: RateLimitConfig, now: datetime) -> bool:
    """True = under cap; False = over cap, caller should rollup."""
    log_path = state_dir / "autobug-emissions.log"
    recent = _read_timestamps_after(log_path, now - cfg.window)
    return len(recent) < cfg.max_writes
```

Storage: `<queue>/.ralph/state/autobug-emissions.log`. Append-only file, one ISO timestamp per line. Every new/reopen emit appends; truncation runs inline in `rate_check` when the file grows past 1000 lines (keeps entries newer than the window).

**Bumps bypass the rate limit.** A 500-crash flap on an existing signature does 500 cheap appends to one PBI — no queue inflation. Only `new` and `reopen_regression` consume rate-limit budget.

### Rollup on overflow

When `rate_check` returns False for a `new` or `reopen_regression`:

1. Append to `<queue>/.ralph/state/autobug-suppressed.log`: `<iso>\t<signature>\t<short_message>`.
2. Log WARNING with the would-be signature.
3. Return without emitting.

Operator reviews the suppressed log to spot flapping bugs that exceeded the cap.

### Order of operations (`detect.py`)

```python
def detect_python_crash(exc, context):
    sig = signature.python_crash_signature(exc)
    if not fuses.recursion_check(context.env):
        log.warning("autobug recursion guard tripped, signature=%s", sig)
        return
    result = dedup.lookup(sig, context.queue_root, context.now)
    if result.kind == "bump_existing":
        return emit.bump(result.existing_pbi_id, exc, context)  # no rate limit
    if result.kind == "reopen_regression":
        if not fuses.rate_check(context.state_dir, cfg, context.now):
            return fuses.rollup(context.state_dir, sig, "rate-limited reopen")
        return emit.reopen(result.existing_pbi_id, exc, sig, context)
    if not fuses.rate_check(context.state_dir, cfg, context.now):
        return fuses.rollup(context.state_dir, sig, "rate-limited new")
    return emit.new(sig, exc, context)
```

## Defensive composer and error handling

### Composer never raises

Every section of `BUG.md` is wrapped in `_safe`. If a section fails, the section is replaced with a placeholder; the rest of the body still composes.

```python
def build_bug_md(exc, context) -> str:
    parts = ["# autobug — {}".format(_safe(lambda: _short_title(exc), "unknown crash"))]
    parts.append(_section("Stacktrace", _safe(lambda: _format_traceback(exc), "<traceback unavailable>")))
    parts.append(_section("Environment", _safe(lambda: _env_snapshot(context), "<env unavailable>")))
    parts.append(_section("Triggering PBI", _safe(lambda: _pbi_ref(context), "<no PBI context>")))
    parts.append(_section("Recent events", _safe(lambda: _last_events(context, n=20), "<event log unavailable>")))
    return "\n\n".join(parts)

def _safe(fn, fallback):
    try:
        return fn()
    except Exception as inner:
        log.warning("autobug.compose section failed: %s", inner)
        return fallback
```

**Frontmatter** uses the same pattern with stricter defaults. The load-bearing fields (`id`, `type`, `signature`) cannot fall back to placeholders — dedup depends on them. If `signature` computation itself fails, emission is aborted (see "Abort conditions" below).

### Resilience-mask risk

A defensive composer can silently degrade for weeks without anyone noticing — every PBI shows `<env unavailable>` but nothing crashes loudly. Mitigation: when `_safe` falls back, emit a structured log line `autobug.compose.section_failed{section=<name>}`. Operator dashboards / log aggregators can alert on non-zero rates.

### Nested try/except at every hook site

Outer try/except in `cli.py`:

```python
try:
    run_loop(cfg)
except BaseException as original_exc:
    try:
        autobug.detect_python_crash(original_exc, build_context(cfg))
    except BaseException as autobug_exc:
        log.error(
            "AUTOBUG FAILED while handling original crash. "
            "Original: %s. Autobug: %s",
            original_exc, autobug_exc,
            exc_info=original_exc,  # show the ORIGINAL traceback
        )
    raise original_exc  # always re-raise the original
```

Two guarantees:

1. **Original always logged with its own traceback.** `exc_info=original_exc` overrides Python's "most recent exception" behaviour so the terminal shows the real crash.
2. **Original always re-raised.** Exit code reflects the real crash, not the autobug failure.

The same nested-try pattern wraps every internal wire site in `loop.py`.

### Abort-emission conditions

`detect.py` has hard aborts where emission is impossible. Each logs WARNING with enough context to reconstruct what would have been emitted:

| Condition | Reason |
|---|---|
| Signature computation fails | Dedup requires signature |
| Queue root not resolvable | Nowhere to write |
| `bot_author_email` missing | Commits would lack identity; downstream filtering by bot-origin (sweep, future cycle-detector autobug-event filter) breaks |
| `RALPH_AUTOBUG_DEPTH >= 1` | Recursion guard |
| Rate limit tripped for new/reopen | Flood protection (rollup, not abort) |

### Emit-step failure

`emit.py` does the queue write + `push_with_rebase`. Every failure is caught, logged, appended to the suppressed log, and `detect_python_crash` returns normally. No emit failure propagates out.

| Failure | Action |
|---|---|
| `git add` fails | Catch, log, suppressed-log, return |
| `push_with_rebase` fails after retries | Catch, log, suppressed-log, return. PBI directory left in queue worktree; next iteration's `commit_all` picks it up. |
| Disk full on PBI write | Catch, log, return. Partial directory caught by existing dirty-worktree handling. |

## Integration with existing modules

| Module | Change | Contract |
|---|---|---|
| `ralph_executor/cli.py` | Wrap `run_loop(cfg)` in outer try/except per §"Defensive composer". | autobug call is no-op-safe; original exception always re-raised |
| `ralph_executor/loop.py` | Wire `autobug.detect_python_crash` into N internal `except` blocks (specific sites: `claim_next_pbi` failure path, sweep invocation, post-iteration error classification — exact list determined during implementation). | Wires never propagate exceptions; existing control flow unchanged |
| `ralph_executor/claude_spawn.py` | After `error` event classification, call `autobug.detect_subprocess_crash`. | Subprocess crash detection orthogonal to existing classification |
| `ralph_executor/claude_spawn.py::_build_env` | Set `RALPH_AUTOBUG_DEPTH=1` when claimed PBI frontmatter has a `signature:` key. | Recursion guard activates in subagent |
| `ralph_executor/config.py` | Add TOML keys per §"Config defaults" below. | Loaded via existing config layer; no new mechanism |
| `ralph_executor/safety/events.py` | Add new EventType: `autobug_emitted` with signature payload. | Cycle detector can filter on this in v2 |
| `ralph_executor/queue/movements.py` | No change. Autobug uses existing `write_pbi_to_inbox` + `update_pbi_frontmatter` helpers. | Reuses existing queue write surface |
| `prompt/03-workflow/bug/workflow.md` | Add signature-aware conditional to step 2 (Reproduce) per §"Bug workflow amendment". | Autobug PBIs whose REPRODUCE.md contains only observed artefacts are not auto-STUCK'd on first read |
| `prompt/06-memory/memory.md` | Add one line: "If you see frontmatter `signature: <hex>` on the PBI, this is an autobug — your `RALPH_AUTOBUG_DEPTH=1` env var prevents further autobug emissions while you fix it." | Iteration's Claude is aware of the autobug shape |

### What does NOT change

- `sweep/feedback_pbi.py` — autobug is structurally separate; no shared code in v1.
- `safety/cycle_detector.py` — no logic change. `autobug_emitted` is just a new EventType variant; existing rules do not consume it.
- `worktree.py` — autobug writes inside the queue worktree only.
- `prompt_composer.py` — no new topic folder. Autobug is transparent to the prompt.
- `ralph-report` / `ralph-status` — no v1 changes. Operator filters via grep or signature column appearing naturally.

### Concern 3 rolled in

The existing `bot_author_email` silent-skip in sweep (`loop.py:140-145`) becomes a more serious gap once autobug also depends on it. This spec includes the fix:

- Add startup validation in `load_config` (or a one-time banner log in `cli.py`): if `bot_author_email` is missing AND any feature that depends on it is enabled (sweep, autobug), log a clear startup error and continue.
- Per-feature WARN-and-skip behaviour is preserved — autobug aborts emission per the abort table above, sweep continues to skip per existing behaviour.

## Testing strategy

### Test layout

```
tests/executor/autobug/
  conftest.py
  test_signature.py            # hash determinism + frame selection
  test_signature_corpus.py     # real-stderr regression (BLOCKING for ship)
  test_dedup.py                # state-window scanning
  test_fuses.py                # recursion + rate limit
  test_compose.py              # defensive field building + section_failed metric
  test_emit.py                 # queue write + push semantics
  test_detect.py               # end-to-end orchestration with mocked emit
  fixtures/stderr_corpus/<bucket>/*.txt
tests/executor/autobug/integration/
  test_python_crash_e2e.py
  test_subprocess_crash_e2e.py
  test_recursion_guard_e2e.py
```

### Key fixtures

- `fake_context` — `Context` with tmp queue + state dir + frozen `now`.
- `python_crash` — real exception with real traceback at a known frame.
- `fake_pbi_inbox` — writes existing autobug PBI with known signature for dedup tests.

Frozen clock via `Context.now` — never call `datetime.now()` inside autobug code, always read from context.

### Load-bearing tests for the safety guarantees

These are the tests that MUST pass for the "autobug doesn't eat the original signal" property:

1. `test_compose.py::test_env_snapshot_failure_falls_back` — pass `cfg=None`, assert body contains `<env unavailable>` and stacktrace section still present.
2. `test_compose.py::test_section_failed_metric_emitted` — assert each fallback emits the structured `autobug.compose.section_failed{section=...}` log line.
3. `test_detect.py::test_signature_failure_aborts` — monkeypatch signature to raise, assert no emit + WARN logged.
4. `test_detect.py::test_emit_failure_does_not_propagate` — monkeypatch `emit.new` to raise `GitPushError`, assert `detect_python_crash` returns normally + suppressed log appended.
5. `test_cli.py::test_outer_handler_reraises_original` — trigger `RuntimeError` from `run_loop`, monkeypatch `autobug.detect_python_crash` to raise `TypeError`, assert `sys.exit` propagates the original `RuntimeError` and log contains the original traceback.
6. `test_recursion_guard_e2e.py::test_recursion_marker_propagates` — claim an autobug PBI, spawn subagent, introspect the actual env the subagent's subprocess receives, assert `RALPH_AUTOBUG_DEPTH=1` is present.
7. `test_signature_corpus.py::test_buckets_collapse_correctly` — for each bucket in `fixtures/stderr_corpus/`, assert all files hash to the same signature; assert cross-bucket signatures differ.
8. `test_roundtrip_preserves_extras` (integration) — write an autobug PBI with all extras, run it through `move_inbox_to_current` → `move_current_to_pending_pr` → `move_pending_pr_to_done`, assert every autobug-specific frontmatter key (`signature`, `occurrences`, `trigger_kind`, `first_seen`, `last_seen`, `regression_of`, `triggering_pbi`, `ralph_sha`) is still present in the final file verbatim. Guards against a future movements change that switches to dataclass-roundtrip serialisation.

### TDD discipline

Per `superpowers:test-driven-development` and the project's subagent-driven-development standard:

- Red → green → refactor per task.
- Three subagents per task (implementer, spec reviewer, code-quality reviewer). No task complete until both reviews pass.
- Integration tests follow the same discipline: write the failing wiring test before adding the hook in `cli.py`.

## Config defaults

| TOML key | Default | Effect |
|---|---|---|
| `autobug_enabled` | `true` | Autobug runs |
| `autobug_rate_max` | `5` | Max emits per window |
| `autobug_rate_window_minutes` | `10` | Rate window |
| `autobug_dedup_done_window_days` | `30` | Reopen threshold |
| `autobug_severity_python_crash` | `critical` | Python crash severity |
| `autobug_severity_subprocess_crash` | `high` | Subprocess crash severity |
| `target_repos.<repo>.autobug_include_history` | `false` | HISTORY excerpt suppressed by default |

Operator can disable entirely with `autobug_enabled = false` — all wires become no-ops, no new dependencies, no new failure modes.

## Assumptions and known limitations

These were surfaced during brainstorming. None block v1 ship; all are documented for honest disclosure.

1. **Multi-ralph rate-limit + dedup race — LIVE as of 2026-06-01.** Per-ralph state means N ralphs hitting the same flapping crash can emit `N × 5` PBIs per 10 min. Dedup also races: first to push wins, others rebase and discover the match on re-check (existing `push_with_rebase` machinery handles the rebase correctly; the race window between detect and push lets duplicates through). Multi-ralph (PR #69, commit `01312f1`) landed earlier than this spec anticipated. Mitigations available: (a) accept the N×cap as the operator-visible budget and document it; (b) migrate `autobug-emissions.log` from the per-instance state dir into the queue branch (gated by `push_with_rebase` for ordering) — bigger change, defer to v2. v1 ships with (a); operator sees N copies in the suppressed log when a flap is fleet-wide.
2. **Signature granularity uses throw + immediate caller frames.** Catches most cases. Edge case: a wrapper that re-throws — top user frame is the wrapper. Mitigated partially by including two frames; not perfectly addressed.
3. **Subprocess normalisation is regex-driven.** New failure modes with un-normalised non-determinism will fragment or collapse incorrectly. Corpus regression test catches known cases; unknown cases require corpus expansion.
4. **`done/` retention requirement.** Regression detection requires `done/` PBIs to survive at least 30 days. A future queue-cleanup PBI that prunes more aggressively breaks regression detection silently.
5. **Recursion guard relies on env-var inheritance.** A future refactor that strips spawn env breaks the guard. Mitigated by `test_recursion_marker_propagates`.
6. **Defensive composer can silently degrade.** Mitigated by `autobug.compose.section_failed` metric; operator must wire alerting.
7. **Severity heuristic is opinionated.** Defaults are guesses, not data-driven. Knobs ship in v1 config table; operator tunes after observing.
8. **Short-sig collision space.** 6 hex chars = 16M namespace. Negligible at expected volume; `<seq>` suffix handles same-sig collisions; different-sig short-prefix collisions handled by `<seq>` increment as well.
9. **Frontmatter-roundtrip preservation depends on movements' line-based rewriter.** Autobug extras survive `_rewrite_status` because the rewriter is line-by-line and doesn't deserialise unknown keys. A future refactor that switches to dataclass-roundtrip serialisation silently drops every autobug-specific field. Mitigated by the `test_roundtrip_preserves_extras` integration test (load-bearing test #8). If that test is ever weakened or removed, autobug dedup breaks silently on the next move.

## Rollout posture

Spec-recommended rollout (not v1 scope, just guidance):

1. Land with `autobug_enabled = true`, `autobug_rate_max = 5` (conservative).
2. Run for one week. Watch suppressed-log growth and PBI emission rate.
3. Run `scripts/build_autobug_corpus.py` against the now-populated `events.db`. Curate buckets. Add to `fixtures/stderr_corpus/`.
4. Adjust `_normalise` patterns based on bucket signal. Add regression tests.
5. If emission rate is healthy and suppressed log is sparse, consider raising `autobug_rate_max`.
6. After steady-state operation, plan v2 — cycle-signal triggers.

## Future work (v2)

Out of scope for v1. Listed so the spec doesn't pretend they don't exist:

- **Cycle-signal triggers.** When `signature_recurrence` or `whack_a_mole` fires, optionally emit an autobug. Needs suppress-list to filter known-noisy signals (e.g. `package.json` thrashing).
- **Multi-ralph coordination.** Move rate-limit log and dedup index into the queue itself so N ralphs share state. Resolves Assumption #1.
- **Ralph-report panel.** Dedicated dashboard panel showing autobug occurrence count, first/last seen, signature top-N. Defer until operator demonstrates the need from real usage.
- **Compose with dead-ends log (candidate A in the comparison doc).** When an autobug closes, contribute its root-cause / fix lines to `docs/RALPH-LESSONS.md` for cross-PBI reuse.
- **STUCK-trigger filtering.** STUCK with `contradictory_evidence` or `repeating_yourself` could be a defect signal; needs filtering to exclude `safety_line_crossed` (which is a PBI-quality issue).

## Open questions

None blocking ship. Two for v2 planning:

- Should `autobug_emitted` events feed back into cycle detector (so a crash-spam triggers `whack_a_mole`-style cycle signals)?
- Should the rate-limit storage migrate to the queue (for multi-ralph) before or after multi-ralph itself lands?
