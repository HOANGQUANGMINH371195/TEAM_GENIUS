from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "eval" / "cases" / "snapshot-c439751724ab7f10.jsonl"
RELEASE = ROOT / "data" / "clean" / "medical_active_v31_fully_reviewed" / "release_benchmark.jsonl"
SEMANTIC = ROOT / "data" / "clean" / "medical_active_v31_fully_reviewed" / "semantic_question_benchmark.jsonl"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_release_locked_suite_is_well_formed_and_has_expected_coverage():
    rows = [json.loads(line) for line in SUITE.read_text(encoding="utf-8").splitlines() if line.strip()]
    manifest = rows[0]["manifest"]
    cases = rows[1:]

    assert manifest["dataset_id"] == "snapshot-c439751724ab7f10"
    assert manifest["cases"] == len(cases) == 292
    assert len({case["case_id"] for case in cases}) == len(cases)
    assert {case["dataset_id"] for case in cases} == {manifest["dataset_id"]}
    coverage = Counter(case["kind"] for case in cases)
    assert coverage == {"exact": 100, "graph_temporal": 100, "thematic": 80, "policy": 6, "table": 2, "no_answer": 4}


def test_release_locked_suite_hashes_match_local_release_artifacts():
    """Validate provenance when the intentionally untracked release artifacts exist.

    `data/clean/` is a deployment data artifact and is not checked into Git.  A
    source-only CI checkout must still validate the committed suite structure,
    while a release/data pipeline that mounts these artifacts verifies that the
    frozen hashes match exactly.
    """
    missing = [str(path.relative_to(ROOT)) for path in (RELEASE, SEMANTIC) if not path.is_file()]
    if missing:
        pytest.skip("release artifacts are not present in this source-only checkout: " + ", ".join(missing))

    manifest = json.loads(SUITE.read_text(encoding="utf-8").splitlines()[0])["manifest"]
    assert manifest["release_benchmark_sha256"] == _sha256(RELEASE)
    assert manifest["semantic_benchmark_sha256"] == _sha256(SEMANTIC)
