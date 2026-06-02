"""T26 — corpus extractor script smoke tests."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "build_autobug_corpus.py"


def test_script_exists() -> None:
    assert SCRIPT_PATH.is_file()


def test_script_imports_cleanly() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("build_autobug_corpus", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    assert hasattr(module, "main")
