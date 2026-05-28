"""Read + parse ``target_repo`` from a PBI directory's entry-file frontmatter.

Used by the sweep to compute per-PBI ``GH_OWNER`` and ``--repo`` overrides
when invoking the pr-github skill scripts (``show.py``, ``read_threads.py``,
``merge_pr.py``, ``lookup_by_branch.py``). The loop's
``_read_target_repo_from_pbi`` covers the inbox→current claim path; this
helper is the equivalent for pending-pr / orphan PBIs that the sweep
processes long after claim time.

Returning ``None`` (rather than raising) when the entry file or
``target_repo`` field is absent lets legacy/test fixtures predating the
multi-target rollout fall back to ``ctx.repo_name``; only a *malformed*
URL raises ``ValueError`` for the caller to map to a skip + log.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from ralph_executor.url_utils import TargetRepoInfo, parse_target_repo

_ENTRY_FILENAMES: tuple[str, ...] = ("PBI.md", "BUG.md", "FEEDBACK.md")


def read_target_info(pbi_dir: Path) -> TargetRepoInfo | None:
    """Read + parse ``target_repo`` from a PBI directory's entry file.

    Returns the parsed ``TargetRepoInfo`` on success. Returns ``None`` when
    the entry file is missing, the frontmatter is absent / not a mapping,
    or the ``target_repo`` field is absent — those are legacy / test PBIs
    predating the multi-target rollout (caller falls back to
    ``ctx.repo_name`` and inherited env).

    Raises ``ValueError`` when frontmatter YAML is malformed or
    ``target_repo`` is set to an invalid URL — the per-PBI guard in the
    sweep maps this to a skip + log so one bad PBI doesn't crash the
    rest of the pass.
    """
    entry: Path | None = None
    for name in _ENTRY_FILENAMES:
        candidate = pbi_dir / name
        if candidate.is_file():
            entry = candidate
            break
    if entry is None:
        return None

    text = entry.read_text(encoding="utf-8")
    if not (text.startswith("---\n") or text.startswith("---\r\n")):
        return None
    lines = text.splitlines()
    close_idx = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if close_idx is None:
        return None
    try:
        frontmatter = yaml.safe_load("\n".join(lines[1:close_idx]))
    except yaml.YAMLError as exc:
        raise ValueError(f"PBI {pbi_dir.name}: YAML parse error: {exc}") from exc
    if not isinstance(frontmatter, dict):
        return None
    target_repo = frontmatter.get("target_repo")
    if not target_repo:
        return None
    return parse_target_repo(str(target_repo))
