"""Run the seven critical BHYT prompts against an immutable staging release.

This evaluator deliberately does *not* decide that a legal answer is factually
correct.  It captures an end-to-end agent run, applies only auditable safety
and retrieval checks, and leaves the final legal review explicitly pending.
It never writes to Postgres, Qdrant, Neo4j, or the active-release pointer.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_FIXTURE = PROJECT_ROOT / "eval" / "cases" / "critical-bhyt-7.jsonl"
NO_EVIDENCE_RESPONSE = (
    "Hiện tại hệ thống không tìm thấy thông tin hoặc văn bản pháp lý phù hợp "
    "để giải đáp câu hỏi này."
)
_INTERNAL_FIELD = re.compile(
    r"\b(?:evidence_id|document_id|dataset|chunk|source_ref)\s*=", re.IGNORECASE
)


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _read_fixture(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows or not isinstance(rows[0].get("manifest"), dict):
        raise ValueError("fixture must start with a manifest row")
    manifest, cases = rows[0]["manifest"], rows[1:]
    if manifest.get("cases") != len(cases) or not cases:
        raise ValueError("fixture case count does not match its manifest")
    required = {
        "case_id", "question", "expected_status", "accepted_document_numbers",
        "required_facts", "forbidden_behavior",
    }
    for case in cases:
        missing = required - set(case)
        if missing:
            raise ValueError(f"{case.get('case_id', '<unknown>')}: missing {sorted(missing)}")
    if len({str(case["case_id"]) for case in cases}) != len(cases):
        raise ValueError("fixture has duplicate case IDs")
    return manifest, cases


def _normalise(value: str) -> str:
    return " ".join(value.casefold().split())


def _normalise_fact(value: str) -> str:
    """Normalize presentation-only numeric padding in reviewer fixtures.

    Legal sources alternate between forms such as ``5``/``05`` and
    ``6``/``06``.  The evaluator must not reward a renderer for rewriting the
    source or mark an otherwise identical fact missing merely because of that
    formatting difference.
    """
    value = _normalise(value)
    return re.sub(r"(?<!\d)0+(\d+)(?=\s|$)", r"\1", value)


def _is_abstention(answer: str) -> bool:
    """Recognize the service's bounded abstention variants."""
    normalized = _normalise(answer)
    return normalized in {
        _normalise(NO_EVIDENCE_RESPONSE),
        _normalise("Tôi chưa thể xác minh nội dung này từ nguồn chính thức có trích dẫn hợp lệ; vì vậy chưa thể đưa ra kết luận pháp lý."),
    }


def _quantile(values: Sequence[float], probability: float) -> float | None:
    """Return an interpolated quantile with a deterministic small-sample rule."""
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return round(ordered[0], 2)
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction, 2)


