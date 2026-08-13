# Live RAGAS Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce one clean, live, read-only MediPay evaluation run using source-derived golden cases, official RAGAS metrics, deterministic hard gates, and truthful failure reports.

**Architecture:** Replace the permissive evaluator behavior inside `eval/golden_eval.py` with focused dataset, runtime, RAGAS, scoring, and reporting functions while preserving the existing production application. The harness reads real corpus files, invokes the existing agent read-only, evaluates each answer with deterministic checks plus RAGAS, and writes one auditable run directory.

**Tech Stack:** Python 3.12, pytest, CSV/JSONL, existing LangGraph/OpenAI/PostgreSQL/Neo4j runtime, official `ragas` package installed only in `.venv`.

## Global Constraints

- Do not modify `src/`, `database/`, `web/`, production prompts, API contracts, database schemas, CI, or `requirements.txt`.
- Never expose `.env` values in logs or artifacts.
- Agent calls are read-only and may use only existing retrieval/generation paths.
- Source-backed case threshold is `0.60`; missing metrics never become PASS.
- P0/P1 safety/privacy failures block the run regardless of aggregate score.
- Delete old result folders only after the new run passes artifact-integrity checks.

---

### Task 1: Lock source-derived golden dataset behavior

**Files:**
- Modify: `eval/test_golden_eval.py`
- Modify: `eval/golden_eval.py`

**Interfaces:**
- `build_golden_dataset(source_dir: Path, output_path: Path, source_case_count: int = 30) -> dict[str, Any]`
- `validate_golden_dataset(dataset_path: Path, source_dir: Path) -> dict[str, Any]`
- Produces JSONL fields `case_id`, `case_origin`, `category`, `risk`, `agent_input`, `reference`, `required_facts`, `reference_contexts`, `reference_context_ids`, `evidence_refs`, and `source_hashes`.

- [ ] **Step 1: Write failing tests** proving cases use public document labels, match real `content.csv` rows, include source-derived references and contexts, balance BHYT/viện-phí rows, and never leak gold into `agent_input`.
- [ ] **Step 2: Run focused tests** with `--basetemp .pytest-tmp` and confirm they fail because the new interfaces/fields are absent.
- [ ] **Step 3: Implement deterministic source joins and case generation** with stable sorting and three templates: title, effective-date/status, and domain.
- [ ] **Step 4: Implement six explicitly labeled synthetic policy cases** for safety/privacy/overpromise boundaries.
- [ ] **Step 5: Run focused tests** and confirm the dataset contract passes.

### Task 2: Reject false PASS results with deterministic gates

**Files:**
- Modify: `eval/test_golden_eval_errors.py`
- Modify: `eval/test_golden_eval_runtime.py`
- Modify: `eval/golden_eval.py`

**Interfaces:**
- `score_deterministic(case: dict[str, Any], actual: dict[str, Any]) -> dict[str, Any]`
- `compute_required_fact_recall(required_facts: list[dict[str, Any]], answer: str) -> tuple[float, list[str]]`
- `is_generic_fallback(answer: str) -> bool`

- [ ] **Step 1: Write a failing regression test** reproducing the old bug: completed fallback with no forbidden phrase must be `FAIL`, not `PASS`.
- [ ] **Step 2: Write failing tests** for missing title/date/status facts, wrong document retrieval, forbidden claims, runtime errors, and missing traces.
- [ ] **Step 3: Run focused tests** and verify failures are caused by the permissive completed-answer branch.
- [ ] **Step 4: Implement fact normalization, date variants, fallback detection, ID precision/recall, and hard-failure taxonomy.**
- [ ] **Step 5: Run focused tests** and confirm deterministic failures are truthful.

### Task 3: Capture complete read-only live traces

**Files:**
- Modify: `eval/test_golden_eval_read_only.py`
- Modify: `eval/golden_eval.py`

**Interfaces:**
- `generate_actual_answers(dataset_path: Path, output_path: Path, run_id: str) -> dict[str, Any]`
- Each record contains full redacted retrieved context text, ordered IDs/titles/scores/channels, citations, status, trace ID, latency, usage fields, and error.

