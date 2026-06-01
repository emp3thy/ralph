"""Pin autobug awareness in the memory prompt — signature + RALPH_AUTOBUG_DEPTH."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MEMORY_PATH = REPO_ROOT / "prompt" / "06-memory" / "memory.md"


def test_memory_prompt_mentions_autobug_signature_marker() -> None:
    text = MEMORY_PATH.read_text(encoding="utf-8")
    assert "signature:" in text
    assert "RALPH_AUTOBUG_DEPTH" in text
