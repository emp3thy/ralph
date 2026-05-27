"""Parse + reshape target_repo URLs.

Used by:
- ralph_executor.target_clone to determine clone destination
- ralph_executor.loop._claim_pbi to host-check and propagate target identity
- ralph_executor.claude_spawn to set GH_OWNER per subprocess
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass


@dataclass(frozen=True)
class TargetRepoInfo:
    """Parsed view of a target_repo URL.

    Example for ``https://github.com/emp3thy/ralph``:
      host:  ``"github.com"``
      owner: ``"emp3thy"``
      name:  ``"ralph"`` (no .git suffix)
    """

    host: str
    owner: str
    name: str

    @property
    def clone_url(self) -> str:
        """The URL to pass to ``git clone``. Always re-adds the .git suffix."""
        return f"https://{self.host}/{self.owner}/{self.name}.git"

    @property
    def slug(self) -> str:
        """Filesystem-safe identifier ``<owner>-<name>``."""
        return f"{self.owner}-{self.name}"


def parse_target_repo(url: str) -> TargetRepoInfo:
    """Parse an HTTPS URL into TargetRepoInfo.

    Raises ValueError on:
      - non-HTTPS scheme
      - missing host (netloc)
      - path with fewer than 2 segments (owner + name required)

    ADO URL shapes (``https://dev.azure.com/myorg/myproj/_git/myrepo``)
    parse as ``owner=myorg, name=myproj`` — known wrong for ADO. Out
    of scope: host_select rejects non-github hosts upstream.
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(f"target_repo must be HTTPS, got {url!r}")
    if not parsed.netloc:
        raise ValueError(f"target_repo has no host: {url!r}")
    segments = [p for p in parsed.path.split("/") if p]
    if len(segments) < 2:
        raise ValueError(f"target_repo path must include owner + name: {url!r}")
    owner = segments[0]
    name = segments[1].removesuffix(".git")
    return TargetRepoInfo(host=parsed.netloc, owner=owner, name=name)
