# Add `target_repo` field to PBI frontmatter

**Date:** 2026-05-27
**Scope:** Add a required `target_repo` field (HTTPS URL string) to the PBI frontmatter schema. Migrate all 13 existing PBIs. Update `ralph-add` to auto-derive the field from the repo's git origin remote (overridable via new `--target-repo` flag). Field is metadata-only this PBI — ralph's loop does not yet read it. Foundation for the multi-target generic-processor work.
**Status:** Design — pending review.

## Background

Today, ralph is implicitly tied to one target repo: the one whose `ralph-queue` branch holds the queue. Self-hosting (ralph developing itself) means the queue lives in ralph's own repo. PBIs don't declare which repo they target — it's inferred from "whichever repo's queue branch this PBI sits in."

To enable a "generic ralph processor" model where one ralph instance can serve many target repos, each PBI must declare its target. This PBI adds the field. Consumption (fetching the target on demand) is a later PBI.

Decision recap (from brainstorm, confirmed by user):
- Queue stays as a branch of the ralph repo. NOT extracted to a separate repo.
- PBIs gain a required `target_repo` field carrying a full HTTPS URL.
- Migration: one-shot script (single-use, hardcoded URL) updates the 13 existing PBIs to point at `https://github.com/emp3thy/ralph`.
- `ralph-add` auto-derives the field from the operator's `--repo` origin remote (SSH form converted to HTTPS); `--target-repo` flag overrides.
- Field is metadata-only this PBI — ralph still operates single-target. Behavior change: NONE.

## Decision

Add `target_repo: str` (HTTPS URL) as a required field in `REQUIRED_FRONTMATTER_FIELDS`. Validation rule rejects non-HTTPS, missing-path, non-string, or unparseable values; accepts both GitHub and ADO URL shapes via a permissive "≥2 path segments" check (no host whitelist).

A single-use migration script `scripts/migrate_pbis_to_target_repo.py` walks `.ralph/{inbox,current,pending-pr,done,blocked}/*/{PBI,BUG,FEEDBACK}.md` and inserts `target_repo: https://github.com/emp3thy/ralph` into each frontmatter that lacks the field. The URL is hardcoded; this is the only migration. The script is idempotent so re-running is safe.

`ralph-add` writes the field at PBI-create time by deriving from the repo's git origin remote (converting SSH to HTTPS), or from an explicit `--target-repo` flag when origin is the wrong target.

## Architecture

### File layout (delta)

```
scripts/validate_samples.py                   MODIFY (add target_repo to REQUIRED_FRONTMATTER_FIELDS
                                              + _validate_target_repo helper)
scripts/pbi_reader.py                         MODIFY (parse field; add to row dataclass)
scripts/migrate_pbis_to_target_repo.py        NEW (one-shot, hardcoded URL)

skills/ralph-add/scripts/add.py               MODIFY (--target-repo flag + auto-derive
                                              from origin; write into new PBI frontmatter)
skills/workitem-fetch-github/scripts/schema.py  MODIFY (work-item JSON gains target_repo)

prompt/PROMPT.md                              MODIFY (mention the field; no behavior change)

samples/*/PBI.md                              MODIFY (canonical samples gain the field;
                                              edited directly for reviewable diff)
.ralph/{inbox,current,pending-pr,done,blocked}/*/{PBI,BUG,FEEDBACK}.md
                                              MODIFY (one-shot migration commit)

tests/test_pbi_reader.py                      MODIFY (target_repo parsed)
tests/skills/test_ralph_add.py                MODIFY (auto-derive + flag override)
tests/scripts/test_validate_samples.py        MODIFY (required-field tests)
tests/scripts/test_migrate_pbis.py            NEW (migration script tests)
```

### Schema change

In `scripts/validate_samples.py`, add `"target_repo"` to `REQUIRED_FRONTMATTER_FIELDS`:

```python
REQUIRED_FRONTMATTER_FIELDS: tuple[str, ...] = (
    "id",
    "type",
    "status",
    "severity",
    "attempts",
    "target_repo",   # NEW
    # ... existing fields ...
)
```

Add the validator:

```python
def _validate_target_repo(value: object) -> str | None:
    """Return error message or None if valid."""
    if not isinstance(value, str):
        return f"target_repo must be a string, got {type(value).__name__}"
    if not value.startswith("https://"):
        return f"target_repo must be an HTTPS URL, got {value!r}"
    parsed = urllib.parse.urlparse(value)
    if not parsed.netloc:
        return f"target_repo URL has no host: {value!r}"
    path_segments = [p for p in parsed.path.split("/") if p]
    if len(path_segments) < 2:
        return f"target_repo URL must include owner + name path: {value!r}"
    return None
```

