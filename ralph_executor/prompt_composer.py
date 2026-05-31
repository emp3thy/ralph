"""Standing-prompt composer for the ralph executor.

The executor calls ``compose_prompt`` once per ``claude -p`` spawn to
assemble the per-PBI-type standing prompt from per-topic source files
under ``prompt/0N-topic/``.

See ``docs/superpowers/specs/2026-05-31-prompt-md-split-design.md`` for
the topic-folder convention, the override-vs-root rule, and the
spawn-time injection mechanism.
"""

from __future__ import annotations

from pathlib import Path

VALID_TYPES = frozenset({"feature", "bug", "pr-feedback"})


class PromptComposeError(RuntimeError):
    """Raised when prompt composition cannot produce a valid result.

    Surfaces fast at spawn time before ``claude -p`` is launched so the
    executor can log a clear error and the iteration is classified as
    ``error`` rather than spawning Claude with an incomplete prompt.
    """


def compose_prompt(prompt_root: Path, pbi_type: str) -> str:
    """Assemble standing prompt for a given PBI type.

    Walks topic folders under ``prompt_root`` in alpha order. For each
    topic, uses the override subfolder matching ``pbi_type`` if present,
    otherwise the topic root ``.md`` file. Concatenates the chosen file
    contents into a single string (sections separated by blank lines)
    and returns it.

    Raises ``PromptComposeError`` if ``pbi_type`` is not in
    ``VALID_TYPES``, or if any topic folder has neither a root ``.md``
    nor a matching override.
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
