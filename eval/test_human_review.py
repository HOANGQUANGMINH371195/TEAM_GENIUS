from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from eval.build_review_packet import build_packet
from eval.human_review import load_review_artifact, validate_review_panel


def _packet_inputs(tmp_path: Path) -> tuple[Path, Path]:
    fixture = tmp_path / "fixture.jsonl"
    fixture.write_text(
        json.dumps({"manifest": {"suite_id": "x", "cases": 1}}) + "\n"
        + json.dumps({"case_id": "c1", "question": "q", "required_facts": ["f"]}) + "\n",
        encoding="utf-8",
    )
    answers = tmp_path / "answers.jsonl"
    answers.write_text(
        json.dumps({"manifest": {"suite_id": "x", "cases": 1}}) + "\n"
        + json.dumps({"case_id": "c1", "response": "answer api_key=bad", "citations": [{"document_number": "51/2024/QH15", "quote": "q"}]}) + "\n",
        encoding="utf-8",
    )
    return fixture, answers


def test_review_packet_redacts_secret_and_binds_answer_hash(tmp_path: Path) -> None:
    fixture, answers = _packet_inputs(tmp_path)
    output = tmp_path / "packet.jsonl"
    manifest = build_packet(fixture, answers, output, release_id="snapshot-test")
    row = json.loads(output.read_text(encoding="utf-8").splitlines()[1])
    assert "bad" not in row["response"]
    assert row["answer_sha256"] == hashlib.sha256(row["response"].encode()).hexdigest()
    assert manifest["cases"] == 1


def test_review_panel_requires_two_unanimous_reviewers(tmp_path: Path) -> None:
    path = tmp_path / "review.jsonl"
    digest = "a" * 64
    manifest = {"artifact": "human-legal-review-v1", "release_id": "snapshot-test", "cases": 1}
    rows = [{"manifest": manifest}]
    for reviewer in ("legal-a", "legal-b"):
        rows.append({
            "case_id": "c1", "release_id": "snapshot-test", "answer_sha256": digest,
            "reviewer": reviewer, "factual_correct": True, "complete": True,
            "citation_supported": True, "catastrophic_error": False,
        })
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    loaded_manifest, labels = load_review_artifact(path)
    with pytest.raises(ValueError, match="at least 300"):
        validate_review_panel(loaded_manifest, labels)