- [ ] **Step 1: Write failing tests** proving only `agent_input` reaches the agent and returned evidence text/order is persisted without gold.
- [ ] **Step 2: Run focused tests** and confirm current evidence summaries omit context text.
- [ ] **Step 3: Implement redacted full-context capture** from `retrieved_evidence`; preserve rank and cap stored text to the source chunk length used by the agent.
- [ ] **Step 4: Run focused tests** and confirm trace records satisfy the contract.

### Task 4: Integrate official RAGAS metrics

**Files:**
- Create: `eval/test_ragas_metrics.py`
- Modify: `eval/golden_eval.py`

**Interfaces:**
- `run_ragas_metrics(cases: list[dict[str, Any]], actuals: list[dict[str, Any]], evaluator_model: str) -> list[dict[str, Any]]`
- Metrics: `factual_correctness`, `response_relevancy`, `faithfulness`, `context_precision`, and `context_recall`.

- [ ] **Step 1: Install official RAGAS into `.venv`** without editing production dependency files; set `RAGAS_DO_NOT_TRACK=true`.
- [ ] **Step 2: Inspect the installed API and record exact package version.**
- [ ] **Step 3: Write failing tests** with scorer injection proving per-metric values and metric errors are preserved; errors must be `NOT_OBSERVABLE`.
- [ ] **Step 4: Run focused tests** and confirm the wrapper is absent.
- [ ] **Step 5: Implement the smallest version-specific RAGAS adapter** using the configured OpenAI evaluator model and official metric classes.
- [ ] **Step 6: Run focused tests** and confirm metric mapping/error behavior passes.

### Task 5: Compute case gates and explanatory reports

**Files:**
- Modify: `eval/test_golden_eval_reports.py`
- Modify: `eval/golden_eval.py`
- Modify: `eval/README.md`

**Interfaces:**
- `merge_case_score(case, actual, deterministic, ragas_score, threshold=0.60) -> dict[str, Any]`
- `write_report(output_dir: Path, summary: dict[str, Any], scores: list[dict[str, Any]]) -> None`

- [ ] **Step 1: Write failing tests** for weighted score, `0.60` threshold, core-metric floor, hard gates, grouped failures, and missing-metric handling.
- [ ] **Step 2: Run focused tests** and confirm the old report cannot express these distinctions.
- [ ] **Step 3: Implement weighted scoring and category/metric aggregates.**
- [ ] **Step 4: Implement `report.md` and `failures.md`** with actual/reference answers, failed thresholds, missing facts, target rank, root category, and next action.
- [ ] **Step 5: Update the README** with one canonical command and artifact-reading order.
- [ ] **Step 6: Run focused tests** and confirm report behavior passes.

### Task 6: Run the real evaluation

**Files:**
- Runtime output only: `eval/results/run-<timestamp>/...`

**Interfaces:**
- CLI command `python eval/golden_eval.py live --source-dir data/raw --out eval/results/run-<timestamp> --source-count 30 --threshold 0.60`

- [ ] **Step 1: Validate `.env` presence without printing values** and record only boolean configuration readiness.
- [ ] **Step 2: Generate and validate the golden dataset.**
- [ ] **Step 3: Invoke every case through the existing live agent** and retain failures/timeouts in the denominator.
- [ ] **Step 4: Run official RAGAS metrics** for source-backed completed cases.
- [ ] **Step 5: Write all nine required artifacts** and exit nonzero when quality gates fail.
- [ ] **Step 6: Recompute counts independently** from JSONL files and verify summary consistency.

### Task 7: Clean results and verify the final handoff

**Files:**
- Delete only after verification: old directories under `eval/results/`
- Keep: the newly verified `eval/results/run-<timestamp>/`

**Interfaces:**
- Final directory contains exactly one `run-*` folder and no archived legacy run folder.

- [ ] **Step 1: Run the full eval test suite** using a workspace basetemp.
- [ ] **Step 2: Run existing backend tests and Ruff** to prove production behavior was not modified.
- [ ] **Step 3: Verify the new run contains all artifacts, valid JSON/JSONL, complete denominators, RAGAS version/model metadata, and no obvious secret patterns.**
- [ ] **Step 4: Resolve every old results directory to an absolute child of `eval/results` and delete only those exact old directories.**
- [ ] **Step 5: Re-list `eval/results`, inspect Git diff, and report the actual PASS/BLOCKED status and lowest metrics without softening failures.**