Acceptance examples:

| URL | Verdict |
|---|---|
| `https://github.com/emp3thy/ralph` | accepted |
| `https://github.com/emp3thy/ralph.git` | accepted (consumers strip `.git`) |
| `https://dev.azure.com/myorg/myproj/_git/myrepo` | accepted (ADO shape, ≥2 path segments) |
| `emp3thy/ralph` | rejected (no scheme) |
| `http://github.com/x/y` | rejected (not HTTPS) |
| `git@github.com:emp3thy/ralph.git` | rejected (SSH form) |
| `https://github.com` | rejected (no path segments) |

`scripts/pbi_reader.py` adds `target_repo: str` to the row dataclass and reads the field from frontmatter alongside the existing fields.

### Migration script

`scripts/migrate_pbis_to_target_repo.py` (NEW, ~50 lines):

```python
"""One-shot migration: insert target_repo into every PBI lacking it.

Hardcoded for the 2026-05-27 ralph self-host migration. Throwaway —
delete after merge if you like, but harmless to keep for audit.

Run from repo root:
    uv run python scripts/migrate_pbis_to_target_repo.py
"""

from __future__ import annotations

from pathlib import Path
import sys

TARGET_REPO = "https://github.com/emp3thy/ralph"
REPO_ROOT = Path(__file__).resolve().parents[1]
STATE_DIRS = ("inbox", "current", "pending-pr", "done", "blocked")
ENTRY_FILENAMES = ("PBI.md", "BUG.md", "FEEDBACK.md")


def _insert_field(path: Path) -> str:
    """Returns 'updated', 'skipped', or 'error: <reason>'."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return "error: no frontmatter fence at start"
    lines = text.splitlines(keepends=True)
    # Find closing fence.
    close_idx = None
    for idx in range(1, len(lines)):
        if lines[idx].rstrip("\n") == "---":
            close_idx = idx
            break
    if close_idx is None:
        return "error: no closing frontmatter fence"
    # Already present?
    for line in lines[1:close_idx]:
        if line.lstrip().startswith("target_repo:"):
            return "skipped"
    # Insert just before the closing fence.
    new_line = f"target_repo: {TARGET_REPO}\n"
    lines.insert(close_idx, new_line)
    path.write_text("".join(lines), encoding="utf-8")
    return "updated"


def main() -> int:
    queue_root = REPO_ROOT / ".ralph"
    if not queue_root.is_dir():
        print(f"error: {queue_root} does not exist", file=sys.stderr)
        return 2

    counts = {"updated": 0, "skipped": 0, "error": 0}
    for state in STATE_DIRS:
        state_dir = queue_root / state
        if not state_dir.is_dir():
            continue
        for pbi_dir in sorted(state_dir.iterdir()):
            if not pbi_dir.is_dir():
                continue
            for filename in ENTRY_FILENAMES:
                entry = pbi_dir / filename
                if not entry.is_file():
                    continue
                result = _insert_field(entry)
                if result.startswith("error"):
                    counts["error"] += 1
                    print(f"  {entry.relative_to(REPO_ROOT)}: {result}", file=sys.stderr)
                else:
                    counts[result] += 1
                print(f"{entry.relative_to(REPO_ROOT)}: {result}")
    print(
        f"\n{counts['updated']} updated, {counts['skipped']} skipped, "
        f"{counts['error']} errors."
    )
    return 0 if counts["error"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
```

**Idempotent**: skip-if-present check at line scan. Re-runnable.

**Single commit during implementation** runs the script, validates output, commits the modified PBI files. The script itself stays in the repo for audit (operators can delete it later if they like).

### `ralph-add` changes

New flag:

```
--target-repo <https-url>   Optional. If omitted, auto-derive from the
                            --repo's git origin remote.
```

Auto-derive logic:

```python
def _derive_target_repo(repo: Path) -> str:
    """Read git origin remote URL. Reshape SSH-form to HTTPS.

    Examples:
      git@github.com:emp3thy/ralph.git    -> https://github.com/emp3thy/ralph
      https://github.com/emp3thy/ralph    -> https://github.com/emp3thy/ralph
      https://github.com/emp3thy/ralph.git -> https://github.com/emp3thy/ralph
    """
    url = _run_git(repo, "remote", "get-url", "origin").strip()
    ssh_match = re.match(r"^git@([^:]+):(.+?)(?:\.git)?$", url)
    if ssh_match:
        host, path = ssh_match.group(1), ssh_match.group(2)
        return f"https://{host}/{path}"
    if url.startswith("https://"):
        return url.removesuffix(".git")
    raise _FatalError(
        f"cannot derive target_repo from origin URL {url!r}; "
        f"pass --target-repo explicitly"
    )
```

