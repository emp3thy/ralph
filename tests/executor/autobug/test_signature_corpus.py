"""Corpus regression test: bucketed real-stderr blobs must collapse correctly.

Ships with synthetic placeholder buckets. Operator runs
``scripts/build_autobug_corpus.py`` to populate real buckets before
raising ``autobug_rate_max`` above the default 5 — running blind on
synthetic fixtures is acceptable for the first week but not steady state.
"""

from pathlib import Path

import pytest

from ralph_executor.autobug.signature import subprocess_crash_signature

CORPUS_ROOT = Path(__file__).parent / "fixtures" / "stderr_corpus"


def _bucket_dirs() -> list[Path]:
    if not CORPUS_ROOT.is_dir():
        return []
    return [d for d in CORPUS_ROOT.iterdir() if d.is_dir()]


@pytest.mark.parametrize("bucket", _bucket_dirs(), ids=lambda d: d.name)
def test_bucket_files_collapse_to_one_signature(bucket: Path) -> None:
    """All files within one bucket dir must hash to the same signature."""
    files = sorted(p for p in bucket.iterdir() if p.suffix == ".txt")
    if len(files) < 2:
        pytest.skip(f"bucket {bucket.name} has <2 files; nothing to collapse")
    sigs = {f.name: subprocess_crash_signature(1, f.read_text(encoding="utf-8")) for f in files}
    unique = set(sigs.values())
    assert len(unique) == 1, f"bucket {bucket.name} did not collapse: {sigs}"


def test_buckets_have_distinct_signatures() -> None:
    """Files from different buckets must produce different signatures."""
    seen: dict[str, str] = {}
    for bucket in _bucket_dirs():
        files = sorted(p for p in bucket.iterdir() if p.suffix == ".txt")
        if not files:
            continue
        sig = subprocess_crash_signature(1, files[0].read_text(encoding="utf-8"))
        assert sig not in seen, (
            f"bucket {bucket.name} collides with {seen[sig]}: signature {sig[:8]}"
        )
        seen[sig] = bucket.name
