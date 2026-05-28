"""Tests for the bash helper scripts."""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


@pytest.mark.parametrize("name", ["build_image.sh", "preflight.sh"])
def test_script_exists_and_is_executable(name: str) -> None:
    path = SCRIPTS / name
    assert path.exists(), f"missing {path}"
    if os.name != "nt":
        mode = path.stat().st_mode
        assert mode & stat.S_IXUSR, f"{path} not executable"


@pytest.mark.parametrize("name", ["build_image.sh", "preflight.sh"])
def test_script_passes_shellcheck(name: str) -> None:
    if shutil.which("shellcheck") is None:
        pytest.skip("shellcheck not installed")
    result = subprocess.run(
        ["shellcheck", str(SCRIPTS / name)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode == 0, f"shellcheck findings:\n{result.stdout}\n{result.stderr}"


def test_build_script_help_exits_zero() -> None:
    result = subprocess.run(
        ["bash", str(SCRIPTS / "build_image.sh"), "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode == 0
    assert "Usage" in result.stdout or "Usage" in result.stderr


def test_build_script_requires_host() -> None:
    """No --host flag MUST be a usage error (exit 2)."""
    result = subprocess.run(
        ["bash", str(SCRIPTS / "build_image.sh")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode == 2, (
        f"expected exit 2 for missing --host, got {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "--host" in result.stderr or "host" in result.stderr.lower()


def test_build_script_rejects_unknown_host() -> None:
    """--host gitlab (or any value other than github|ado) MUST exit 2."""
    result = subprocess.run(
        ["bash", str(SCRIPTS / "build_image.sh"), "--host", "gitlab"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode == 2
    assert "github" in result.stderr and "ado" in result.stderr


@pytest.mark.parametrize("host", ["github", "ado"])
def test_build_script_accepts_valid_hosts(host: str) -> None:
    """--host github and --host ado MUST be accepted by the parser.
    The actual docker build is not invoked here — we shim docker
    with a stub on PATH so the script returns immediately after
    argument parsing."""
    import os as _os
    import tempfile as _tempfile

    with _tempfile.TemporaryDirectory() as td:
        shim = Path(td) / "docker"
        shim.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        shim.chmod(0o755)
        env = dict(_os.environ)
        env["PATH"] = f"{td}:{env.get('PATH', '')}"
        env.pop("RALPH_REGISTRY", None)
        result = subprocess.run(
            ["bash", str(SCRIPTS / "build_image.sh"), "--host", host],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            env=env,
        )
        assert result.returncode == 0, (
            f"expected exit 0 for --host {host}, got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        # The last line of stdout is the tag, which must include
        # the host suffix.
        last_line = result.stdout.strip().splitlines()[-1]
        assert last_line.endswith(f"-{host}"), f"expected tag suffix -{host}, got {last_line!r}"
