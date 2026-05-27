---
id: RALPH-PBI-TARGET-REPO-FIELD
type: feature
status: current
severity: normal
attempts: 0
created_at: 2026-05-27T00:03:00+00:00
updated_at: 2026-05-27T18:43:15+00:00
depends_on: []
target_repo: https://github.com/emp3thy/ralph
---

# Add target_repo field to PBI frontmatter

Add a required `target_repo` field (HTTPS URL string) to every PBI's frontmatter. Foundation for "generic ralph processor" where one ralph instance can serve many target repos. Each PBI declares which repo it applies to via its frontmatter.

**Behavior change this PBI: none.** Field is metadata only. Consumption (ralph's loop fetching the target on demand) happens in later PBIs.

Decision recap (from brainstorm):
- Queue stays as a branch in the ralph repo. NOT extracted to a separate repo.
- Format: full HTTPS URL (e.g., `https://github.com/emp3thy/ralph`)
- Required on every PBI; validation enforces HTTPS + parseable URL + ≥2 path segments
- Migration: single-use script inserts `target_repo: https://github.com/emp3thy/ralph` into the 13 existing PBIs
- `ralph-add` auto-derives the field from `--repo`'s git origin remote (SSH form converted to HTTPS); `--target-repo` flag overrides

Spec: `docs/superpowers/specs/2026-05-27-pbi-target-repo-field-design.md`
Plan: `docs/superpowers/plans/2026-05-27-pbi-target-repo-field-plan.md`

See `PLAN.md` for the implementation plan pointer.

## Acceptance criteria

- `scripts/validate_samples.py` adds `target_repo` to `REQUIRED_FRONTMATTER_FIELDS` with `_validate_target_repo` helper enforcing HTTPS URL with ≥2 path segments
- `scripts/pbi_reader.py` parses the field; `PBIRow.target_repo: str` field
- `scripts/migrate_pbis_to_target_repo.py` exists (single-use, hardcoded URL, idempotent)
- All 13 existing PBIs under `.ralph/` have `target_repo: https://github.com/emp3thy/ralph` in frontmatter
- `samples/*/PBI.md` (and BUG.md / FEEDBACK.md if present) include the field
- `skills/ralph-add/scripts/add.py` writes the field at PBI-creation time: auto-derive from origin OR explicit `--target-repo` flag
- `skills/workitem-fetch-github/scripts/schema.py` JSON output includes `target_repo`
- `prompt/PROMPT.md` notes the field exists (no behavior instructions)
- Plan-defined tests pass: `uv run pytest`
- `uv run ruff check .` clean
- `uv run ruff format --check .` clean
- `uv run mypy ralph_executor scripts skills tests` clean
- PR opened against `main` from `ralph/RALPH-PBI-TARGET-REPO-FIELD`
