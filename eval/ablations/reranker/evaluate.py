#!/usr/bin/env python3
"""Evaluate reranker variants on a fixed candidate/evidence artifact.

This is an IR ablation harness, not a legal-accuracy claim and not a live
provider benchmark. Candidate rows must come from a release-locked artifact;
the harness preserves its hash and reports only Recall@k/MRR so a later human
review can decide whether a variant is promotable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.models.graph import RetrievalResult  # noqa: E402
from src.services.retrieval import rerank_legal_candidates  # noqa: E402


def _read_cases(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict) or not str(row.get("case_id") or "").strip():
            raise ValueError(f"invalid candidate case at line {line_number}")
        candidates = row.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise ValueError(f"{row['case_id']}: candidates are required")
        rows.append(row)
    if not rows:
        raise ValueError("candidate artifact has no cases")
    return rows


def _result(candidate: dict[str, Any]) -> RetrievalResult:
    return RetrievalResult(
        chunk_id=str(candidate.get("chunk_id") or candidate.get("evidence_id") or ""),
        document_id=str(candidate.get("document_id") or ""),
        content=str(candidate.get("content") or candidate.get("text") or ""),
        title=str(candidate.get("title") or ""),
        document_number=str(candidate.get("document_number") or ""),
        section_title=str(candidate.get("section_title") or ""),
        score=float(candidate.get("score") or 0),
        channels=[str(value) for value in candidate.get("channels") or ["artifact"]],
    )


def _relevant_ids(case: dict[str, Any]) -> set[str]:
    expected = case.get("relevant_ids") or case.get("reference_context_ids") or []
    if not isinstance(expected, list):
        raise ValueError(f"{case['case_id']}: relevant_ids must be a list")
    return {str(value) for value in expected if str(value).strip()}


def _rank_baseline(candidates: list[RetrievalResult]) -> list[RetrievalResult]:
    return sorted(candidates, key=lambda item: (-float(item.score), item.document_id, item.chunk_id))


def _metrics(rows: list[tuple[list[RetrievalResult], set[str]]], *, k_values: tuple[int, ...]) -> dict[str, Any]:
    metrics: dict[str, Any] = {"cases": len(rows), "eligible": 0, "recall": {}, "mrr": 0.0}
    reciprocal: list[float] = []
    for k in k_values:
        hits = 0
        eligible = 0
        for ranked, relevant in rows:
            if not relevant:
                continue
            eligible += 1
            identifiers = {item.chunk_id for item in ranked[:k]} | {item.document_id for item in ranked[:k]}
            hits += int(bool(identifiers & relevant))
        metrics["recall"][f"@{k}"] = hits / eligible if eligible else None
        metrics["eligible"] = max(metrics["eligible"], eligible)
    for ranked, relevant in rows:
        if not relevant:
            continue
        for index, item in enumerate(ranked, start=1):
            if item.chunk_id in relevant or item.document_id in relevant:
                reciprocal.append(1.0 / index)
                break
        else:
            reciprocal.append(0.0)
    metrics["mrr"] = sum(reciprocal) / len(reciprocal) if reciprocal else None
    return metrics


def run_ablation(path: Path, *, k_values: tuple[int, ...] = (1, 5, 10)) -> dict[str, Any]:
    cases = _read_cases(path)
    baseline_rows: list[tuple[list[RetrievalResult], set[str]]] = []
    heuristic_rows: list[tuple[list[RetrievalResult], set[str]]] = []
    for case in cases:
        candidates = [_result(candidate) for candidate in case["candidates"]]
        if any(not item.chunk_id or not item.document_id for item in candidates):
            raise ValueError(f"{case['case_id']}: candidate IDs are required")
        relevant = _relevant_ids(case)
        baseline_rows.append((_rank_baseline(candidates), relevant))
        heuristic_rows.append((rerank_legal_candidates(str(case.get("query") or ""), candidates), relevant))
    return {
        "artifact": "reranker-ablation-v1",
        "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "variants": {
            "rrf_score_order": _metrics(baseline_rows, k_values=k_values),
            "heuristic_sentence_coverage": _metrics(heuristic_rows, k_values=k_values),
        },
        "warning": "IR metrics are not legal accuracy; human adjudication and latency measurement remain required.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run_ablation(args.artifact)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"artifact": report["artifact"], "cases": report["variants"]["rrf_score_order"]["cases"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
