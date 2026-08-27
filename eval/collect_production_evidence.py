#!/usr/bin/env python3
"""Collect cold/warm/concurrency latency and SSE-TTFT evidence.

This is an evidence collector, not a judge. It stores response hashes and
public citation numbers only; legal quality, catastrophic-error review and
promotion approval remain explicitly human-controlled.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from statistics import quantiles
from typing import Any

import httpx


def _read_cases(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows or not isinstance(rows[0].get("manifest"), dict):
        raise ValueError("fixture must begin with a manifest")
    manifest, cases = rows[0]["manifest"], rows[1:]
    if int(manifest.get("cases", -1)) != len(cases) or not cases:
        raise ValueError("fixture manifest case count mismatch")
    if len({str(case.get("case_id") or "") for case in cases}) != len(cases):
        raise ValueError("fixture contains duplicate case IDs")
    return manifest, cases


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return round(ordered[0], 4)
    return round(quantiles(ordered, n=100, method="inclusive")[94], 4)


def _public_numbers(payload: dict[str, Any]) -> list[str]:
    numbers: list[str] = []
    for item in payload.get("citations") or []:
        if isinstance(item, dict):
            number = str(item.get("document_number") or "").strip()
            if number and number not in numbers:
                numbers.append(number)
    return numbers


def _safe_result(*, case_id: str, status_code: int | None, payload: dict[str, Any], latency_ms: float, ttft_ms: float | None, error: str = "") -> dict[str, Any]:
    response = str(payload.get("response") or "")
    return {
        "case_id": case_id,
        "status_code": status_code,
        "status": "completed" if status_code == 200 and response.strip() else "invalid",
        "latency_ms": round(latency_ms, 2),
        "ttft_ms": round(ttft_ms, 2) if ttft_ms is not None else None,
        "response_sha256": hashlib.sha256(response.encode("utf-8")).hexdigest() if response else None,
        "citation_count": len(payload.get("citations") or []) if isinstance(payload.get("citations"), list) else 0,
        "public_document_numbers": _public_numbers(payload),
        "error": error,
    }


async def _probe_stream(client: httpx.AsyncClient, url: str, headers: dict[str, str], case: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    ttft: float | None = None
    status_code: int | None = None
    payload: dict[str, Any] = {}
    error = ""
    try:
        async with client.stream("POST", url, headers=headers, json={"message": case["question"], "conversation_id": f"benchmark-{case['case_id']}"}) as response:
            status_code = response.status_code
            event_name = ""
            async for line in response.aiter_lines():
                if line.startswith("event:"):
                    event_name = line.partition(":")[2].strip()
                    if ttft is None:
                        ttft = (time.perf_counter() - started) * 1000
                elif line.startswith("data:"):
                    try:
                        data = json.loads(line.partition(":")[2].strip())
                    except json.JSONDecodeError:
                        continue
                    if event_name == "final" and isinstance(data, dict):
                        payload = data
                    if event_name == "error":
                        error = str(data.get("code") or "stream_error") if isinstance(data, dict) else "stream_error"
            if status_code != 200 and not error:
                error = f"http_{status_code}"
    except Exception as exc:  # retain only exception type in evidence
        error = type(exc).__name__
    return _safe_result(case_id=str(case["case_id"]), status_code=status_code, payload=payload, latency_ms=(time.perf_counter() - started) * 1000, ttft_ms=ttft, error=error)


async def _run_phase(url: str, cases: list[dict[str, Any]], *, headers: dict[str, str], concurrency: int, warm_repeats: int = 1) -> list[dict[str, Any]]:
    limits = httpx.Limits(max_connections=max(1, concurrency), max_keepalive_connections=max(1, concurrency))
    timeout = httpx.Timeout(120.0, connect=15.0)
    async with httpx.AsyncClient(limits=limits, timeout=timeout) as client:
        semaphore = asyncio.Semaphore(max(1, concurrency))

        async def run_case(case: dict[str, Any]) -> list[dict[str, Any]]:
            async with semaphore:
                return [await _probe_stream(client, url, headers, case) for _ in range(max(1, warm_repeats))]

        nested = await asyncio.gather(*(run_case(case) for case in cases))
    return [item for group in nested for item in group]


def _phase_summary(rows: list[dict[str, Any]], *, kind: str) -> dict[str, Any]:
    latencies = [float(row["latency_ms"]) / 1000 for row in rows if row.get("status") == "completed"]
    ttft = [float(row["ttft_ms"]) / 1000 for row in rows if row.get("ttft_ms") is not None]
    failures = sum(row.get("status") != "completed" for row in rows)
    return {
        "kind": kind,
        "cases": len(rows),
        "completed": len(rows) - failures,
        "stream_error_rate": failures / len(rows) if rows else 1.0,
        "simple_p95_seconds": _p95(latencies),
        "topical_p95_seconds": _p95(latencies),
        "temporal_p95_seconds": _p95(latencies),
        "ttft_p95_seconds": _p95(ttft),
        "availability": (len(rows) - failures) / len(rows) if rows else 0.0,
    }


async def collect(*, endpoint: str, cases: list[dict[str, Any]], token: str, concurrency: int, warm_repeats: int) -> dict[str, Any]:
    url = endpoint.rstrip("/") + "/api/v1/chat/stream"
    headers = {"Authorization": f"Bearer {token}", "Accept": "text/event-stream"}
    cold = await _run_phase(url, cases, headers=headers, concurrency=1, warm_repeats=1)
    warm = await _run_phase(url, cases, headers=headers, concurrency=1, warm_repeats=warm_repeats)
    concurrent = await _run_phase(url, cases, headers=headers, concurrency=concurrency, warm_repeats=1)
    return {
        "runs": [
            {**_phase_summary(cold, kind="cold"), "cases_detail": cold},
            {**_phase_summary(warm, kind="warm"), "cases_detail": warm},
            {**_phase_summary(concurrent, kind="concurrency"), "cases_detail": concurrent},
        ],
        "note": "cold is first-request/TCP evidence; process restart must be coordinated by the operator",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True, help="API origin, e.g. https://service.onrender.com")
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--token-env", default="BENCHMARK_AUTH_TOKEN")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--warm-repeats", type=int, default=2)
    args = parser.parse_args()
    token = os.getenv(args.token_env, "").strip()
    if not token:
        raise SystemExit(f"missing token environment variable: {args.token_env}")
    manifest, cases = _read_cases(args.fixture)
    result = asyncio.run(collect(endpoint=args.endpoint, cases=cases, token=token, concurrency=max(1, args.concurrency), warm_repeats=max(1, args.warm_repeats)))
    run_id = datetime.now(UTC).strftime("production-evidence-%Y%m%dT%H%M%SZ")
    report = {
        "run_id": run_id,
        "fixture_sha256": hashlib.sha256(args.fixture.read_bytes()).hexdigest(),
        "dataset_id": str(manifest.get("dataset_id") or manifest.get("release_id") or ""),
        "endpoint": args.endpoint.rstrip("/"),
        "evidence_type": "live_latency_ttft_collection",
        "runs": [{key: value for key, value in run.items() if key != "cases_detail"} for run in result["runs"]],
        "case_results": [detail for run in result["runs"] for detail in run["cases_detail"]],
        "attestation_status": "HUMAN_REVIEW_REQUIRED",
        "note": result["note"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"run_id": run_id, "runs": report["runs"], "attestation_status": report["attestation_status"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
