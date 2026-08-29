from __future__ import annotations

import json
from pathlib import Path

from scripts.verify_release_artifacts import verify


def test_missing_external_artifacts_are_explicitly_unverified(tmp_path: Path) -> None:
    report = verify(tmp_path)
    assert report["available"] is False
    assert report["verified"] is False
    assert report["missing"]


def test_mounted_artifacts_must_match_locked_suite(tmp_path: Path) -> None:
    release_dir = tmp_path / "data/clean/medical_active_v31_fully_reviewed"
    release_dir.mkdir(parents=True)
    release = release_dir / "release_benchmark.jsonl"
    semantic = release_dir / "semantic_question_benchmark.jsonl"
    release.write_text(json.dumps({"case_id": "r1"}) + "\n", encoding="utf-8")
    semantic.write_text(json.dumps({"question": "q", "document_id": "d"}) + "\n", encoding="utf-8")
    suite_dir = tmp_path / "eval/cases"
    suite_dir.mkdir(parents=True)
    # A malformed suite is rejected even though both external files exist.
    (suite_dir / "snapshot-c439751724ab7f10.jsonl").write_text("{}\n", encoding="utf-8")
    report = verify(tmp_path)
    assert report["available"] is True
    assert report["verified"] is False
    assert "locked_suite_manifest_missing" in report["errors"]
