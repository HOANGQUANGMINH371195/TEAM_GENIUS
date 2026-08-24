from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "eval" / "cases" / "snapshot-c439751724ab7f10.jsonl"
RELEASE = ROOT / "data" / "clean" / "medical_active_v31_fully_reviewed" / "release_benchmark.jsonl"
SEMANTIC = ROOT / "data" / "clean" / "medical_active_v31_fully_reviewed" / "semantic_question_benchmark.jsonl"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_release_locked_suite_has_current_source_hashes_and_coverage():
    rows = [json.loads(line) for line in SUITE.read_text(encoding="utf-8").splitlines() if line.strip()]
    manifest = rows[0]["manifest"]
    cases = rows[1:]

    assert manifest["dataset_id"] == "snapshot-c439751724ab7f10"
    assert manifest["cases"] == len(cases) == 292
    assert manifest["release_benchmark_sha256"] == _sha256(RELEASE)
    assert manifest["semantic_benchmark_sha256"] == _sha256(SEMANTIC)
    assert len({case["case_id"] for case in cases}) == len(cases)
    assert {case["dataset_id"] for case in cases} == {manifest["dataset_id"]}
    coverage = Counter(case["kind"] for case in cases)
    assert coverage == {"exact": 100, "graph_temporal": 100, "thematic": 80, "policy": 6, "table": 2, "no_answer": 4}
