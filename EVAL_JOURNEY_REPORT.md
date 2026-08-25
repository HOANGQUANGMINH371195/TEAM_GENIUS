# P-151 — đánh giá độ tin cậy và quá trình phát triển

Ngày kiểm tra: 2026-08-25 UTC. Phạm vi: repository `P-151`, các bộ eval đang
có trong checkout và các kết quả được lưu cùng chúng. Báo cáo này cố ý tách
retrieval, answer surface và legal correctness; không coi một gate kỹ thuật là
bằng chứng đáp án pháp lý đúng.

## Kết luận điều hành

Không có cơ sở để đưa claim “độ chính xác 100%” lên slide. Trong các artifact
đang tồn tại và đọc được:

| Bài chạy | Denominator | Pass | Kết luận | Bằng chứng chính |
|---|---:|---:|---|---|
| `canonical-live-ragas` | 36 | 0 | FAIL | 33 fallback; quality mean 0.097; factual correctness 0.055 |
| `run-20260816-full-36` | 36 | 0 | FAIL | 34 fallback; quality mean 0.048; context recall 0.033 |
| `run-20260816-smoke-3` | 3 | 0 | FAIL | 3/3 fallback |
| lexical baseline (retrieval-only, cùng golden set) | 30 source cases | — | retrieval reference | hit@1 100%, hit@20 100% |

Bài lexical baseline không sinh câu trả lời, vì vậy không được gán factuality,
faithfulness hay legal accuracy. Nó chỉ cho thấy một hệ thống token-overlap
đơn giản đã tìm đúng document ID ở 30/30 source cases, trong khi project run
hiện tại đạt hit@1 0%, hit@5 3.3%, hit@10/20 10% trên cùng artifact. Đây là
tín hiệu mạnh của lỗi projection/release mapping hoặc retrieval route, không
phải bằng chứng baseline trả lời tốt hơn toàn hệ thống.

## Vì sao claim 100% trước đây không hợp lệ

1. `PLAN.md` trích dẫn các artifact như
   `eval/results/run-20260822-completion-audit-v3/` và các file parity/load
   tương ứng, nhưng các path này không tồn tại trong checkout hiện tại. Chúng
   không thể được dùng làm evidence reproducible.
2. Các con số 100% trong PLAN là gate hẹp: exact identifier, graph evidence,
   policy behavior, parity hoặc targeted regression. Chúng không phải
   end-to-end legal answer accuracy trên 200–300 câu held-out.
3. Chính PLAN đã ghi human-adjudicated quality/RAGAS trên toàn denominator là
   gate riêng còn mở. Không được gộp `Recall@20`, parity hay test pass thành
   “đáp án đúng 100%”.
4. Golden source cases được tạo theo metadata/content snapshot và có trạng thái
   `source_derived`; các case/policy rubric không thay thế adjudication của
   chuyên gia pháp lý. Required-fact matching chỉ là review queue.
5. Citation presence không đồng nghĩa citation correctness: historical report
   ghi 30/30 source cases có citation nhưng chỉ 3/30 (baseline) và 1/30
   (current) chứa target source document.

## Số liệu đã kiểm tra

### Live RAGAS artifacts hiện có

`canonical-live-ragas/summary.json` ghi 36/36 fail, 33 fallback, response
relevancy 0.053594, factual correctness 0.055333, faithfulness 0.072222,
context precision 0.180020, context recall 0.133333 và quality 0.096942.

`run-20260816-full-36/summary.json` ghi 36/36 fail, 34 fallback, response
relevancy 0.026284, factual correctness 0.019000, faithfulness 0.066667,
context precision 0.108440, context recall 0.033333 và quality 0.048175.

Latency của run full historical là mean 3,504.9 ms, p50 3,034.0 ms và max
20,827.4 ms. Đây là latency harness end-to-end cũ, không phải latency của
lexical baseline; không được dùng để tuyên bố baseline nhanh hơn.

