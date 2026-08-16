# Insurance & Hospital Billing Golden Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a non-invasive harness that generates and evaluates a deterministic draft golden dataset from the repository legal corpus.

**Architecture:** A standard-library-only evaluator lives under the existing `eval/` convention. It generates source-backed synthetic cases, validates gold metadata, records `not_observable` actual answers when no isolated runtime is configured, and writes unique run artifacts outside the repository. It never changes the production agent.

**Tech Stack:** Python 3.12 standard library, existing pytest for harness tests, CSV/JSONL, PowerShell invocation.

## Global Constraints

- No production source, API contract, prompt, schema, dependency, CI, or database changes.
- No live side effects and no real patient/payment/secret data.
- Auto-generated gold is `draft_gold`; no release PASS without expert review.
- Artifacts are written under `C:\tmp\agent_eval\P-151\<run_id>`.
- The agent receives only synthetic user/runtime input, never evaluator-only gold fields.

---

### Task 1: Define source-backed case generation contracts

**Files:**
- Create: `eval/test_golden_eval.py`
- Create: `eval/golden_eval.py`

**Interfaces:**
- `build_dataset(source_dir: Path, output_path: Path, count: int = 30) -> dict`
- `validate_dataset(dataset_path: Path) -> dict`
- `make_case(case_id: str, question: str, category: str, risk: str, gold: dict) -> dict`

- [ ] **Step 1: Write failing tests** for deterministic case shape, source references, unique IDs, and rejection of gold leakage into agent input.
- [ ] **Step 2: Run the focused tests** and confirm failure because `eval.golden_eval` does not exist.
- [ ] **Step 3: Implement the smallest CSV reader and deterministic case builder** using only `metadata_bhyt.csv` and `metadata_vien_phi.csv`.
- [ ] **Step 4: Run the focused tests** and confirm they pass.

### Task 2: Add dataset validation and hashing

**Files:**
- Modify: `eval/golden_eval.py`
- Modify: `eval/test_golden_eval.py`

**Interfaces:**
- `validate_dataset(dataset_path: Path) -> dict` returns `valid`, `count`, `errors`, `gold_completeness`, and `sha256`.
- `source_manifest(source_dir: Path) -> dict` returns file hashes and record counts without exposing row contents.

- [ ] **Step 1: Add failing tests** for duplicate IDs, empty inputs, missing source references, secret patterns, and dataset hash stability.
- [ ] **Step 2: Run focused tests** and confirm the expected validation failures.
- [ ] **Step 3: Implement validation and redaction-safe manifest generation.**
- [ ] **Step 4: Run focused tests** and confirm they pass.

### Task 3: Add safe actual-answer adapter and deterministic scoring

**Files:**
- Modify: `eval/golden_eval.py`
- Modify: `eval/test_golden_eval.py`

**Interfaces:**
- `generate_actual_answers(dataset_path: Path, output_path: Path, run_id: str) -> dict`
- `evaluate_answers(dataset_path: Path, actual_path: Path, output_dir: Path) -> dict`

- [ ] **Step 1: Add failing tests** proving absent isolated runtime yields one `not_observable` record per case and never calls network code.
- [ ] **Step 2: Run focused tests** and confirm failure.
- [ ] **Step 3: Implement the safe adapter guard and evaluator.** Deterministic checks cover required source-backed facts when an answer exists, forbidden-claim markers for safety/privacy cases, and hard-gate handling for `not_observable`.
- [ ] **Step 4: Run focused tests** and confirm they pass.

### Task 4: Add CLI, artifacts, and human-readable diagnostics

**Files:**
- Modify: `eval/golden_eval.py`
- Modify: `eval/test_golden_eval.py`

**Interfaces:**
- CLI commands: `generate`, `validate`, `run`, and `report`.
- `write_report(output_dir: Path, summary: dict, scores: list[dict]) -> None`

- [ ] **Step 1: Add failing tests** for required artifact names, report sections, production inspection map, and nonzero quality/inconclusive exit status.
- [ ] **Step 2: Run focused tests** and confirm failure.
- [ ] **Step 3: Implement artifact writing and CLI progress output.**
- [ ] **Step 4: Run focused tests** and confirm they pass.

### Task 5: Run the harness against the repository dataset

**Files:**
- Create: `eval/README.md`

- [ ] **Step 1: Run the harness in offline mode** against `data/raw` and write a unique external run directory.
- [ ] **Step 2: Inspect `summary.json`, `failures.md`, and `report.md`** for denominator, blockers, and code-location guidance.
- [ ] **Step 3: Run existing backend/frontend verification commands** without modifying production.
- [ ] **Step 4: Report actual status as `INCONCLUSIVE` or `BLOCKED` if model/sandbox/trace is unavailable; never convert missing observability into pass.**
