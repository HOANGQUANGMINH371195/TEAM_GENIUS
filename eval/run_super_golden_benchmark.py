#!/usr/bin/env python3
"""Run the super golden set against the read-only production graph.

The report separates retrieval gates from answer gates. A partial run is never
reported as a full quality claim; use ``--limit 0`` for the complete set.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _norm(value: str) -> str:
    return " ".join(str(value or "").casefold().split())


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _field(item: object, name: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _select(cases: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    if limit <= 0 or limit >= len(cases):
        return cases
    # Stratify the limited run by category, preserving deterministic order.
    buckets: dict[str, list[dict[str, Any]]] = {}
    for case in cases:
        buckets.setdefault(str(case.get("category", "unknown")), []).append(case)
    selected: list[dict[str, Any]] = []
    categories = sorted(buckets)
    while len(selected) < limit and categories:
        next_categories: list[str] = []
        for category in categories:
            bucket = buckets[category]
            if bucket:
                selected.append(bucket.pop(0))
                if len(selected) >= limit:
                    break
            if bucket:
                next_categories.append(category)
        categories = next_categories
    return selected


def _score_case(case: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    answer = str(result.get("response") or "").strip()
    citations = result.get("citations") or []
    evidence = result.get("retrieved_evidence") or []
    relations = result.get("graph_results") or []
    documents = {
        str(_field(item, "document_id"))
        for item in [*evidence, *citations]
        if _field(item, "document_id")
    }
    expected_documents = {str(value) for value in case.get("expected_document_ids") or []}
    category = str(case.get("category", ""))
    if category == "social_routing":
        social_ok = bool(answer) and not evidence and not citations and result.get("route") == "policy"
        return {
            "retrieval_pass": social_ok,
            "answer_pass": social_ok,
            "document_recall": 1.0,
            "relation_pass": True,
            "latency_ms": result.get("latency_ms", 0.0),
            "provider_calls": 0 if result.get("route") == "policy" else None,
        }
    doc_recall = 1.0 if not expected_documents else len(expected_documents & documents) / len(expected_documents)
    expected_relation = str(case.get("expected_relationship_id") or "")
    expected_relation_type = str(case.get("expected_relation") or (case.get("gold_facts") or {}).get("expected_relation") or "")
    expected_scope = str(case.get("expected_scope") or (case.get("gold_facts") or {}).get("expected_scope") or "")
    expected_direction = str(case.get("expected_direction") or "")
    expected_status = str(case.get("expected_serving_status") or "")
    expected_status_candidate = str(case.get("expected_status_candidate") or "")
    expected_span = case.get("expected_source_span") or case.get("source_span") or []
    expected_hash = str(case.get("expected_evidence_sha256") or "")
    expected_text_sha = str(case.get("expected_text_sha256") or (case.get("gold_facts") or {}).get("expected_text_sha256") or "")
    expected_facts = (case.get("gold_facts") or {}).get("expected_facts") or case.get("expected_facts") or []
    matching_relation = next(
        (item for item in relations if not expected_relation or str(_field(item, "relationship_id")) == expected_relation),
        None,
    )
    relation_pass = not expected_relation or matching_relation is not None
    if matching_relation is not None:
        relation_pass = relation_pass and (
            not expected_relation_type
            or _norm(_field(matching_relation, "relation_type", _field(matching_relation, "description", ""))) == _norm(expected_relation_type)
        )
        relation_pass = relation_pass and (not expected_scope or _norm(_field(matching_relation, "scope", "")) == _norm(expected_scope))
        relation_pass = relation_pass and (not expected_hash or str(_field(matching_relation, "evidence_sha256", "")) == expected_hash)
        relation_pass = relation_pass and (not expected_direction or _norm(_field(matching_relation, "direction", "")) == _norm(expected_direction))
        relation_pass = relation_pass and (not expected_status or _norm(_field(matching_relation, "serving_status", "")) == _norm(expected_status))
        if expected_span and len(expected_span) == 2:
            relation_pass = relation_pass and [
                _field(matching_relation, "source_start"), _field(matching_relation, "source_end")
            ] == expected_span
    signature = str((case.get("gold_facts") or {}).get("expected_signature") or case.get("expected_signature") or "")
    signature_pass = bool(signature) and (
        _norm(signature) in _norm(answer)
        or any(_norm(signature) == _norm(_field(item, "so_ky_hieu", "")) for item in citations)
    )
    evidence_hashes = {
        str(_field(item, "text_sha256", ""))
        for item in [*evidence, *citations]
        if _field(item, "text_sha256", "")
    }
    source_pass = not expected_text_sha or expected_text_sha in evidence_hashes
    status_candidate_pass = not expected_status_candidate or (
        _norm(expected_status_candidate) in _norm(answer)
        or any(
            _norm(expected_status_candidate) == _norm(_field(item, "status_candidate", ""))
            for item in relations
        )
    )
    policy_pass = category != "policy_safety" or any(
        marker in _norm(answer)
        for marker in ("không thể", "không tiếp nhận", "không cung cấp", "không thể xác nhận")
    )
    fixture_valid, fixture_reason = True, ""
    fixture_unservable = case.get("servable") is False or bool(case.get("fixture_unservable"))
    if fixture_unservable:
        fixture_valid, fixture_reason = False, "source is external or answer_ready=false"
    if category in {"exact_deep", "exact"} and (not expected_documents or not signature):
        fixture_valid, fixture_reason = False, "missing expected document or signature"
    if category == "multi_hop_temporal" and expected_relation and not (expected_relation_type and expected_scope):
        fixture_valid, fixture_reason = False, "missing typed relation gold"
    if category == "multi_hop_temporal" and expected_relation and not expected_direction:
        fixture_valid, fixture_reason = False, "missing relation direction gold"
    if category == "multi_hop_temporal" and expected_relation and not expected_span:
        fixture_valid, fixture_reason = False, "missing relation evidence span gold"
    if category == "temporal_status" and not expected_status_candidate:
        fixture_valid, fixture_reason = False, "missing status-candidate gold"
    if category in {"thematic_synthesis", "table_numeric"} and not (expected_text_sha or expected_facts):
        fixture_valid, fixture_reason = False, "missing factual or source-text gold"
    expected_abstention = category in {"abstention", "no_answer"} or bool((case.get("gold_facts") or {}).get("expected_abstention"))
    abstention = any(marker in _norm(answer) for marker in ("không tìm thấy", "chưa thể xác minh", "chưa đủ cơ sở"))
    answer_pass = abstention if expected_abstention else bool(answer) and policy_pass and source_pass and (
        signature_pass if category in {"exact_deep", "exact"} else True
    ) and (status_candidate_pass if category == "temporal_status" else True)
    return {
        "retrieval_pass": doc_recall >= 1.0 and relation_pass,
        "answer_pass": answer_pass and fixture_valid,
        "fixture_valid": fixture_valid,
        "fixture_unservable": fixture_unservable,
        "fixture_reason": fixture_reason,
        "document_recall": round(doc_recall, 4),
        "relation_pass": relation_pass,
        "signature_pass": signature_pass,
        "source_pass": source_pass,
        "status_candidate_pass": status_candidate_pass,
        "answer_chars": len(answer),
        "citation_count": len(citations),
        "evidence_count": len(evidence),
        "latency_ms": result.get("latency_ms"),
    }


async def _run(cases: list[dict[str, Any]], concurrency: int) -> list[dict[str, Any]]:
    os.environ.setdefault("EVAL_AGENT_MODE", "read_only")
    from src.agents.graph import get_agent
    agent = get_agent()
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def one(case: dict[str, Any], index: int) -> dict[str, Any]:
        question = str(case.get("question", ""))
        queued_at = time.perf_counter()
        async with semaphore:
            started = time.perf_counter()
            try:
                # Social/policy cases must traverse production agent routing;
                # direct policy_response calls would hide provider regressions.
                result = await asyncio.wait_for(agent.ainvoke({"query": question}), timeout=180)
            except Exception as exc:
                result = {"error": f"{type(exc).__name__}: {str(exc)[:400]}"}
            service_latency_ms = round((time.perf_counter() - started) * 1000, 2)
        result = dict(result)
        result["latency_ms"] = service_latency_ms
        result["queue_latency_ms"] = round((started - queued_at) * 1000, 2)
        score = _score_case(case, result)
        if result.get("error"):
            status = "INFRA_FAILURE"
        elif score.get("fixture_unservable"):
            status = "FIXTURE_UNSERVABLE"
        elif not score.get("fixture_valid", True):
            status = "FIXTURE_INVALID"
        else:
            status = "PASS" if score["retrieval_pass"] and score["answer_pass"] else "FAIL_QUALITY"
        record = {
            "case_id": case["case_id"],
            "category": case.get("category"),
            "difficulty": case.get("difficulty"),
            "question_sha256": _sha256(question),
            "status": status,
            "error": result.get("error"),
            "answer": str(result.get("response") or "")[:4000],
            "retrieved_document_ids": sorted({
                str(_field(item, "document_id"))
                for item in [*(result.get("retrieved_evidence") or []), *(result.get("citations") or [])]
                if _field(item, "document_id")
            }),
            "score": score,
            "queue_latency_ms": result["queue_latency_ms"],
            "provider_metadata": result.get("provider_metadata") or {},
        }
        print(f"[SUPER {index}/{len(cases)}] {case['case_id']} {status} {record['score'].get('latency_ms')}ms", flush=True)
        return record

    return await asyncio.gather(*(one(case, index) for index, case in enumerate(cases, start=1)))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=0, help="0 means the complete dataset")
    parser.add_argument("--concurrency", type=int, default=2)
    args = parser.parse_args()
    rows = _load(args.dataset)
    cases = [row for row in rows if "case_id" in row]
    selected = _select(cases, args.limit)
    records = asyncio.run(_run(selected, args.concurrency))
    by_category: dict[str, dict[str, int]] = {}
    for record in records:
        bucket = by_category.setdefault(str(record["category"]), Counter())
        bucket[record["status"]] += 1
    try:
        from src.config import get_settings

        configured_model = get_settings().model_name
    except Exception:
        configured_model = os.getenv("MODEL_NAME", "")
    summary = {
        "dataset": str(args.dataset),
        "dataset_sha256": _sha256(args.dataset.read_text(encoding="utf-8")),
        "requested_cases": len(cases),
        "executed_cases": len(records),
        "servable_cases": sum(
            bool(record["score"].get("fixture_valid"))
            and not bool(record["score"].get("fixture_unservable"))
            for record in records
        ),
        "fixture_unservable_cases": sum(bool(record["score"].get("fixture_unservable")) for record in records),
        "fixture_invalid_cases": sum(not bool(record["score"].get("fixture_valid", True)) for record in records),
        "partial_run": len(records) != len(cases),
        "pass": sum(record["status"] == "PASS" for record in records),
        "fail": sum(record["status"] in {"FAIL_QUALITY", "FIXTURE_INVALID", "FIXTURE_UNSERVABLE", "INFRA_FAILURE"} for record in records),
        "by_category": {key: dict(value) for key, value in sorted(by_category.items())},
        "model": configured_model,
        "provider_metadata_observed": sum(bool(record.get("provider_metadata")) for record in records),
        "provider_metadata_complete": sum(
            bool((record.get("provider_metadata") or {}).get("model"))
            and bool((record.get("provider_metadata") or {}).get("finish_reason"))
            for record in records
        ),
        "accuracy_status": "OBSERVED_DIAGNOSTIC_ONLY",
        "concurrency": args.concurrency,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps({"summary": summary, "records": records}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    hard_fail = sum(
        record["status"] in {"FAIL_QUALITY", "FIXTURE_INVALID", "INFRA_FAILURE"}
        for record in records
    )
    return 0 if hard_fail == 0 and not summary["partial_run"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
