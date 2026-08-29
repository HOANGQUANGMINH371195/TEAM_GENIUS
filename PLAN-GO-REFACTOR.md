# Kế hoạch đánh giá và refactor MediPay sang Go

> Tài liệu này là kế hoạch kỹ thuật, chưa phải quyết định rewrite. Mục tiêu là
> giảm latency của lớp điều phối mà không đánh đổi độ chính xác pháp lý, khả
> năng truy nguyên hoặc chi phí vận hành.

## 1. Kết luận điều hành

Không chuyển toàn bộ ngay lập tức. Baseline hiện tại cho thấy thời gian chủ yếu
đến từ OpenAI, Supabase, Qdrant và Neo4j qua mạng; đổi Python sang Go không làm
nhanh hơn các dịch vụ bên ngoài. Go có lợi thế ở HTTP concurrency, memory
footprint, cancellation và binary deploy, nhưng có rủi ro lớn khi viết lại
retrieval/reranking/schema guard.

Lộ trình được chọn:

1. Giữ pipeline ingest, benchmark, reviewer và các bộ parser Python làm nguồn
   chuẩn.
2. Viết Go benchmark harness và một Go gateway chạy shadow, không trả traffic.
3. Chuyển dần lớp API/orchestrator và các adapter I/O sang Go khi benchmark
   chứng minh lợi ích.
4. Chỉ chuyển ranking hoặc calculator sau khi kết quả trên release locked
   dataset không kém Python.
5. Xóa Python runtime khỏi production chỉ khi canary và rollback gate đều pass.

## 2. Baseline bắt buộc trước khi viết lại

Lưu cùng một release, prompt, model và concurrency:

- accuracy trên golden 100 câu và release-locked 292 câu;
- citation precision/recall, unsupported-claim rate, raw-chunk leakage rate;
- TTFT, retrieval, rerank, generation và end-to-end p50/p95/p99;
- cold start, warm cache, 1/5/20/50 concurrent users;
- số request lỗi theo dependency, retry count và cache hit;
- input/output tokens và chi phí thật.

Không chấp nhận kết luận “Go nhanh hơn” nếu chỉ đo một handler local. Ngưỡng
promotion đề xuất: accuracy/citation không giảm quá 0,5 điểm phần trăm, không
tăng unsupported claim, p95 giảm ít nhất 20% hoặc memory giảm ít nhất 30%, và
chi phí request không tăng.

## 3. Kiến trúc Go đích

```text
Vercel web
    -> Nginx/TLS
    -> Go API (SSE, auth, rate-limit, idempotency, context cache)
         -> planner/router
         -> parallel ports: Postgres lexical | Qdrant dense | Neo4j graph
         -> deterministic merge + reranker
         -> OpenAI Responses API (structured output)
         -> citation/output guard -> SSE final
    -> Valkey/Redis (cache, quota, idempotency)
    -> OTel -> Prometheus/Loki/Langfuse collector
Python pipeline/evaluator (offline, không nằm trên request path)
```

### Package layout

```text
go/
  go.mod
  cmd/medipay-api/main.go
  internal/
    domain/          # Answer, Citation, RetrievalHit, Release contracts
    application/     # use cases and orchestration
    ports/            # interfaces for stores/providers
    adapters/
      postgres/      # pgxpool + sqlc generated queries
      qdrant/        # official client or REST/gRPC adapter
      neo4j/         # official Bolt driver
      openai/        # Responses + SSE + retries
      redis/         # cache/rate-limit/idempotency
      firebase/      # token verification
    transport/http/  # net/http, SSE, middleware, OpenAPI
    observability/   # OTel, metrics, redaction
  migrations/        # references existing ordered SQL; no create-all
```

`domain` không được import SDK. Mọi adapter phải nhận `context.Context`, có
deadline riêng và trả typed errors (`Unavailable`, `Timeout`, `InvalidData`,
`RateLimited`).

## 4. Công nghệ và nguyên tắc triển khai

### HTTP và concurrency

- Dùng `net/http` trước; chỉ thêm router nếu profiling cho thấy cần.
- Mỗi request có context deadline; SSE hủy toàn bộ downstream khi browser đóng.
- Dùng `errgroup`/worker pool với giới hạn rõ ràng, không tạo goroutine vô hạn.
- Có bounded channel/backpressure cho token stream và queue.
- Bật `go test -race` ở CI riêng; race detector có overhead lớn nên không chạy
  trong benchmark latency production.
- Bật `pprof` nội bộ, không public; lấy CPU/heap/block/mutex profile trước và
  sau migration.

### Database và retrieval

- PostgreSQL: `pgxpool`, `SetMaxConns/MinConns`, query timeout và `sqlc`; giữ
  schema/migration hiện tại làm authority.
- Qdrant: bắt đầu bằng REST để dễ parity/debug; chuyển gRPC chỉ khi đo được
  lợi ích. Dùng official Go client và immutable release collection.
- Neo4j: official Go driver, một driver singleton, pool nhỏ, Bolt TLS, query
  bounded, circuit breaker và fallback lexical+dense. Graph là navigation
  signal, không phải nguồn citation trực tiếp.
- Reranker cross-encoder không nhúng vào Go API ở phase đầu; chạy model server
  riêng hoặc giữ Python worker để tránh thay đổi điểm ranking.
- Table calculator chỉ nhận typed rows đã hydrate từ PostgreSQL; cấm LLM tự
  suy ra ô thiếu.

