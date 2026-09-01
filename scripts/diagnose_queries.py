"""Run a small real-provider diagnostic and explain where each query fails.

Unlike a score-only benchmark, this writes one JSON object per query with the
route, stage timings, bounded retrieval lineage, authority checks, final
evidence and rendered answer.  Internal document/chunk primary keys are
hashed; public document numbers and titles remain available for legal review.

Examples:
  uv run python scripts/diagnose_queries.py --query "Mức đóng BHYT hiện nay là bao nhiêu?"
  uv run python scripts/diagnose_queries.py --input eval/cases/diagnostic.jsonl
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _queries(args: argparse.Namespace) -> list[str]:
    values = [value.strip() for value in args.query if value.strip()]
    if args.input:
        for line in Path(args.input).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            value: Any = json.loads(line)
            if isinstance(value, str):
                values.append(value.strip())
            elif isinstance(value, dict) and isinstance(value.get("query"), str):
                values.append(value["query"].strip())
    return list(dict.fromkeys(value for value in values if value))


def _failure_class(metadata: dict[str, Any], result: dict[str, Any]) -> str:
    if metadata.get("input_guardrail") not in {None, "allow"}:
        return "input_guardrail"
    if metadata.get("verification_failed"):
        return "verification"
    if not result.get("retrieved_evidence") and not result.get("response"):
        return "retrieval_empty"
    if metadata.get("generation_trace", {}).get("outcome") in {"error", "schema_error", "empty"}:
        return "generation"
    if metadata.get("guardrail_failed"):
        return "output_guardrail"
    return "none"


def _public_result(result: dict[str, Any], elapsed_ms: float) -> dict[str, Any]:
    metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    evidence = result.get("retrieved_evidence") or []
    citations = result.get("citations") or []
    return {
        "elapsed_ms": round(elapsed_ms, 2),
        "route": metadata.get("route_plan", {}),
        "route_intent": metadata.get("route_intent", ""),
        "model_route": metadata.get("model_route", {}),
        "stage_timings_ms": {
            key: metadata[key]
            for key in (
                "retrieval_ms", "planner_ms", "planner_followup_ms", "context_ms",
                "verification_ms", "generation_ms", "guardrail_ms",
            )
            if key in metadata
        },
        "retrieval_trace": metadata.get("retrieval_trace", {}),
        "evidence": metadata.get("evidence_diagnostics", []),
        "evidence_count": len(evidence),
        "relation_count": len(result.get("graph_results") or []),
        "authority": {
            "verified_evidence_count": sum(
                1 for item in evidence if getattr(item, "legal_status_verified", False)
            ),
            "public_citation_count": len(citations),
            "verification_failed": bool(metadata.get("verification_failed")),
            "verification_failed_reason": metadata.get("verification_failed_reason", ""),
        },
        "generation": metadata.get("generation_trace", {}),
        "response": str(result.get("response") or ""),
        "citations": [
            {
                "title": getattr(item, "title", ""),
                "document_number": getattr(item, "document_number", ""),
                "section_title": getattr(item, "section_title", ""),
                "evidence_kind": getattr(item, "evidence_kind", ""),
                "provenance_verified": bool(getattr(item, "provenance_verified", False)),
            }
            for item in citations
        ],
        "failure_class": _failure_class(metadata, result),
    }


async def _run(queries: list[str]) -> list[dict[str, Any]]:
    from src.agents.graph import get_agent

    agent = get_agent()
    rows: list[dict[str, Any]] = []
    for index, query in enumerate(queries, start=1):
        started = time.perf_counter()
        try:
            result = await agent.ainvoke({"query": query})
            row = _public_result(result if isinstance(result, dict) else {}, (time.perf_counter() - started) * 1000)
            row["ok"] = True
        except Exception as exc:  # diagnostics must preserve the failing query
            row = {
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                "ok": False,
                "failure_class": type(exc).__name__,
                "error": str(exc)[:500],
            }
        row["index"] = index
        row["query"] = query
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", action="append", default=[], help="Question to run; repeatable")
    parser.add_argument("--input", type=Path, help="JSONL containing strings or {query: ...}")
    parser.add_argument("--output", type=Path, default=Path("eval/results/query-diagnostics.jsonl"))
    args = parser.parse_args()
    queries = _queries(args)
    if not queries:
        parser.error("provide --query or --input")
    rows = asyncio.run(_run(queries))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    failures: dict[str, int] = {}
    for row in rows:
        key = str(row.get("failure_class", "unknown"))
        failures[key] = failures.get(key, 0) + 1
    print(json.dumps({"queries": len(rows), "failures": failures, "output": str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
