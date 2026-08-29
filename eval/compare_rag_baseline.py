"""Compare the project run with a small, reproducible lexical RAG baseline.

This is intentionally an IR baseline, not a second LLM.  It indexes the raw
metadata/content CSVs, ranks documents by token overlap, and reports retrieval
and answer-surface metrics separately.  A baseline with no generated answer is
never awarded a factuality score.

Examples:
  python eval/compare_rag_baseline.py build-baseline \
    --gold eval/results/canonical-live-ragas/golden_dataset.jsonl \
    --source-dir data/raw --out /tmp/lexical-baseline.jsonl
  python eval/compare_rag_baseline.py compare \
    --gold eval/results/canonical-live-ragas/golden_dataset.jsonl \
    --current eval/results/canonical-live-ragas/actual_answers.jsonl \
    --baseline /tmp/lexical-baseline.jsonl --out /tmp/rag-comparison.json
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import unicodedata
from pathlib import Path
from statistics import mean, median
from typing import Any

SOURCE_FILES = ("metadata_bhyt.csv", "metadata_vien_phi.csv")
FALLBACK = "Hiện tại hệ thống không tìm thấy thông tin"
TOKEN_RE = re.compile(r"[\wÀ-ỹ]+", re.UNICODE)
STOPWORDS = {
    "và", "có", "là", "từ", "ngày", "nào", "này", "văn", "bản", "số", "hiệu",
    "thuộc", "nhóm", "nội", "dung", "hiện", "còn", "được", "không", "thông", "tin",
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def tokens(value: str) -> set[str]:
    value = unicodedata.normalize("NFC", value.casefold())
    return {token for token in TOKEN_RE.findall(value) if token not in STOPWORDS and len(token) > 1}


def read_csv(path: Path) -> list[dict[str, str]]:
    csv.field_size_limit(10_000_000)
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [{str(k): str(v or "").strip() for k, v in row.items()} for row in csv.DictReader(handle)]


def corpus(source_dir: Path) -> list[dict[str, str]]:
    content = {row.get("id", ""): row.get("content_html", "") for row in read_csv(source_dir / "content.csv")}
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for filename in SOURCE_FILES:
        for row in read_csv(source_dir / filename):
            document_id = row.get("id", "")
            if not document_id or document_id in seen:
                continue
            seen.add(document_id)
            rows.append({
                "document_id": document_id,
                "title": row.get("title", ""),
                "so_ky_hieu": row.get("so_ky_hieu", ""),
                "text": " ".join((row.get("title", ""), row.get("so_ky_hieu", ""), content.get(document_id, ""))),
            })
    for row in rows:
        row["_tokens"] = tokens(row["text"])
    return rows


def rank(query: str, documents: list[dict[str, str]], k: int) -> list[dict[str, Any]]:
    query_tokens = tokens(query)
    scored: list[tuple[float, dict[str, str]]] = []
    for document in documents:
        document_tokens = document["_tokens"]
        overlap = len(query_tokens & document_tokens)
        score = overlap / math.sqrt(max(1, len(query_tokens) * len(document_tokens)))
        # Exact public identifiers are a legitimate strength of a plain lexical baseline.
        if document["so_ky_hieu"].casefold() in query.casefold() and document["so_ky_hieu"]:
            score += 1.0
        if document["title"].casefold() in query.casefold() and document["title"]:
            score += 0.5
        scored.append((score, document))
    scored.sort(key=lambda item: (-item[0], item[1]["document_id"]))
    return [
        {
            "document_id": document["document_id"],
            "title": document["title"],
            "score": round(score, 8),
            "channels": ["lexical_baseline"],
        }
        for score, document in scored[:k]
        if score > 0
    ]


def build_baseline(gold_path: Path, source_dir: Path, output: Path, k: int) -> None:
    documents = corpus(source_dir)
    rows = []
    for case in read_jsonl(gold_path):
        question = str(case.get("agent_input", {}).get("messages", [{}])[-1].get("content", ""))
        rows.append({
            "case_id": case["case_id"],
            "answer": "",
            "latency_ms": 0.0,
            "retrieved_contexts": rank(question, documents, k),
            "baseline": "lexical_token_overlap",
        })
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def expected_ids(case: dict[str, Any]) -> set[str]:
    return {str(value) for value in case.get("reference_context_ids", []) if value}


def answer_surface(case: dict[str, Any], answer: str) -> float | None:
    """Return required-value coverage; do not call it legal factual accuracy."""
    facts = case.get("required_facts") or []
    values = [str(item.get("value", "")) for item in facts if isinstance(item, dict) and item.get("value")]
    if not values:
        return None
    normal = unicodedata.normalize("NFC", answer).casefold()
    return sum(value.casefold() in normal for value in values) / len(values)


def evaluate(name: str, cases: dict[str, dict[str, Any]], run: list[dict[str, Any]], k_values: tuple[int, ...]) -> dict[str, Any]:
    by_id = {str(row.get("case_id")): row for row in run}
    retrieval: dict[str, float] = {}
    surfaces: list[float] = []
    latencies: list[float] = []
    fallbacks = 0
    for k in k_values:
        hits = 0
        eligible = 0
        for case_id, case in cases.items():
            expected = expected_ids(case)
            row = by_id.get(case_id, {})
            retrieved = [str(item.get("document_id")) for item in row.get("retrieved_contexts", []) if item.get("document_id")]
            if expected:
                eligible += 1
                hits += int(bool(expected.intersection(retrieved[:k])))
        retrieval[f"hit@{k}"] = hits / eligible if eligible else 0.0
    for case_id, case in cases.items():
        row = by_id.get(case_id, {})
        answer = str(row.get("answer") or "")
        surface = answer_surface(case, answer)
        if surface is not None and answer:
            surfaces.append(surface)
        if FALLBACK.casefold() in answer.casefold():
            fallbacks += 1
        if row.get("latency_ms") is not None:
            try:
                latencies.append(float(row["latency_ms"]))
            except (TypeError, ValueError):
                pass
    return {
        "name": name,
        "cases": len(cases),
        "retrieval": retrieval,
        "answer_surface_fact_coverage": mean(surfaces) if surfaces else None,
        "answer_surface_denominator": len(surfaces),
        "fallback_count": fallbacks,
        "latency_ms": {
            "n": len(latencies),
            "mean": mean(latencies) if latencies else None,
            "p50": median(latencies) if latencies else None,
            "max": max(latencies) if latencies else None,
        },
    }


def compare(args: argparse.Namespace) -> None:
    gold_rows = read_jsonl(args.gold)
    cases = {str(row["case_id"]): row for row in gold_rows}
    current = evaluate("project", cases, read_jsonl(args.current), tuple(args.k))
    baseline = evaluate("ordinary_lexical_rag", cases, read_jsonl(args.baseline), tuple(args.k))
    result = {
        "warning": "Retrieval hit and surface fact coverage are not legal accuracy; human adjudication is required.",
        "gold_cases": len(cases),
        "project": current,
        "baseline": baseline,
        "delta_project_minus_baseline": {
            metric: (current["retrieval"][metric] - baseline["retrieval"][metric])
            for metric in current["retrieval"]
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build-baseline")
    build.add_argument("--gold", type=Path, required=True)
    build.add_argument("--source-dir", type=Path, required=True)
    build.add_argument("--out", type=Path, required=True)
    build.add_argument("--k", type=int, default=20)
    run = sub.add_parser("compare")
    run.add_argument("--gold", type=Path, required=True)
    run.add_argument("--current", type=Path, required=True)
    run.add_argument("--baseline", type=Path, required=True)
    run.add_argument("--out", type=Path, required=True)
    run.add_argument("--k", type=int, nargs="+", default=[1, 5, 10, 20])
    args = parser.parse_args()
    if args.command == "build-baseline":
        build_baseline(args.gold, args.source_dir, args.out, args.k)
    else:
        compare(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