### LLM và streaming

- Dùng official `openai-go` Responses API với JSON Schema/Pydantic-equivalent
  Go structs và strict validation.
- Retry chỉ cho lỗi transport/408/429/5xx, có exponential backoff và budget
  tổng; không retry lỗi schema hoặc policy.
- SSE chỉ phát event schema (`meta`, `delta`, `citation`, `final`, `error`),
  không phát raw provider chunk.
- Cache answer phải khóa theo user, release, prompt version, model và normalized
  query; không dùng cache của user khác.

### Observability và security

- OTel traces/metrics stable, logs redacted; span attributes không chứa nội
  dung pháp luật đầy đủ, secret, raw prompt hoặc document ID nội bộ.
- Prometheus histogram cho TTFT/stage latency; Langfuse lưu prompt/model
  lineage; health/readiness tách biệt.
- Firebase Admin JSON chỉ ở AWS secret store. Go binary không chứa secret lúc
  build.

## 5. Lộ trình có rollback

### Phase 0 — CI và baseline (1–2 ngày)

- Sửa `aquasecurity/trivy-action` sang tag đã phát hành `0.33.1`.
- Chạy lại GitHub Actions; nếu fail ở `Set up job`, xử lý quota/runner của
  GitHub trước, không đổi ngôn ngữ.
- Đóng băng release/prompt/model và lưu benchmark JSON + cost ledger.

### Phase 1 — Go compatibility harness (3–5 ngày)

- Tạo `go/` với health/readiness, config validation và typed release contract.
- Viết client probes cho Postgres/Qdrant/Neo4j/OpenAI, không có write.
- Chạy cùng 100/292 câu với Python, so sánh retrieval IDs, citations, latency.
- Gate: không được đưa Go vào traffic nếu parity retrieval chưa đạt 100% trên
  fixture deterministic.

### Phase 2 — Go gateway shadow (5–10 ngày)

- Go nhận một bản copy request, gọi cùng release nhưng không trả kết quả cho
  user; hash/record kết quả đã redacted.
- Đo overhead HTTP, auth, cache, idempotency, SSE và connection pools.
- So sánh answer schema và unsupported claims với Python.

### Phase 3 — I/O adapters và fast path (1–2 tuần)

- Chuyển middleware, auth, rate limit, idempotency, context cache.
- Chuyển parallel Postgres/Qdrant/Neo4j reads; giữ Python reranker/generator
  qua internal HTTP nếu cần.
- Canary 1–5% traffic, rollback bằng image tag cũ trong vài phút.

### Phase 4 — Planner/reranker có kiểm soát (2–4 tuần)

- Port planner state machine và deterministic merge trước.
- Chỉ port heuristic reranker nếu top-k parity đạt; cross-encoder giữ service
  riêng cho đến khi có benchmark model runtime tương đương.
- Port calculator/table renderer với property tests và golden examples.

### Phase 5 — Production promotion

- Go làm API chính; Python chỉ còn pipeline/eval/repair worker.
- Chạy cold/warm/concurrency benchmark thật, failure injection Neo4j/Qdrant,
  restore/rollback và security scan.
- Giữ Python image và release pointer cũ trong observation window; có lệnh
  chuyển traffic ngược ngay.

## 6. CI/CD và quality gates cho Go

- `go test ./...`, `go test -race ./...`, `go vet ./...`, `staticcheck`;
- `govulncheck`, SBOM, Trivy image, gitleaks;
- contract tests chống raw chunk, internal ID leakage và citation mismatch;
- replay benchmark bắt buộc trên cùng release/prompt/model;
- build multi-arch ARM64 cho EC2 và immutable digest;
- deploy AWS chỉ sau CI xanh; Vercel vẫn deploy frontend độc lập.

## 7. Chi phí và quyết định cuối

Go có thể giảm RAM/CPU và cold-start, nhưng không làm giảm token cost hoặc
latency của OpenAI/managed databases. Vì vậy không tăng số service, Kubernetes
hay message broker trong phase đầu. Một EC2 Compose + Valkey + managed stores
hiện tại là đủ cho canary.

Quyết định rewrite toàn bộ chỉ được thông qua nếu Phase 2–3 chứng minh đồng thời:

1. p95 end-to-end giảm ≥20% trong cùng workload;
2. accuracy, citation và safety không giảm;
3. chi phí compute không tăng sau khi tính observability;
4. rollback và parity tự động, không phụ thuộc thao tác thủ công.

Nếu không đạt, giữ Python và tối ưu đúng bottleneck bằng connection pooling,
cache, parallel I/O, giảm round-trip và prompt/context budget.

## 8. Nguồn kỹ thuật đã đối chiếu

- Go `net/http`, `context`, `runtime/pprof` và database connection management:
  https://pkg.go.dev/net/http · https://pkg.go.dev/context ·
  https://pkg.go.dev/runtime/pprof · https://go.dev/doc/database/manage-connections
- Go race detector: https://go.dev/doc/articles/race_detector
- Official OpenAI Go SDK/Responses API: https://github.com/openai/openai-go
- Official Neo4j Go driver/manual: https://neo4j.com/docs/go-manual/current/install/
- Qdrant official clients, REST/gRPC trade-off và Go client:
  https://qdrant.tech/documentation/interfaces/
- OpenTelemetry Go stability/instrumentation:
  https://opentelemetry.io/docs/languages/go/