### Independent ordinary-RAG baseline

`eval/compare_rag_baseline.py` xây một baseline token-overlap trên cùng
`data/raw/metadata_{bhyt,vien_phi}.csv` và `content.csv`. Nó không dùng graph,
reranker hay LLM. Lệnh đã chạy:

```bash
python3 eval/compare_rag_baseline.py build-baseline \
  --gold eval/results/canonical-live-ragas/golden_dataset.jsonl \
  --source-dir data/raw --out /tmp/p151-lexical-baseline.jsonl --k 20
python3 eval/compare_rag_baseline.py compare \
  --gold eval/results/canonical-live-ragas/golden_dataset.jsonl \
  --current eval/results/canonical-live-ragas/actual_answers.jsonl \
  --baseline /tmp/p151-lexical-baseline.jsonl --out /tmp/p151-rag-comparison.json
```

Kết quả retrieval trên 30 source cases:

| Metric | Project | Lexical baseline | Delta |
|---|---:|---:|---:|
| hit@1 | 0.0% | 100.0% | -100.0 pp |
| hit@5 | 3.3% | 100.0% | -96.7 pp |
| hit@10 | 10.0% | 100.0% | -90.0 pp |
| hit@20 | 10.0% | 100.0% | -90.0 pp |

Project answer surface fact coverage chỉ 2.78% với 33/36 fallback. Baseline
không có answer nên coverage là `N/A`, không phải 0%.

## Lịch sử và quãng đường

- **Khởi tạo release:** P-151 được tạo và đóng gói thành repository riêng.
- **Nền tảng deploy:** Docker, Render/Vercel contract, auth, SSE và health
  checks được bổ sung; đây là operational readiness, không phải legal quality.
- **Dữ liệu:** PostgreSQL/Qdrant/Neo4j, release pointer, parity và corpus
  intake được chuẩn hóa; các projection gate giúp phát hiện drift nhưng không
  chứng minh nội dung trả lời đúng.
- **Retrieval:** đã thử exact route, hybrid lexical/semantic, focus/scope,
  reranking, legal-unit expansion và graph navigation. Các commit gần nhất
  tập trung BHYT và regression suite.
- **Guardrail:** đã thêm chống lộ internal ID, fallback, policy/safety và
  claim verification. Đây là điều kiện an toàn; không biến thành factuality.
- **Evaluation:** từ smoke 3 case → full 36 case → critical BHYT và release
  locked 292-case structural suite. RAGAS hiện có cho thấy quality gate fail;
  292-case locked suite chủ yếu khóa hash/taxonomy/coverage, chưa phải 292
  human-adjudicated end-to-end answers.
- **Bài học:** retrieval phải được đánh giá độc lập trước generation; mọi claim
  cần denominator, held-out split, seed/repeat, source hash, model/provider,
  latency p50/p95 và reviewer status.

## Cần làm trước khi có thể trình bày kết quả

1. Sửa release/projection mapping để project đạt ít nhất cùng hit@k với lexical
   baseline trên public-identifier cases; lưu document-number/span recall.
2. Tạo holdout 200–300 câu do chuyên gia duyệt, tách train/tuning khỏi test;
   khóa dataset hash và không để câu hỏi sinh trực tiếp từ cùng metadata dùng
   để tối ưu retrieval.
3. Chạy tối thiểu ba lần độc lập cùng release, ghi variance, p50/p95/p99 và
   provider/model. Báo cáo mean không đủ.
4. Chấm answer bằng rubric human: đúng căn cứ, đúng thời điểm, đủ điều kiện,
   không overclaim, citation đúng span; RAGAS chỉ là tín hiệu phụ.
5. Chỉ đưa lên slide các số có denominator và nhãn rõ: `retrieval hit@k`,
   `policy safety pass`, `parity`, `human legal accuracy`. Không dùng “100%”
   chung chung.

