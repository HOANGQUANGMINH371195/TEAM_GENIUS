"""Run a real-provider retrieval/generation parameter sweep.

The sweep is intentionally process-isolated: LangChain/OpenAI wrappers and
the GraphRAG runtime are cached singletons, so changing settings in one
process would otherwise benchmark stale configuration.  Every worker reads
the ignored project ``.env``, targets one immutable release, and writes only
redacted evaluator records plus bounded stage traces.

This is a measurement harness, not a legal judge.  The deterministic findings
are routing/citation/safety checks; factual legal correctness still requires an
independent reviewer.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
DEFAULT_FIXTURE = PROJECT_ROOT / "eval" / "cases" / "accuracy-bhyt-30.jsonl"
DEFAULT_DATASET = "snapshot-c439751724ab7f10"
DEFAULT_COLLECTION = "medical_legal_hybrid_snapshot-c439751724ab7f10"

# Keep retrieval channels comparable while sweeping the two model controls.
# The three retrieval widths deliberately exercise the production caps (24,
# 24 and 30 passage candidates respectively) instead of pretending that a
# larger env value bypasses the route planner.
PROFILES: tuple[dict[str, Any], ...] = (
    {
        "name": "fast",
        "llm_max_output_tokens": 320,
        "llm_reasoning_effort": "none",
        "retrieval_top_k": 5,
        "retrieval_candidate_k": 36,
        "max_llm_evidence": 8,
        "max_context_tokens": 12_000,
        "max_chunks_per_document": 3,
    },
    {
        "name": "balanced",
        "llm_max_output_tokens": 600,
        "llm_reasoning_effort": "low",
        "retrieval_top_k": 8,
        "retrieval_candidate_k": 60,
        "max_llm_evidence": 12,
        "max_context_tokens": 24_000,
        "max_chunks_per_document": 4,
    },
    {
        "name": "quality",
        "llm_max_output_tokens": 900,
        "llm_reasoning_effort": "medium",
        "retrieval_top_k": 10,
        "retrieval_candidate_k": 90,
        "max_llm_evidence": 16,
        "max_context_tokens": 32_000,
        "max_chunks_per_document": 4,
    },
)


def _quantile(values: Sequence[float], probability: float) -> float | None:
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


def _read_fixture(path: Path, limit: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows or not isinstance(rows[0].get("manifest"), dict):
        raise ValueError("fixture must begin with a manifest row")
    manifest, cases = rows[0]["manifest"], rows[1:]
    if not cases:
        raise ValueError("fixture has no cases")
    selected = cases[: max(1, min(limit, len(cases)))]
    return manifest, selected


def _stage_rows(records: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in records:
        metadata = record.get("metadata") or {}
        retrieval = metadata.get("retrieval_trace") or {}
        for stage in retrieval.get("stages") or []:
            if isinstance(stage, dict) and isinstance(stage.get("duration_ms"), (int, float)):
                rows.append({"case_id": record.get("case_id"), **stage})
        generation = metadata.get("generation_trace") or {}
        if isinstance(generation, dict) and isinstance(generation.get("duration_ms"), (int, float)):
            rows.append(
                {
                    "case_id": record.get("case_id"),
                    "stage": "generation",
                    "duration_ms": generation["duration_ms"],
                    "outcome": generation.get("outcome", "unknown"),
                    "usage": generation.get("usage") or {},
                }
            )
    return rows


def _summarise(profile: dict[str, Any], records: Sequence[dict[str, Any]], elapsed_ms: float) -> dict[str, Any]:
    completed = [record for record in records if record.get("status") == "completed"]
    latencies = [float(record["latency_ms"]) for record in completed if record.get("latency_ms") is not None]
    failures = [record for record in records if (record.get("findings") or {}).get("deterministic_status") == "FAIL"]
    traces = _stage_rows(records)
    stage_durations: defaultdict[str, list[float]] = defaultdict(list)
    usage_totals: defaultdict[str, int] = defaultdict(int)
    for row in traces:
        stage = str(row.get("stage") or "unknown")
        duration = row.get("duration_ms")
        if isinstance(duration, (int, float)):
            stage_durations[stage].append(float(duration))
        for key, value in (row.get("usage") or {}).items():
            if isinstance(value, (int, float)):
                usage_totals[str(key)] += int(value)
    stage_summary = {
        stage: {
            "count": len(values),
            "p50_ms": _quantile(values, 0.50),
            "p95_ms": _quantile(values, 0.95),
            "mean_ms": round(mean(values), 2),
        }
        for stage, values in sorted(stage_durations.items())
    }
    return {
        "profile": profile,
        "cases": len(records),
        "completed": len(completed),
        "deterministic_passed": len(records) - len(failures),
        "deterministic_failed": len(failures),
        "availability": round(len(completed) / len(records), 4) if records else 0.0,
        "latency_ms": {
            "min": min(latencies) if latencies else None,
            "p50": _quantile(latencies, 0.50),
            "p95": _quantile(latencies, 0.95),
            "max": max(latencies) if latencies else None,
            "mean": round(mean(latencies), 2) if latencies else None,
        },
        "trace_completeness": {
            "records_with_stage_trace": sum(bool((record.get("metadata") or {}).get("retrieval_trace")) for record in records),
            "stage_events": len(traces),
            "stages": stage_summary,
        },
        "usage_totals": dict(sorted(usage_totals.items())),
        "wall_time_ms": round(elapsed_ms, 2),
    }


def _worker(args: argparse.Namespace) -> int:
    from dotenv import load_dotenv

    env_path = PROJECT_ROOT / ".env"
    if env_path.is_file():
        load_dotenv(env_path, override=False)
    os.environ["QDRANT_COLLECTION"] = args.qdrant_collection
    os.environ["LLM_MAX_OUTPUT_TOKENS"] = str(args.llm_max_output_tokens)
    os.environ["LLM_REASONING_EFFORT"] = args.llm_reasoning_effort
    os.environ["RETRIEVAL_TOP_K"] = str(args.retrieval_top_k)
    os.environ["RETRIEVAL_CANDIDATE_K"] = str(args.retrieval_candidate_k)
    os.environ["MAX_LLM_EVIDENCE"] = str(args.max_llm_evidence)
    os.environ["MAX_CONTEXT_TOKENS"] = str(args.max_context_tokens)
    os.environ["MAX_CHUNKS_PER_DOCUMENT"] = str(args.max_chunks_per_document)
    # Query rewrite is kept off for this controlled comparison. It is an
    # extra provider call and high-risk legal routes intentionally bypass it.
    os.environ["QUERY_REWRITE_ENABLED"] = "false"
    os.environ["P151_EVAL_DISABLE_REMOTE_TRACING"] = "1" if args.disable_remote_tracing else "0"

    from src.config import get_settings

    get_settings.cache_clear()
    from eval.critical_bhyt_eval import _read_fixture, _run_cases

    _manifest, cases = _read_fixture(Path(args.fixture))
    cases = cases[: args.limit]
    started = time.perf_counter()
    records = asyncio.run(
        _run_cases(
            cases,
            dataset_id=args.dataset_id,
            run_id=args.run_id,
            concurrency=args.concurrency,
        )
    )
    elapsed_ms = (time.perf_counter() - started) * 1000
    profile_config = next(item for item in PROFILES if item["name"] == args.profile)
    report = {
        "schema_version": 1,
        "run_id": args.run_id,
        "fixture": str(args.fixture),
        "fixture_sha256": hashlib.sha256(Path(args.fixture).read_bytes()).hexdigest(),
        "release": {"dataset_id": args.dataset_id, "qdrant_collection": args.qdrant_collection},
        "runtime": {
            "model_name": get_settings().model_name,
            "query_rewrite_enabled": get_settings().query_rewrite_enabled,
            "concurrency": args.concurrency,
            "remote_tracing": not args.disable_remote_tracing,
            "provider": "real_openai_provider",
        },
        "summary": _summarise(profile_config, records, elapsed_ms),
        # _run_cases already redacts private IDs in answers and bounds quotes.
        # Metadata contains only local stage timings and provider usage.
        "cases": records,
    }
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False), flush=True)
    return 0


def _parent(args: argparse.Namespace) -> int:
    _manifest, cases = _read_fixture(Path(args.fixture), args.limit)
    if len(cases) < 20:
        raise SystemExit("refusing a sweep with fewer than 20 cases; use a smoke probe separately")
    run_id = args.run_id or datetime.now(UTC).strftime("retrieval-sweep-%Y%m%dT%H%M%SZ")
    root = Path(args.out)
    root.mkdir(parents=True, exist_ok=True)
    summaries: list[dict[str, Any]] = []
    for profile in PROFILES:
        output = root / f"{run_id}-{profile['name']}.json"
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--worker",
            "--profile", profile["name"],
            "--run-id", run_id,
            "--fixture", str(args.fixture),
            "--limit", str(args.limit),
            "--dataset-id", args.dataset_id,
            "--qdrant-collection", args.qdrant_collection,
            "--concurrency", str(args.concurrency),
            "--out", str(output),
        ]
        for key in (
            "llm_max_output_tokens", "llm_reasoning_effort", "retrieval_top_k",
            "retrieval_candidate_k", "max_llm_evidence", "max_context_tokens",
            "max_chunks_per_document",
        ):
            command.extend([f"--{key.replace('_', '-')}", str(profile[key])])
        if args.disable_remote_tracing:
            command.append("--disable-remote-tracing")
        print(f"\n=== profile {profile['name']} ({len(cases)} cases) ===", flush=True)
        started = time.perf_counter()
        if output.is_file():
            print("reusing existing worker artifact", flush=True)
        else:
            completed = subprocess.run(command, cwd=PROJECT_ROOT, text=True, capture_output=True)
            if completed.stdout:
                print(completed.stdout, end="", flush=True)
            if completed.stderr:
                print(completed.stderr, end="", file=sys.stderr, flush=True)
            if completed.returncode != 0 or not output.is_file():
                raise SystemExit(f"profile {profile['name']} failed with exit code {completed.returncode}")
        report = json.loads(output.read_text(encoding="utf-8"))
        summary = report["summary"]
        # Worker artifacts produced by v1.0 stored only the profile name;
        # normalize them while re-aggregating so the public report remains
        # self-describing without rerunning paid provider calls.
        if isinstance(summary.get("profile"), str):
            summary["profile"] = profile
        summaries.append(summary)
        print(f"profile wall time: {round((time.perf_counter() - started) * 1000, 2)} ms", flush=True)

    aggregate = {
        "schema_version": 1,
        "run_id": run_id,
        "fixture": str(args.fixture),
        "fixture_sha256": hashlib.sha256(Path(args.fixture).read_bytes()).hexdigest(),
        "release": {"dataset_id": args.dataset_id, "qdrant_collection": args.qdrant_collection},
        "method": {
            "cases_per_profile": len(cases),
            "profiles": [item["profile"] for item in summaries],
            "real_provider": True,
            "remote_tracing": not args.disable_remote_tracing,
            "independent_legal_adjudication": "required",
        },
        "profiles": summaries,
    }
    aggregate_path = root / f"{run_id}-aggregate.json"
    aggregate_path.write_text(json.dumps(aggregate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    trace_path = root / f"{run_id}-trace.jsonl"
    with trace_path.open("w", encoding="utf-8") as handle:
        for summary in summaries:
            handle.write(json.dumps({"run_id": run_id, **summary}, ensure_ascii=False) + "\n")
    ai_log = PROJECT_ROOT / ".ai-log" / "retrieval-sweep.jsonl"
    ai_log.parent.mkdir(parents=True, exist_ok=True)
    with ai_log.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "ts": datetime.now(UTC).isoformat(),
                    "event": "RetrievalSweep",
                    "run_id": run_id,
                    "model": "gpt-5.6-luna",
                    "cases_per_profile": len(cases),
                    "profiles": [
                        {
                            "name": item["profile"]["name"],
                            "reasoning_effort": item["profile"]["llm_reasoning_effort"],
                            "max_output_tokens": item["profile"]["llm_max_output_tokens"],
                            "p50_ms": item["latency_ms"]["p50"],
                            "p95_ms": item["latency_ms"]["p95"],
                            "deterministic_passed": item["deterministic_passed"],
                        }
                        for item in summaries
                    ],
                },
                ensure_ascii=False,
            )
            + "\n"
        )
    print(json.dumps({"aggregate": str(aggregate_path), "trace": str(trace_path), "ai_log": str(ai_log), "profiles": summaries}, ensure_ascii=False, indent=2), flush=True)
    return 0


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--profile", default="")
    parser.add_argument("--run-id", default="", help="Reuse an existing run ID when aggregating worker artifacts")
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--dataset-id", default=DEFAULT_DATASET)
    parser.add_argument("--qdrant-collection", default=DEFAULT_COLLECTION)
    parser.add_argument("--out", type=Path, default=PROJECT_ROOT / "eval" / "results")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--disable-remote-tracing", action="store_true")
    for key, default in (
        ("llm_max_output_tokens", 900), ("llm_reasoning_effort", "medium"),
        ("retrieval_top_k", 10), ("retrieval_candidate_k", 60),
        ("max_llm_evidence", 12), ("max_context_tokens", 32_000),
        ("max_chunks_per_document", 4),
    ):
        parser.add_argument(f"--{key.replace('_', '-')}", default=default, type=int if isinstance(default, int) else str)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.worker:
        profile = next((item for item in PROFILES if item["name"] == args.profile), None)
        if profile is None:
            raise SystemExit(f"unknown profile: {args.profile}")
        return _worker(args)
    return _parent(args)


if __name__ == "__main__":
    raise SystemExit(main())
