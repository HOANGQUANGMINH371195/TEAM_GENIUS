"""Run a live fixture in bounded batches and persist one aggregate report."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from critical_bhyt_eval import _quantile, _read_fixture, _run_cases


async def _run_batches(
    cases: list[dict], *, dataset_id: str, run_id: str, concurrency: int, batch_size: int
) -> list[dict]:
    records: list[dict] = []
    for offset in range(0, len(cases), batch_size):
        batch = cases[offset : offset + batch_size]
        records.extend(
            await _run_cases(
                batch,
                dataset_id=dataset_id,
                run_id=run_id,
                concurrency=concurrency,
            )
        )
        print(f"completed {len(records)}/{len(cases)}", flush=True)
    return records


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--qdrant-collection", required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument(
        "--case-id",
        action="append",
        default=[],
        help="Run only the named case; repeat for multiple cases.",
    )
    args = parser.parse_args()
    if not args.dataset_id.startswith("snapshot-") or "snapshot-" not in args.qdrant_collection:
        raise SystemExit("Only immutable physical snapshots are allowed")
    from dotenv import load_dotenv

    project_root = Path(__file__).resolve().parents[1]
    load_dotenv(project_root.parent / ".env", override=False)
    os.environ["QDRANT_COLLECTION"] = args.qdrant_collection
    from src.config import get_settings

    get_settings.cache_clear()
    _manifest, cases = _read_fixture(args.fixture)
    if args.case_id:
        requested = list(dict.fromkeys(args.case_id))
        by_id = {str(case.get("case_id")): case for case in cases}
        missing = [case_id for case_id in requested if case_id not in by_id]
        if missing:
            raise SystemExit(f"Unknown case ids: {', '.join(missing)}")
        cases = [by_id[case_id] for case_id in requested]
    run_id = datetime.now(UTC).strftime("live-benchmark-%Y%m%dT%H%M%SZ")
    started = time.perf_counter()
    batch_size = max(1, args.batch_size)
    records = asyncio.run(
        _run_batches(
            cases,
            dataset_id=args.dataset_id,
            run_id=run_id,
            concurrency=max(1, args.concurrency),
            batch_size=batch_size,
        )
    )
    latencies = [r["latency_ms"] for r in records if r.get("status") == "completed"]
    failures = sum(r.get("findings", {}).get("deterministic_status") == "FAIL" for r in records)
    report = {
        "run_id": run_id,
        "fixture": str(args.fixture),
        "fixture_sha256": hashlib.sha256(args.fixture.read_bytes()).hexdigest(),
        "release": {"dataset_id": args.dataset_id, "qdrant_collection": args.qdrant_collection},
        "runtime": {
            "model_name": get_settings().model_name,
            "router_model_name": get_settings().model_router_model_name,
            "model_router_enabled": get_settings().model_router_enabled,
            "model_router_timeout_seconds": get_settings().model_router_timeout_seconds,
            "query_rewrite_enabled": get_settings().query_rewrite_enabled,
            "provider_observability": "local_stage_trace",
            "trace_schema_version": 1,
            "eval_concurrency": max(1, args.concurrency),
            "eval_batch_size": batch_size,
            "selected_case_ids": [str(case.get("case_id")) for case in cases],
        },
        "deterministic_summary": {
            "cases": len(records),
            "passed": len(records) - failures,
            "failed": failures,
            "p50_latency_ms": _quantile(latencies, 0.50),
            "p95_latency_ms": _quantile(latencies, 0.95),
            "wall_time_ms": round((time.perf_counter() - started) * 1000, 2),
        },
        "release_gate": "HUMAN_REVIEW_REQUIRED",
        "review_note": "Live-provider benchmark only; independent legal adjudication remains required.",
        "cases": records,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["deterministic_summary"], ensure_ascii=False), flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
