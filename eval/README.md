# Live RAGAS evaluation

## Production latency/TTFT collection

`collect_production_evidence.py` measures first-request (cold), warm, and
bounded-concurrency SSE runs. It stores only response hashes and public
citation numbers and marks the output `HUMAN_REVIEW_REQUIRED`; it never judges
legal correctness or fills an attestation with inferred values.

```bash
BENCHMARK_AUTH_TOKEN='<short-lived Firebase ID token>' \
PYTHONPATH=. uv run python eval/collect_production_evidence.py \
  --endpoint https://<api-origin> \
  --fixture eval/cases/critical-bhyt-7.jsonl \
  --output eval/results/production-evidence-<timestamp>.json
```

Evaluation chuẩn nằm duy nhất tại `eval/results/canonical-live-ragas/`.

## Đọc kết quả

1. `report.md`: kết luận, điểm trung bình và phần dự án đang yếu.
2. `failures.md`: từng failure thật, điểm thấp, lý do, actual answer và nơi nên kiểm tra.
3. `summary.json`: thống kê máy đọc được theo metric, nguồn và loại câu hỏi.

## Human claim calibration

Confidence calibration is a separate release artifact. Prepare JSONL rows with
`claim_id`, `confidence`, `outcome` (`0`/`1`) and an explicit `reviewer` from
at least two independent reviewers. The fitting command rejects incomplete or
duplicate labels and writes an isotonic (monotone) calibrator plus ECE/Brier
metrics:

```bash
make calibrate-claims \
  LABELS_FILE=/absolute/path/reviewed-claims.jsonl \
  OUTPUT=eval/results/calibration-<release>.json
```

The artifact is not a legal-quality pass by itself; it becomes eligible for a
promotion decision only after the panel and its minimum case count are
approved.
4. `case_scores.jsonl`: release gate đầy đủ cho toàn bộ denominator.
5. `ragas_scores.jsonl`: năm metric official RAGAS cho từng source case.
6. `actual_answers.jsonl`: output và retrieved context thật của agent.
7. `golden_dataset.jsonl`: câu hỏi/reference/fact trích từ CSV nguồn thật cùng provenance.
8. `dataset_validation.json`: hash nguồn và kết quả kiểm tra golden dataset.
9. `run_manifest.json`: model, phiên bản RAGAS, threshold và hash artifact.

## Quy tắc PASS/FAIL

- Ngưỡng từng metric cốt lõi và quality score là `0.60`.
- Source cases dùng factual correctness, completeness, response relevancy, faithfulness,
  context precision, context recall và ID context recall.
- Fallback chung chung, retrieval miss, forbidden claim hoặc policy behavior thiếu là FAIL cứng.
- Metric lỗi, thiếu hoặc NaN là `NOT_OBSERVABLE`, không bao giờ được tính PASS.
- Sáu policy cases được chấm bằng gate hành vi deterministic vì không cần retrieval context.

## Nguồn golden dataset

Ba file được join theo `id`:

- `data/raw/metadata_bhyt.csv`
- `data/raw/metadata_vien_phi.csv`
- `data/raw/content.csv`

Mỗi câu user-facing dùng số hiệu/tên văn bản thật; internal document ID chỉ nằm trong
provenance và reference IDs. Validator kiểm tra source hash, content reference, gold
completeness, trùng case ID, secret pattern và gold leakage.

## Chạy lại

### Bảy câu acceptance BHYT trên staging

Không dùng alias `medical_legal_active` cho suite này. Runner bắt buộc nhận cả
dataset snapshot và collection Qdrant vật lý, thực hiện luồng agent read-only,
ghi citation công khai/đầu ra/thời gian, rồi luôn để kết luận pháp lý ở trạng
thái `HUMAN_REVIEW_REQUIRED`.

```bash
cd P-151
PYTHONPATH=. uv run --offline --python 3.11 --with-requirements requirements/dev.lock \
  python eval/critical_bhyt_eval.py \
  --dataset-id snapshot-8dee10dd6798b9ac \
  --qdrant-collection medical_legal_hybrid_snapshot-8dee10dd6798b9ac \
  --out /tmp/critical-bhyt-staging.json
```

