"""Static tests for the Dockerfile.

These do NOT build the image. They parse the Dockerfile and
assert structural properties so failures show up in pytest
rather than in a 5-minute CI build.
"""

from pathlib import Path

import pytest
from dockerfile_parse import DockerfileParser

DOCKERFILE = Path(__file__).resolve().parents[2] / "Dockerfile"


@pytest.fixture(scope="module")
def parser() -> DockerfileParser:
    assert DOCKERFILE.exists(), f"missing {DOCKERFILE}"
    dfp = DockerfileParser(fileobj=DOCKERFILE.open("r", encoding="utf-8"))
    return dfp


def test_dockerfile_has_two_stages(parser: DockerfileParser) -> None:
    stages = [s for s in parser.structure if s["instruction"] == "FROM"]
    names = {s["value"].split(" AS ")[-1].strip() for s in stages if " AS " in s["value"]}
    assert "builder" in names, f"expected builder stage, got {names}"
    assert "runtime" in names, f"expected runtime stage, got {names}"


def test_dockerfile_declares_ralph_git_host_arg(parser: DockerfileParser) -> None:
    args = [s for s in parser.structure if s["instruction"] == "ARG"]
    blob = " ".join(s["value"] for s in args)
    assert "RALPH_GIT_HOST" in blob, "expected ARG RALPH_GIT_HOST"


def test_dockerfile_sets_ralph_git_host_env(parser: DockerfileParser) -> None:
    contents = DOCKERFILE.read_text(encoding="utf-8")
    assert "ENV RALPH_GIT_HOST=${RALPH_GIT_HOST}" in contents, (
        "expected ENV RALPH_GIT_HOST=${RALPH_GIT_HOST} so doctor sees the host"
    )


def test_dockerfile_fails_on_empty_ralph_git_host(parser: DockerfileParser) -> None:
    """The Dockerfile MUST fail the build if RALPH_GIT_HOST is
    not provided. Look for the guard RUN line."""
    contents = DOCKERFILE.read_text(encoding="utf-8")
    assert 'test -n "${RALPH_GIT_HOST}"' in contents, (
        "expected guard `test -n` to fail builds without the build arg"
    )


def test_dockerfile_copies_host_specific_skills(parser: DockerfileParser) -> None:
    contents = DOCKERFILE.read_text(encoding="utf-8")
    # Each host's skills must be copied twice — once to /opt/ralph/skills
    # (audit) and once to /root/.claude/skills (canonical name).
    assert "skills/pr-${RALPH_GIT_HOST}/" in contents
    assert "skills/workitem-fetch-${RALPH_GIT_HOST}/" in contents
    assert "/root/.claude/skills/pr/" in contents
    assert "/root/.claude/skills/workitem-fetch/" in contents


def test_runtime_image_runs_as_non_root(parser: DockerfileParser) -> None:
    user_lines = [s for s in parser.structure if s["instruction"] == "USER"]
    assert user_lines, "no USER directive found"
    last = user_lines[-1]["value"].strip()
    assert last == "ralph", f"final USER must be ralph, got {last!r}"


def test_entrypoint_is_ralph_executor(parser: DockerfileParser) -> None:
    ep_lines = [s for s in parser.structure if s["instruction"] == "ENTRYPOINT"]
    assert ep_lines, "no ENTRYPOINT"
    value = ep_lines[-1]["value"]
    assert "ralph-executor" in value


def test_no_inline_secrets(parser: DockerfileParser) -> None:
    contents = DOCKERFILE.read_text(encoding="utf-8")
    for needle in ("ANTHROPIC_API_KEY=", "ADO_PAT=", "GH_TOKEN=", "AWS_SECRET_ACCESS_KEY="):
        assert needle not in contents, f"found hard-coded secret marker {needle!r} in Dockerfile"


def test_baked_settings_copied(parser: DockerfileParser) -> None:
    contents = DOCKERFILE.read_text(encoding="utf-8")
    assert ".claude/settings.json /etc/ralph/.claude/settings.json" in contents


def test_node_and_claude_code_installed(parser: DockerfileParser) -> None:
    contents = DOCKERFILE.read_text(encoding="utf-8")
    assert "nodejs" in contents
    assert "@anthropic-ai/claude-code" in contents


def test_oci_labels_set_with_host_label(parser: DockerfileParser) -> None:
    labels = [s for s in parser.structure if s["instruction"] == "LABEL"]
    blob = " ".join(s["value"] for s in labels)
    assert "org.opencontainers.image.title" in blob
    assert "org.opencontainers.image.source" in blob
    assert "ralph.git-host" in blob, "expected ralph.git-host LABEL for host traceability"
