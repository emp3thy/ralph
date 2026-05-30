"""Body templates for ralph-new — bug + feature + HISTORY constant.

All templates use simple ``str.format``-style placeholders. Section
rendering is the caller's job (``writer.write_pbi_dir``); these strings
are the canonical layout the spec pins.
"""

from __future__ import annotations

HISTORY_TEMPLATE = (
    "<!-- Executor appends attempt records here. Do not delete — "
    "required by the PBI directory schema. -->\n"
)

_NOT_PROVIDED = "_Not provided_"


def render_bug_md(
    *,
    frontmatter: str,
    title: str,
    symptom: str,
    root_cause: str,
    impact: str,
    acceptance_criteria: str,
) -> str:
    """Render the full ``BUG.md`` file body, frontmatter included."""
    return (
        f"{frontmatter}"
        f"# {title}\n"
        f"\n"
        f"## Symptom\n"
        f"{_section_body(symptom)}\n"
        f"\n"
        f"## Root cause (suspected)\n"
        f"{_section_body(root_cause)}\n"
        f"\n"
        f"## Impact\n"
        f"{_section_body(impact)}\n"
        f"\n"
        f"## Acceptance criteria\n"
        f"{_section_body(acceptance_criteria)}\n"
    )


def render_reproduce_md(
    *,
    title: str,
    environment: str,
    steps: str,
    expected: str,
    actual: str,
) -> str:
    """Render the full ``REPRODUCE.md`` file body. No frontmatter."""
    return (
        f"# Reproduce: {title}\n"
        f"\n"
        f"## Environment\n"
        f"{_section_body(environment)}\n"
        f"\n"
        f"## Steps\n"
        f"{_section_body(steps)}\n"
        f"\n"
        f"## Expected\n"
        f"{_section_body(expected)}\n"
        f"\n"
        f"## Actual\n"
        f"{_section_body(actual)}\n"
    )


def render_reproduce_md_raw(*, title: str, body: str) -> str:
    """Render REPRODUCE.md with file-supplied body embedded verbatim.

    Used when --reproduce-file supplies the full REPRODUCE.md body; the
    file is assumed to already carry whatever structure the author wants
    (its own ## sections etc.).
    """
    return f"# Reproduce: {title}\n\n{body.rstrip()}\n"


def render_pbi_md(
    *,
    frontmatter: str,
    title: str,
    description: str,
    acceptance_criteria: str,
    spec_path: str,
) -> str:
    """Render the full feature ``PBI.md`` file body, frontmatter included."""
    spec_line = f"Spec: `{spec_path}`" if spec_path else "Spec: `<TBD>`"
    return (
        f"{frontmatter}"
        f"# {title}\n"
        f"\n"
        f"{_section_body(description)}\n"
        f"\n"
        f"{spec_line}   (referenced for context; the plan is self-contained in PLAN.md)\n"
        f"\n"
        f"## Acceptance criteria\n"
        f"{_section_body(acceptance_criteria)}\n"
        f"\n"
        f"See `PLAN.md` for the implementation plan checklist.\n"
    )


def render_plan_md(*, title: str, plan_path: str, plan_content: str) -> str:
    """Render ``PLAN.md`` with the plan content embedded inline.

    ``plan_content`` is the verbatim text of the file at ``--plan-path``
    (read by ``new.py`` BEFORE calling this function; if the path is
    invalid, ``new.py`` exits 2 with a clear error and never reaches here).
    Blank ``plan_path`` + blank ``plan_content`` → TODO stub.
    """
    if plan_path and plan_content:
        return (
            f"# Plan: {title}\n"
            f"\n"
            f"> Source: `{plan_path}` (embedded verbatim at PBI submission time)\n"
            f"\n"
            f"{plan_content.rstrip()}\n"
        )
    return (
        f"# Plan: {title}\n"
        f"\n"
        f"TODO: write the implementation plan as inline content in this file. "
        f"The PBI directory is self-contained: do not reference an external "
        f"plan path that ralph's worktree may not be able to resolve.\n"
    )


def _section_body(text: str) -> str:
    """Return the section body, or the italic 'not provided' placeholder."""
    stripped = text.strip()
    if not stripped:
        return _NOT_PROVIDED
    return stripped