Exit code khác `0` nghĩa là một gate cơ học đã thất bại; exit code `0` vẫn
không thay thế việc reviewer kiểm căn cứ pháp lý, phạm vi áp dụng và p95 từ
nhiều lần chạy độc lập.

Trước RAGAS, luôn chạy deterministic retrieval gates của active release:

```bash
PYTHONPATH=database/pipeline:. .venv/bin/python database/corpus/evaluate_qdrant_semantic.py \
  --benchmark data/clean/medical_active_v31_fully_reviewed/semantic_question_benchmark.jsonl \
  --dataset-id snapshot-c439751724ab7f10 \
  --output /tmp/qdrant-semantic-eval.json
```

Suite này đo thematic semantic Recall@20 và ANN-vs-exact Qdrant; benchmark
document-number dùng exact route, không phải semantic gate.

RAGAS được tách khỏi `.venv` production để không làm thay đổi dependency dự án:

```powershell
python -m venv .eval-ragas-venv
.\.eval-ragas-venv\Scripts\python.exe -m pip install "ragas==0.3.9" "langchain-openai<1"
.\.venv\Scripts\python.exe eval\golden_eval.py live `
  --source-dir data\raw `
  --out eval\results\canonical-live-ragas `
  --count 30 `
  --ragas-python .eval-ragas-venv\Scripts\python.exe `
  --evaluator-model gpt-4o-mini `
  --embedding-model text-embedding-3-small `
  --concurrency 3 `
  --threshold 0.60
```

Lệnh `live` gọi agent/DB/Neo4j ở chế độ read-only. Không sửa production chỉ để làm
đẹp điểm eval; sửa retrieval/prompt/guardrail rồi chạy lại cùng nguồn và threshold.

### Human calibration artifact

Calibration chỉ nhận JSONL có đủ `claim_id`, `confidence`, `outcome` (0/1) và
`reviewer`. Dùng loader để từ chối dòng thiếu nhãn; không dùng output do model
tự chấm làm gold:

```bash
PYTHONPATH=. uv run python - <<'PY'
from pathlib import Path
from eval.calibration import calibration_report, load_calibration_records
rows = load_calibration_records(Path("/path/to/reviewed-calibration.jsonl"))
print(calibration_report(rows))
PY
```

Trước khi fit ngưỡng abstention/uncertainty, gọi thêm
`validate_calibration_panel(rows, min_cases=30, min_reviewers=2)`. Mỗi claim
phải có nhãn độc lập từ ít nhất hai reviewer; duplicate hoặc claim thiếu một
reviewer bị từ chối. Raw agreement chỉ là kiểm tra quy trình, không thay thế
legal adjudication.

### Human answer-review artifact

Production attestation must reference a redacted, answer-hash-bound JSONL
artifact; entering accuracy numbers without that artifact is rejected. Build a
review packet from the immutable fixture and the live answer export:

```bash
PYTHONPATH=. uv run python eval/build_review_packet.py \
  --fixture eval/cases/market-leadership-v1.jsonl \
  --answers /absolute/path/answers.jsonl \
  --release-id snapshot-<immutable-release> \
  --output /absolute/path/human-review-packet.jsonl
```

Two independent reviewers must fill one label row per case in a separate
`human-legal-review-v1` artifact. `eval.human_review.validate_review_panel`
rejects duplicate, incomplete or disagreeing labels and requires at least 300
cases. The production attestation stores the artifact path and SHA-256; the
verifier recomputes both before accepting human metrics. Model-generated
judgments are never treated as legal review.

The three ablation entries in the production attestation follow the same
rule: each must reference its actual `reranker-ablation-v1`,
`typed-graph-ablation-v1`, or `grounded-planning-ablation-v1` JSON result and
the verifier recomputes its SHA-256 before accepting `reviewed` or
`no_regression`.

Outage and rollback booleans are also bound to an `operations-evidence-v1`
JSON artifact containing the release ID and each drill result. The attestation
verifier rejects missing or hash-mismatched operations evidence.

The cost-reduction claim follows the same rule through a `cost-ledger-v1`
artifact. It must contain provider receipts and baseline/candidate costs; the
verifier recomputes the reduction and compares it with the attestation.