Precedence in main() argparse handling:
1. `--target-repo <url>` if passed
2. Else `_derive_target_repo(args.repo)`
3. Validate via `_validate_target_repo` BEFORE writing PBI

`skills/workitem-fetch-github/scripts/schema.py` adds `target_repo` to the work-item JSON schema. The fetcher passes the value through (or leaves it unset and lets ralph-add fill it from its own derivation).

`prompt/PROMPT.md`: brief note that the field exists. No behavior change for claude this PBI.

### Sample updates

`samples/*/PBI.md` (and `BUG.md`, `FEEDBACK.md` if present) edited directly to add the field. Reviewable in diff; not done via the migration script.

## Testing

`tests/scripts/test_validate_samples.py`:

- PBI without `target_repo` → validation error mentions `target_repo`
- Valid GitHub URL → accepted
- Valid ADO URL → accepted
- `http://` URL → rejected (mentions HTTPS)
- SSH URL → rejected (mentions HTTPS)
- URL without path → rejected (mentions path)
- Non-string value → rejected (mentions string)

`tests/test_pbi_reader.py`:

- Parsed row carries `target_repo` field with the value from frontmatter

`tests/skills/test_ralph_add.py`:

- HTTPS origin → derived target_repo matches origin (minus `.git`)
- SSH origin → derived target_repo is HTTPS form
- `--target-repo` flag → overrides auto-derive
- Invalid `--target-repo` value → exit 2 + clear message
- Unparseable origin → exit 2 + message pointing at `--target-repo`

`tests/scripts/test_migrate_pbis.py` (NEW):

- Adds field to PBIs lacking it
- Idempotent: second run is no-op
- Inserts before closing fence (position check)
- Preserves field order of existing frontmatter
- Errors on malformed frontmatter (no fence) without crashing

Validation regression test: every file under `.ralph/*/{PBI,BUG,FEEDBACK}.md` passes `validate_samples.validate()` post-migration.

## Acceptance

- `scripts/validate_samples.py` adds `target_repo` to required fields + `_validate_target_repo` helper
- `scripts/pbi_reader.py` parses the field
- `scripts/migrate_pbis_to_target_repo.py` exists, is idempotent, runs successfully against the live queue
- `skills/ralph-add/scripts/add.py` writes `target_repo` into new PBIs (auto-derive or flag-override)
- `skills/workitem-fetch-github/scripts/schema.py` includes the field
- `samples/*/PBI.md` (etc.) have the field
- All 13 existing PBIs under `.ralph/` have `target_repo: https://github.com/emp3thy/ralph`
- pytest / ruff / mypy strict all green
- depends_on: `[]`

## Out of scope

- Consumption: ralph's loop does not yet read `target_repo` to switch repos. Single-target behavior preserved.
- Queue extraction to a separate repo — confirmed not happening; queue stays in ralph repo as a data-only branch (later PBI).
- Deployed-ralph model — separate later PBI.
- ADO-specific URL parsing beyond the generic "HTTPS + ≥2 path segments" check
- `target_branch` field (e.g., target `develop`) — YAGNI
- Per-target auth credentials — relies on ambient `GH_TOKEN`
- Multi-target test fixtures or simulation harness

## Risks

| Risk | Mitigation |
|---|---|
| Operator's fork has non-matching origin → derived target_repo points at upstream | `--target-repo` override flag |
| Existing PBI in some state not walked (e.g., `archive/`) doesn't get migrated | Migration walks all 5 STATE_FOLDERS; validation regression test asserts all are touched |
| Over-strict validation regex rejects valid URLs | Permissive check: HTTPS + parseable URL + ≥2 path segments; no host whitelist; explicit ADO URL test case |
| Ralph-add auto-derive fails on a fresh repo with no origin | Clear error message naming `--target-repo` flag |
| Migration script run twice → corruption | Idempotent (skip-if-present); test covers re-run |
| Field added but ralph still operates single-target → operators expect multi-target | Spec explicitly states "behavior change: none"; tests confirm existing single-target paths still pass |
| `.git` suffix significant for cloning, not for identity | Consumers strip `.git` when comparing / displaying; field can carry either form |
| Field name collision with existing PBI metadata in some forks | `target_repo` is novel — grep verified zero existing uses in current tree |
