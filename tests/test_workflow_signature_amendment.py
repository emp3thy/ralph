from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = REPO_ROOT / "prompt" / "03-workflow" / "bug" / "workflow.md"


def test_workflow_includes_signature_aware_branch() -> None:
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "signature:" in text.lower()
    assert "autobug" in text.lower()
    assert "starting point" in text.lower() or "observed artefacts" in text.lower()
