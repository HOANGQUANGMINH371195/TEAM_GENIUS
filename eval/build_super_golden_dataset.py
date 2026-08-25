#!/usr/bin/env python3
"""Build a large, source-locked adversarial benchmark for the active release.

The existing 292-case release suite is strong for exact/graph/thematic gates,
but it does not stress paraphrase, long constraints, distractors and answer
synthesis at enough depth. This builder keeps every expected document/relation
from the frozen benchmark and adds deterministic hard variants; it never puts
gold facts into the agent question.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).casefold() in {"1", "true", "yes"}


def _case(
    case_id: str,
    category: str,
    difficulty: str,
    question: str,
    source: dict[str, Any],
    *,
    variant: str,
) -> dict[str, Any]:
    expected_documents = list(source.get("expected_document_ids") or [])
    expected_relation = str(source.get("expected_relationship_id") or "")
    expected_signature = str(source.get("expected_signature") or "")
    servable = _bool(source.get("servable"), True)
    return {
        "case_id": case_id,
        "category": category,
        "difficulty": difficulty,
        "variant": variant,
        "question": question,
        "dataset_id": source.get("dataset_id", ""),
        "expected_document_ids": expected_documents,
        "expected_relationship_id": expected_relation,
        "expected_relation": source.get("expected_relation", ""),
        "expected_direction": source.get("expected_direction", ""),
        "expected_scope": source.get("expected_scope", ""),
        "expected_serving_status": source.get("expected_serving_status", "approved_evidence"),
        "expected_status_candidate": source.get("expected_status_candidate", ""),
        "expected_evidence_sha256": source.get("expected_evidence_sha256", ""),
        "expected_text_sha256": source.get("expected_text_sha256", ""),
        "expected_source_span": source.get("expected_source_span", source.get("source_span", [])),
        "servable": servable,
        "fixture_unservable": not servable,
        "expected_abstention": _bool(source.get("expected_abstention")),
        "source_case_id": source.get("case_id", ""),
        "gold_facts": {
            "document_count": len(expected_documents),
            "relationship_required": bool(expected_relation),
            "expected_signature": expected_signature,
            "expected_relation": source.get("expected_relation", ""),
            "expected_scope": source.get("expected_scope", ""),
            "expected_status_candidate": source.get("expected_status_candidate", ""),
            "expected_text_sha256": source.get("expected_text_sha256", ""),
            "expected_facts": source.get("expected_facts", []),
        },
    }


def build(release_path: Path, semantic_path: Path, output_path: Path) -> dict[str, Any]:
    release_rows = _load_jsonl(release_path)
    semantic_rows = _load_jsonl(semantic_path)
    manifest = release_rows[0].get("manifest", {}) if release_rows and "manifest" in release_rows[0] else {}
    release_cases = [row for row in release_rows if "case_id" in row]
    invalid = [
        str(row.get("case_id", ""))
        for row in release_cases
        if str(row.get("kind", "")) in {"exact", "graph_temporal"}
        and not (row.get("expected_document_ids") or row.get("expected_relationship_id"))
    ]
    if invalid:
        raise ValueError(f"release fixture has no identity gold: {invalid[:5]}")
    cases: list[dict[str, Any]] = []

    for row in release_cases:
        kind = str(row.get("kind", "unknown"))
        category = {
            "exact": "exact_deep",
            "graph_temporal": "multi_hop_temporal",
            "temporal_status": "temporal_status",
            "thematic": "thematic_synthesis",
            "policy": "policy_safety",
            "table": "table_numeric",
            "no_answer": "abstention",
        }.get(kind, kind)
        cases.append(
            _case(
                f"base:{row['case_id']}",
                category,
                "hard",
                str(row["question"]),
                row,
                variant="release_locked",
            )
        )

        if kind == "exact":
            question = (
                "Hãy tra cứu thật chính xác, phân biệt số hiệu gần giống và không dùng văn bản cũ thay thế. "
                + str(row["question"])
                + " Chỉ kết luận những trường thông tin mà nguồn chính thức xác nhận."
            )
            cases.append(_case(f"deep-exact:{row['case_id']}", "exact_deep", "extreme", question, row, variant="distractor_guard"))
        elif kind == "graph_temporal":
            question = (
                "Hãy giải bài toán nhiều bước: xác định văn bản nguồn, văn bản đích, loại quan hệ và phạm vi "
                "toàn bộ hay một phần; không suy ra quan hệ chỉ từ tên văn bản. "
                + str(row["question"])
            )
            cases.append(_case(f"deep-graph:{row['case_id']}", "multi_hop_temporal", "extreme", question, row, variant="relation_chain"))
        elif kind == "thematic":
            question = (
                str(row["question"])
                + " Hãy tổng hợp thành kết luận ngắn, liệt kê điều kiện/ngoại lệ/đối tượng nếu có, "
                "không chép nguyên đoạn văn bản và không trộn với văn bản khác."
            )
            cases.append(_case(f"deep-theme:{row['case_id']}", "thematic_synthesis", "extreme", question, row, variant="synthesis_no_copy"))

    # Semantic cases carry a canonical passage and document ID, so they remain
    # source-backed even though they are not identifier lookups.
    for index, row in enumerate(semantic_rows, start=1):
        source = {
            "case_id": f"semantic:{index}",
            "dataset_id": manifest.get("dataset_id", ""),
            "expected_document_ids": [row["document_id"]],
            "expected_relationship_id": "",
            "expected_evidence_sha256": "",
            "expected_text_sha256": hashlib.sha256(str(row.get("passage", "")).encode("utf-8")).hexdigest()
            if row.get("passage")
            else "",
            "expected_facts": row.get("expected_facts") or [],
        }
        question = (
            str(row["question"])
            + " Hãy tổng hợp dựa trên đoạn quy định phù hợp, nêu rõ phạm vi áp dụng và điều kiện; "
            "nếu nguồn không đủ thì phải nói rõ phần chưa thể kết luận."
        )
        cases.append(_case(f"semantic-deep:{index}", "thematic_synthesis", "extreme", question, source, variant="canonical_passage_synthesis"))

    # Add deterministic conversational/safety probes. These do not require a
    # source document and ensure the large set still tests non-legal routing.
    social = (
        ("SOCIAL-HI", "hi"),
        ("SOCIAL-HELLO", "Xin chào"),
        ("SOCIAL-THANKS", "Cảm ơn bạn"),
        ("SOCIAL-BYE", "Tạm biệt"),
    )
    for case_id, question in social:
        cases.append({
            "case_id": case_id,
            "category": "social_routing",
            "difficulty": "hard",
            "variant": "no_provider",
            "question": question,
            "dataset_id": manifest.get("dataset_id", ""),
            "expected_document_ids": [],
            "expected_relationship_id": "",
            "expected_evidence_sha256": "",
            "source_case_id": "",
            "gold_facts": {"provider_calls": 0},
        })

    output_path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "manifest": {
            "dataset_id": manifest.get("dataset_id", ""),
            "release_source": str(release_path),
            "semantic_source": str(semantic_path),
            "release_source_sha256": _sha256(release_path),
            "semantic_source_sha256": _sha256(semantic_path),
            "base_release_cases": len(release_cases),
            "semantic_cases": len(semantic_rows),
            "generated_cases": len(cases),
            "categories": sorted({str(case["category"]) for case in cases}),
            "gold_status": "release_locked_machine_generated; human_review_required",
            "validator": "nonempty_identity_and_provenance_v1",
        }
    }
    with output_path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(body, ensure_ascii=False, sort_keys=True) + "\n")
        for case in cases:
            handle.write(json.dumps(case, ensure_ascii=False, sort_keys=True) + "\n")
    # A file cannot contain a hash of its final bytes without a circular
    # rewrite. Keep the reproducible content hash in a detached sidecar and
    # make consumers verify it before scoring.
    dataset_sha256 = _sha256(output_path)
    sidecar = output_path.with_suffix(output_path.suffix + ".sha256")
    sidecar.write_text(dataset_sha256 + "\n", encoding="ascii")
    body["manifest"]["dataset_sha256"] = dataset_sha256
    body["manifest"]["dataset_sha256_sidecar"] = str(sidecar)
    return body["manifest"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--semantic", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.release, args.semantic, args.output), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
