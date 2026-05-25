# Ralph

Ralph v1 — a per-repo autonomous coding loop. See
`docs/superpowers/specs/2026-05-23-ralph-v1-per-repo-loop-design.md` for the design
and `docs/superpowers/plans/2026-05-24-00-orchestrator.md` for the implementation plan.

## Bootstrap

```bash
uv sync
uv run pytest
uv run ruff check .
uv run mypy ralph_executor scripts tests
```
