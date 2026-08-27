#!/usr/bin/env python3
"""Build a redacted, hash-bound packet for independent legal reviewers.

The packet contains the user question, answer text and public citation fields,
but never raw retrieval chunks, internal document IDs, secrets or user
identifiers.  It creates no labels; reviewers must fill those independently.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

_SECRET = re.compile(r"(?i)(?:api[_-]?key|secret|password|token|private[_-]?key)\s*[:=]\s*[^\s,;]+")
_INTERNAL_ID = re.compile(r"(?i)(?:document|chunk|passage|node|relationship|unit)_?id\s*[:=]\s*[^\s,;]+")


def _safe_text(value: Any, *, limit: int = 12_000) -> str:
    text = str(value or "")
    text = _SECRET.sub("[REDACTED]", text)
    text = _INTERNAL_ID.sub("[REDACTED]", text)
    return text[:limit]


def _load_jsonl(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows or not isinstance(rows[0].get("manifest"), dict):
        raise ValueError(f"{path}: manifest required")
    return rows[0]["manifest"], [row for row in rows[1:] if isinstance(row, dict)]


def build_packet(fixture: Path, answers: Path, output: Path, *, release_id: str) -> dict[str, Any]:
    fixture_manifest, fixture_rows = _load_jsonl(fixture)
    _, answer_rows = _load_jsonl(answers)
    by_case = {str(row.get("case_id") or ""): row for row in answer_rows}
    if len(by_case) != len(answer_rows):
        raise ValueError("answers contain duplicate or empty case IDs")
    packet: list[dict[str, Any]] = []
    for case in fixture_rows:
        case_id = str(case.get("case_id") or "").strip()
        answer = by_case.get(case_id)
        if answer is None:
            raise ValueError(f"missing answer for {case_id}")
        response = _safe_text(answer.get("response"), limit=20_000)
        citations = []
        for item in answer.get("citations") or []:
            if not isinstance(item, dict):
                continue
            citations.append({
                "document_number": _safe_text(item.get("document_number"), limit=200),
                "title": _safe_text(item.get("title"), limit=500),
                "section_title": _safe_text(item.get("section_title"), limit=500),
                "quote": _safe_text(item.get("quote"), limit=1_200),
            })
        packet.append({
            "case_id": case_id,
            "release_id": release_id,
            "question": _safe_text(case.get("question"), limit=4_000),
            "response": response,
            "answer_sha256": hashlib.sha256(response.encode("utf-8")).hexdigest(),
            "citations": citations[:12],
            "expected_status": _safe_text(case.get("expected_status"), limit=200),
            "required_facts": [_safe_text(item, limit=500) for item in case.get("required_facts") or []],
            "forbidden_behavior": [_safe_text(item, limit=500) for item in case.get("forbidden_behavior") or []],
            "review_labels": [],
        })
    manifest = {
        "artifact": "human-legal-review-packet-v1",
        "release_id": release_id,
        "cases": len(packet),
        "fixture_sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
        "answers_sha256": hashlib.sha256(answers.read_bytes()).hexdigest(),
        "source_suite": str(fixture_manifest.get("suite_id") or ""),
        "label_schema": "eval.human_review.ReviewLabel",
        "reviewer_policy": "two independent reviewers; disagreement must be resolved outside this packet",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps({"manifest": manifest}, ensure_ascii=False, sort_keys=True) + "\n")
        for row in packet:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--answers", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--release-id", required=True)
    args = parser.parse_args()
    if not args.release_id.startswith("snapshot-"):
        parser.error("--release-id must be an immutable snapshot")
    print(json.dumps(build_packet(args.fixture, args.answers, args.output, release_id=args.release_id), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