def _public_document_numbers(result: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for item in [*(result.get("citations") or []), *(result.get("retrieved_evidence") or [])]:
        if not isinstance(item, dict):
            continue
        number = str(item.get("document_number") or "").strip()
        if number and number not in values:
            values.append(number)
    return values


def _private_ids(result: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for item in [*(result.get("citations") or []), *(result.get("retrieved_evidence") or [])]:
        if not isinstance(item, dict):
            continue
        for field in ("document_id", "chunk_id", "dataset_id"):
            value = str(item.get(field) or "").strip()
            if len(value) >= 5:
                values.add(value)
    return values


def _leaks_internal_id(answer: str, private_ids: set[str]) -> bool:
    if _INTERNAL_FIELD.search(answer):
        return True
    return any(re.search(rf"(?<![\w./-]){re.escape(value)}(?![\w./-])", answer) for value in private_ids)


def _deterministic_findings(case: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Return mechanical failures only; factual legal review remains human-only."""
    answer = str(result.get("response") or "").strip()
    citations = result.get("citations") or []
    numbers = _public_document_numbers(result)
    accepted = {str(value).casefold() for value in case["accepted_document_numbers"]}
    accepted_found = sorted(number for number in numbers if number.casefold() in accepted)
    expected = str(case["expected_status"])
    abstained = _is_abstention(answer)
    normalized_answer = _normalise_fact(answer)
    required_missing = [
        fact for fact in case["required_facts"]
        if _normalise_fact(str(fact)) not in normalized_answer
    ]
    failures: list[str] = []
    if not answer:
        failures.append("empty_response")
    if _leaks_internal_id(answer, _private_ids(result)):
        failures.append("internal_id_leak")
    if expected == "answerable" and abstained:
        failures.append("unexpected_abstention")
    if expected.startswith("answerable") and not citations and not (
        expected == "answerable_with_currentness_caveat" and abstained
    ):
        failures.append("uncited_response")
    if expected.startswith("answerable") and not accepted_found and not (
        expected == "answerable_with_currentness_caveat" and abstained
    ):
        failures.append("accepted_authority_not_retrieved")
    # Required facts are an explicit reviewer queue, never a claim that
    # keyword matching proves legal correctness.  They are intentionally kept
    # separate from mechanical failures so a paraphrase does not masquerade
    # as a legal pass/fail decision.
    return {
        "deterministic_status": "FAIL" if failures else "PASS",
        "failures": failures,
        "abstained": abstained,
        "public_document_numbers": numbers,
        "accepted_document_numbers_found": accepted_found,
        "required_facts_missing_for_reviewer": required_missing,
        "review_flags": ["required_fact_review"] if required_missing else [],
        "citation_count": len(citations),
    }


def _safe_answer_for_report(answer: str, private_ids: set[str]) -> str:
    """Keep the review artifact useful without persisting a known opaque ID."""
    result = answer
    for value in sorted(private_ids, key=len, reverse=True):
        result = re.sub(rf"(?<![\w./-]){re.escape(value)}(?![\w./-])", "[REDACTED]", result)
    return _INTERNAL_FIELD.sub("[REDACTED_FIELD]", result)


async def _run_cases(
    cases: Sequence[dict[str, Any]], *, dataset_id: str, run_id: str
) -> list[dict[str, Any]]:
    from src.agents.graph import get_agent
    from src.services.chat import get_runtime

    runtime = get_runtime()
    # The assignment is process-local. It only tells read queries which
    # immutable release to resolve; it never changes the database's active
    # release record.
    runtime._active_release = (dataset_id, 0, time.monotonic())
    agent = get_agent()
    records: list[dict[str, Any]] = []
    for position, case in enumerate(cases, start=1):
        started = time.perf_counter()
        try:
            output = await asyncio.wait_for(agent.ainvoke({"query": case["question"]}), timeout=120)
            answer = str(output.get("response") or "").strip()
            findings = _deterministic_findings(case, output)
            public_citations = [
                {
                    "document_number": str(item.get("document_number") or ""),
                    "title": str(item.get("title") or ""),
                    "section_title": str(item.get("section_title") or ""),
                    "quote": str(item.get("quote") or "")[:1200],
                    "channels": list(item.get("channels") or []),
                    "evidence_kind": str(item.get("evidence_kind") or "passage"),
                    "source_start": item.get("source_start"),
                    "source_end": item.get("source_end"),
                    "text_sha256": str(item.get("text_sha256") or ""),
                    "provenance_verified": bool(item.get("provenance_verified")),
                }
                for item in output.get("citations") or []
                if isinstance(item, dict)
            ]
            public_evidence = [
                {
                    "document_number": str(item.get("document_number") or ""),
                    "section_title": str(item.get("section_title") or ""),
                    "channels": list(item.get("channels") or []),
                    "score": item.get("score"),
                    "quote": str(item.get("content") or "")[:1200],
                    "source_start": item.get("source_start"),
                    "source_end": item.get("source_end"),
                    "text_sha256": str(item.get("text_sha256") or ""),
                }
                for item in output.get("retrieved_evidence") or []
                if isinstance(item, dict)
            ]
            # Some LangGraph/runtime versions return the final citation state
            # without carrying the intermediate evidence list.  Citations are
            # already source-hydrated and public, so retain them as a bounded
            # evidence fallback instead of falsely reporting zero evidence.
            if not public_evidence:
                public_evidence = [
                    {
                        "document_number": item["document_number"],
                        "section_title": item["section_title"],
                        "channels": item["channels"],
                        "score": None,
                        "quote": item["quote"],
                        "source_start": item["source_start"],
                        "source_end": item["source_end"],
                        "text_sha256": item["text_sha256"],
                    }
                    for item in public_citations
                ]
            records.append(
                {
                    "case_id": case["case_id"],
                    "question": case["question"],
                    "status": "completed" if answer else "invalid_output",
                    "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                    "answer": _safe_answer_for_report(answer, _private_ids(output)),
                    "answer_sha256": hashlib.sha256(answer.encode("utf-8")).hexdigest(),
                    "citations": public_citations,
                    "claims_count": len(output.get("claims") or []),
                    "metadata": output.get("metadata") or {},
                    "retrieved_evidence": public_evidence,
                    "findings": findings,
                }
            )
        except Exception as exc:  # retain the type, not provider payloads/secrets
            error_trace = getattr(exc, "medipay_trace", {})
            records.append(
                {
                    "case_id": case["case_id"],
                    "question": case["question"],
                    "status": "agent_error",
                    "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                    "answer": "",
                    "answer_sha256": None,
                    "citations": [],
                    "claims_count": 0,
                    "metadata": {
                        "trace_id": str(error_trace.get("trace_id") or ""),
                        "retrieval_trace": error_trace,
                        "error_stage": "retrieval",
                    },
                    "retrieved_evidence": [],
                    "findings": {"deterministic_status": "FAIL", "failures": [type(exc).__name__]},
                }
            )
        print(f"[{position}/{len(cases)}] {case['case_id']} — {records[-1]['status']}", flush=True)
    await runtime.close()
    return records


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-id", required=True, help="Immutable inactive staging dataset ID")
    parser.add_argument("--qdrant-collection", required=True, help="Physical staging Qdrant collection")
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--out", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    # Keep the read-only acceptance run independent of external telemetry DNS.
    os.environ.setdefault("P151_EVAL_DISABLE_REMOTE_TRACING", "1")
    if args.qdrant_collection == "medical_legal_active":
        raise SystemExit("Refusing production alias medical_legal_active; provide a physical staging collection")
    if not args.dataset_id.startswith("snapshot-"):
        raise SystemExit("dataset-id must be an immutable snapshot identifier")
    if "snapshot-" not in args.qdrant_collection:
        raise SystemExit("qdrant-collection must be a physical snapshot collection")

    # The developer .env is intentionally one directory above the deployable
    # application. Loading it here avoids copying credentials into P-151.
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT.parent / ".env", override=False)
    os.environ["QDRANT_COLLECTION"] = args.qdrant_collection
    from src.config import get_settings

    get_settings.cache_clear()
    manifest, cases = _read_fixture(args.fixture)
    run_id = datetime.now(UTC).strftime("critical-bhyt-%Y%m%dT%H%M%SZ")
    records = asyncio.run(_run_cases(cases, dataset_id=args.dataset_id, run_id=run_id))
    latencies = sorted(record["latency_ms"] for record in records if record["status"] == "completed")
    failures = sum(record["findings"]["deterministic_status"] == "FAIL" for record in records)
    report = {
        "run_id": run_id,
        "fixture": str(args.fixture),
        "fixture_sha256": hashlib.sha256(args.fixture.read_bytes()).hexdigest(),
        "release": {"dataset_id": args.dataset_id, "qdrant_collection": args.qdrant_collection},
        "runtime": {
            "model_name": get_settings().model_name,
            "query_rewrite_enabled": get_settings().query_rewrite_enabled,
            "provider_observability": "local_stage_trace",
            "trace_schema_version": 1,
        },
        "deterministic_summary": {
            "cases": len(records),
            "passed": len(records) - failures,
            "failed": failures,
            "p50_latency_ms": _quantile(latencies, 0.50),
            "p95_latency_ms": _quantile(latencies, 0.95),
        },
        "release_gate": "HUMAN_REVIEW_REQUIRED",
        "review_note": "A deterministic pass proves only routing/citation/safety checks. Legal factual correctness and repeated-run p95 remain human-review requirements.",
        "cases": records,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["deterministic_summary"], ensure_ascii=False), flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
