# MediPay — PLAN triển khai AWS và LLMOps tinh gọn

> Bản chuẩn ngày 2026-08-29. Đây là kế hoạch duy nhất cho runtime production của
> P-151. Mục tiêu được xếp theo thứ tự: độ chính xác pháp lý, latency, độ ổn định,
> chi phí trên mỗi câu trả lời đúng và các feature đã chốt.

## 1. Mục tiêu sản phẩm

MediPay là một BHYT Decision & Evidence Engine có LLMOps kiểm soát được. Mỗi câu
trả lời phải:

- dùng passage pháp luật canonical, có hiệu lực theo ngày được hỏi;
- hợp nhất exact/lexical, semantic và quan hệ văn bản có giới hạn;
- trả kết luận, điều kiện, ngoại lệ, căn cứ và liên kết tới HTML gốc;
- tính tiền/tỷ lệ từ bảng bằng Decimal và công thức xác định;
- giữ context riêng theo Firebase UID nhưng luôn tìm lại evidence mới cho câu hỏi
  pháp lý;
- hỏi đúng fact còn thiếu, không trả chunk thô, ID nội bộ, score hay nhãn hệ thống;
- sinh qua JSON Schema strict/Pydantic trước khi render thành văn bản công khai;
- truy nguyên được prompt version, model version, release dữ liệu và latency từng
  stage mà không ghi secret hoặc nội dung riêng tư vào telemetry;
- kết thúc bằng SSE đúng schema, kể cả khi một dependency lỗi.

Không ingest hoặc extract thêm corpus trong release triển khai này. PostgreSQL,
Qdrant và Neo4j hiện có được giữ nguyên làm data plane; chỉ dọn cách truy cập và
đường runtime.

`AUDIT.md` là hồ sơ lịch sử của các lần kiểm tra trước, không phải release gate
hiện hành; mọi quyết định promote phải dùng manifest, benchmark và trạng thái
được ghi trong tài liệu này.

### Quyết định chốt cho release hiện tại

1. **Data plane giữ nguyên:** Supabase PostgreSQL là canonical store, Qdrant là
   semantic index, Neo4j là bounded relation index, Firebase là identity. Không
   tạo database thứ tư, không nhân bản snapshot và không ingest/extract thêm
   corpus trong release này.
2. **Serving plane:** một EC2 Graviton chạy Docker Compose; Nginx làm edge/TLS/SSE,
   Valkey/Redis OSS làm cache context/rate state, Prometheus + Grafana chạy cùng
   host. Đây là profile chi phí thấp đã chốt; không dùng dịch vụ AWS managed cho
   các lớp trên nếu chưa có số liệu chứng minh cần mở rộng.
3. **LLMOps:** Langfuse là prompt/trace control-plane; OpenTelemetry là contract
   trace fail-open. RAGAS và Promptfoo chỉ chạy offline/CI. Không đưa telemetry,
   judge hay batch vào critical chat path.
4. **Không mở rộng framework:** không thêm Kubernetes/Helm, APISIX, Jenkins,
   Celery/BullMQ/RabbitMQ, Jaeger/ELK/Loki, S3 hay model gateway riêng. Chỉ xem
   xét lại sau một load/cost report có chữ ký, không vì cảm tính.
5. **Quality over green:** `production_promotion_allowed=false` là trạng thái
   đúng cho tới khi các blocker ở mục 2.1 có bằng chứng live. Không đổi ngưỡng,
   tắt guardrail hoặc hardcode số hiệu văn bản để biến gate thành PASS.

### Bằng chứng bắt buộc cho mọi thay đổi

Mỗi thay đổi runtime/retrieval/model phải lưu cùng một `release_id`, commit,
prompt/model version, image digest, dataset/index hash và kết quả benchmark. Một
claim “đã xong” chỉ hợp lệ khi có artifact hoặc log có thể tái chạy; pytest và
synthetic benchmark không thay thế reviewer pháp lý, load test hay restore drill.

## 2. Hiện trạng đã xác nhận

### Trạng thái hardening hiện tại (2026-08-28)

- Đã khóa generation bằng `GroundedAnswer` (Pydantic/JSON Schema) và renderer
  deterministic; output không hợp lệ bị từ chối trước guardrail/public SSE.
- Đã thêm resolver Prompt Registry Langfuse có cache TTL, fallback hash cục bộ
  và ghi `prompt_version`, `model_version`, `release_id` vào generation trace.
- Đã bổ sung contract response công khai gồm `request_id`, `conversation_id` và
  `turn_id`, cùng cấu hình Promptfoo red-team ngoài request path.
- Đã bổ sung idempotency ledger PostgreSQL, replay/conflict handling cho chat và
  SSE, cùng `Idempotency-Key` client header; production thiếu key bị từ chối.
- Đã thêm Compose AWS single-host, Nginx SSE/TLS proxy, Prometheus/Grafana/Valkey
  và Ansible bootstrap có digest pinning, secret mode `0600` và migration hook.
- Promotion gate đã được sửa để đọc đúng bảng trạng thái tiếng Việt của PLAN;
  hiện báo `production_promotion_allowed=false` với 5 blocker thật thay vì
  false-positive từ parser header tiếng Anh cũ.
- CI đã có Gitleaks, Trivy HIGH/CRITICAL và SBOM sau bước build image.
- Migration rehearsal trên PostgreSQL disposable đã pass: lần đầu áp dụng 22
  migration, lần chạy thứ hai skip toàn bộ; không còn phụ thuộc image
  `corpus-worker` stale.
- Kiểm chứng local: `232 passed` backend, `112 passed` corpus/Neo4j/eval
  (tổng `344 passed`), Ruff sạch, frontend lint/typecheck và
  implementation/deploy/plan contract đều pass. Đây là kiểm chứng offline;
  chưa thay thế smoke/load test với
  PostgreSQL, Qdrant, Neo4j, Firebase và provider production.
- Production-like local Compose smoke ngày 2026-08-29 đã pass: PostgreSQL,
  Qdrant, Neo4j, Redis, migration, API và web đều healthy; `/health` và
  `/ready` trả HTTP 200 với cả ba dependency graph/data ở trạng thái ready.
  Healthcheck image Slim đã dùng đúng interpreter `/usr/local/bin/python3.11`.
  Readiness load local 20 request đồng thời cũng pass 20/20, P50 khoảng 309 ms,
  P95 khoảng 312 ms; đây chỉ là process/dependency smoke, không phải chat
  provider benchmark.
- External preflight read-only ngày 2026-08-29: AWS profile `p151` xác thực
  thành công nhưng account/region hiện có 0 EC2, 0 SSM-managed instance và 0
  Lightsail instance; vì vậy AWS Compose chưa được deploy. Vercel project
  `team-genius` có deployment production READY và domain trả HTTP 200, nhưng
  P-151 chưa được link vào project và working tree còn thay đổi chưa commit.
  Render API `/health` trả 200 nhưng `/ready` trả 503 (`qdrant=false`,
  `neo4j=false`), nên backend production chưa đạt dependency readiness. Vercel
  project đang có khoảng 80 biến môi trường (bao gồm cả backend/database
  secrets); theo kiến trúc web chỉ cần API URL và Firebase public config, nên
  các secret backend phải được rà soát và gỡ khỏi Vercel trước khi promote.
  Read-only Render API audit bổ sung xác nhận service `medipay-api` đang chạy
  commit `b416129` (không phải source hiện tại). PostgreSQL active release là
  `snapshot-c439751724ab7f10`; projection Qdrant còn locator logic
  `legal_graph_chunks__snapshot_c439751724ab7f10`, trong khi collection vật lý
  đúng là release-suffixed hybrid collection. Source hiện tại đã có resolver
  bounded, read-only để ánh xạ locator đó; bản đang chạy trên Render chưa có
  resolver nên readiness fail dù Qdrant và Neo4j managed vẫn reachable. Render
  cũng chưa khai báo `QDRANT_COLLECTION` và `NEO4J_DATABASE` trong service env.
  Không sửa bằng cách trỏ mù vào collection hoặc tắt readiness: phải triển khai
  commit hiện tại, giữ resolver, rồi chạy lại parity/readiness.

- Runtime API đi qua `src/runtime_entrypoint.py`, `src/main.py`, `src/api/` và
  `src/services/chat.py`.
- PostgreSQL là nguồn text/HTML, legal unit, bảng, conversation và release.
- Qdrant là semantic index; mọi hit phải hydrate lại passage từ PostgreSQL.
- Neo4j là index quan hệ văn bản; mọi kết quả quan hệ phải quay lại passage
  canonical trước khi sinh câu trả lời.
- Cache context hiện có trong `src/services/conversation_cache.py`; Redis không
  được coi là source of record.
- Migration hiện dùng SQL tuần tự, checksum và advisory lock tại
  `database/postgres/migrations/runner.py`; API không tự tạo bảng lúc khởi động.
- Langfuse đã có adapter; `/metrics` đã có endpoint Prometheus-compatible.
- Prompt runtime có fallback trong `src/agents/prompts.py` và đã có resolver
  Prompt Registry với cache/version lineage; khi control-plane lỗi vẫn ghi hash
  prompt local để tái lập.
- Query rewrite và generation cuối đều có đường structured output; renderer chỉ
  nhận `GroundedAnswer` đã validate và không phát raw chunk ra public API.
- Khi model trả câu “không tìm thấy” dù đã có evidence hợp lệ, guardrail dùng
  fallback trích xuất ngắn từ chính passage nguồn; fallback không tự thêm số
  tiền/năm/tỷ lệ còn thiếu và vẫn qua citation/claim audit.
- RAGAS chỉ chạy trong runtime đánh giá cô lập; release gate chưa bắt buộc cùng
  model/prompt/release với production.
- `web/` build thành Next standalone artifact và được phục vụ phía sau Nginx.

Các điểm chưa được phép coi là hoàn tất: OTel SDK đã được nối fail-open với OTLP
batch exporter nhưng collector/live redaction chưa được xác minh; live benchmark
với provider thật mới là canary nhỏ, và chưa có recovery/rollback evidence. Kiểm tra dependency thật
ngày 2026-08-28 cho thấy Qdrant kết nối được; runtime đã thêm resolver read-only
chọn collection vật lý theo `dataset_id` và exact point count nên readiness hiện
đạt dù `.env` cũ còn alias `medical_legal_active`. Locator logic trong
`release_projections` vẫn cần được đồng nhất khi vận hành. Neo4j kết nối được
nhưng parity đang lệch (1914 nodes/197 approved edges so với 1901/187). Đây là
blocker vận hành, không được chữa bằng cách tắt readiness hoặc đoán dữ liệu.

Canary provider ngày 2026-08-28 cho câu hỏi khám trái tuyến đã lấy được điều
khoản 50%/100% từ Luật BHYT sửa đổi 2024 và Nghị định 188/2025, không còn chọn
văn bản 2005. Sau khi sửa phép đo latency, warm-up release và lexical ranking,
retrieval thường ở khoảng 8--12 giây; generation live làm toàn request khoảng
15--25 giây. Accuracy đã cải thiện nhưng latency vẫn chưa đạt SLO, vì vậy chưa
được đánh dấu production-ready.

#### Bằng chứng live mới nhất (critical-bhyt-7, 2026-08-28)

Đã chạy read-only bằng `gpt-5.6-luna`, release
`snapshot-c439751724ab7f10` và collection vật lý tương ứng. Lần chạy mới nhất
(v12, serial) đạt **7/7** deterministic gate, P50 toàn request **14,65 giây**,
P95 **16,73 giây**. Các lần chạy trước từng dao động 5--6/7 và case lỗi thay
đổi do provider/model hoặc thứ tự candidate; vì vậy v12 chỉ là latest observed,
chưa phải bằng chứng ổn định pháp lý. Phép đo trước đó 46,7/84,3 giây đã bị loại
vì tính cả thời gian chờ semaphore; evaluator hiện đo từ lúc request được nhận
slot và prewarm release trước canary. Kết quả 6/7 vẫn chưa phải bằng chứng độ
chính xác pháp lý: các fact còn thiếu cần reviewer đối chiếu và latency chưa đạt
SLO mục 4. Các câu trả lời abstain khi thiếu evidence là hành vi an toàn.

Smoke GraphRAG read-only ngày 2026-08-29 đã xác nhận nhánh Neo4j chạy thật:
với câu hỏi có số hiệu và quan hệ sửa đổi, trace có `provider:neo4j` thành công
(~83 ms), channel `legal_graph` và 3 relation sau source-backed filtering.
Đây chỉ là bằng chứng wiring/fallback, không phải phê duyệt nội dung pháp lý hay
thay thế parity report.

Parity read-only ngày 2026-08-29 đã tìm đúng collection vật lý Qdrant
`medical_legal_hybrid_snapshot-c439751724ab7f10` với 14.393 điểm; alias/locator
cũ `legal_graph_chunks__snapshot-c439751724ab7f10` chỉ là metadata stale và
không còn làm verifier báo sai collection. Report vẫn **fail** đúng nguyên nhân:
source/parser fingerprint không khớp release lock, PostgreSQL dư 86 chunks,
Qdrant alias collection cũ dư 86 points (physical hybrid collection khớp 14.393)
và Neo4j lệch node/edge/reference parity. Không có thao tác xoá hoặc cutover nào
được thực hiện.

Route planner cũng đã sửa thứ tự ưu tiên: câu hỏi có số hiệu nhưng đồng thời hỏi
quan hệ/lịch sử không còn bị ép thành `exact` lookup; temporal/relational route
giữ provider `neo4j` trong bounded GraphRAG plan.

Nguyên nhân cần ưu tiên theo thứ tự:

1. Candidate recall theo release vẫn có các phase PostgreSQL 1--3 giây và
   hydration/operative expansion 5--8 giây; warm-up và giới hạn deadline đã
   giảm tail nhưng chưa đạt SLO. Lexical query hiện xếp theo `ts_rank_cd` thay
   vì thứ tự UUID; mọi truy vấn phase 3 hiện bị giới hạn bởi cùng route deadline
   và rơi về lexical khi hydration/rescue quá chậm.
2. Authority/currentness recall đã có seed theo loại văn bản và rescue theo
   passage nguồn, nhưng output/citation vẫn dao động giữa các lần live run.
   Đây là lỗi recall/rerank cần benchmark lặp lại, không giải quyết bằng bảng
   ánh xạ câu hỏi--văn bản.
3. `table_cell_facts` trong active release chưa có row
   `payload.review_status=accepted`; mọi phép tính phải tiếp tục fallback về
   passage canonical cho tới khi có projection đã review.
4. Neo4j vẫn lệch parity với manifest (1914 nodes/197 approved edges so với
   1901/187), vì vậy chỉ được dùng bounded expansion và phải có direct
   lexical/semantic fallback.

Việc đóng các nguyên nhân trên phải được đo lại bằng cùng manifest; không tăng
ngưỡng, không tắt guardrail và không đánh dấu PASS chỉ vì model sinh được văn
bản trôi chảy.

### 2.1. Bảng trạng thái chốt

| Hạng mục | Trạng thái | Điều kiện đóng |
|---|---|---|
| API/SSE/schema/renderer/idempotency/context cache | Đã có và có regression test | Giữ contract; chạy smoke có auth |
| PostgreSQL migration authority | Đã có | Chạy migration one-shot trên host, không `create_all` |
| Table-fact/calculator path | Contract và fallback đã có; active projection chưa có row `review_status=accepted` | Chạy migration index và chỉ nạp/đánh dấu fact đã review; nếu không có thì trả canonical passage, không tự tính |
| Qdrant/Neo4j connectivity | Qdrant readiness đã pass nhờ resolver; Neo4j parity chưa đạt | Ghi physical locator chuẩn và reconcile Neo4j theo manifest; chạy parity report |
| Langfuse prompt/trace | Adapter fail-open + OTel exporter đã có | Tạo/pin prompt thật, kiểm tra collector, redaction và lineage |
| Prometheus/Grafana/Nginx/Valkey/Ansible | Artifact đã có, chưa có evidence host | Bootstrap EC2, TLS, readiness, dashboard và restart drill |
| Accuracy/latency/cost | **Đang fail live gate**: latest observed 7/7, nhưng chưa có lặp cold/warm/concurrency; P50 14,65s, P95 16,73s | Ổn định recall/citation, reviewer fact, rồi chạy 100 câu độc lập + cold/warm/concurrency và cost ledger |
| Security/rollback/restore | Gitleaks/Trivy/SBOM local pass (0 HIGH/CRITICAL, 4 SBOM: API/web/migrate/research-worker); rollback/restore drill thật chưa có | Giữ scan artifact theo digest, chạy restore và rollback evidence trên host |

Không blocker nào trong bảng này được giải quyết bằng cách xóa dữ liệu đang phục
vụ, đổi active release mù hoặc bỏ qua readiness. Nếu một blocker phụ thuộc dịch
vụ bên ngoài (EC2, TLS, collector, reviewer), mã nguồn chỉ có thể đánh dấu
`pending_external_evidence`; không giả lập bằng fixture rồi gọi là production.

## 3. Stack cố định

| Lớp | Quyết định triển khai | Hợp đồng bắt buộc |
|---|---|---|
| Compute | Một EC2 Graviton chạy Docker Compose | Docker image bất biến, health/readiness, restart policy và giới hạn CPU/RAM |
| Edge | Nginx trên cùng host | TLS ACME, web reverse proxy, SSE, gzip, header bảo mật, rate-limit thô |
| API | Một container API, một Uvicorn worker ban đầu | Bounded semaphore, connection pool, timeout và circuit breaker |
| Web | Next.js standalone phục vụ phía sau Nginx | Asset hash bất biến, CSP và API domain rõ ràng |
| Context/cache | Valkey/Redis OSS trên host | Namespace theo UID/conversation/release/prompt, TTL và giới hạn token |
| Prompt/output | Langfuse Prompt Registry + Pydantic JSON Schema strict | Prompt version bất biến; model chỉ trả response schema, backend mới render public text |
| Canonical DB | Supabase PostgreSQL | Text/HTML, legal unit, table cell, conversation, release và idempotency |
| Semantic DB | Qdrant Cloud hiện có | Candidate locator; không phải nguồn citation cuối |
| Relation DB | Neo4j Aura hiện có | Bounded relation expansion; outage phải degrade sang direct retrieval |
| Identity | Firebase Auth hiện có | Firebase UID là owner boundary |
| Trace | Langfuse adapter hiện có; OpenTelemetry/OpenInference là bước bắt buộc kế tiếp | W3C trace ID, redaction, sampling và không chặn request |
| Metrics | Prometheus OSS + Grafana OSS trên host | Low-cardinality labels, retention giới hạn, dashboard SLO |
| Logs | JSON stdout/journald với retention giới hạn | Không ghi secret, cookie, full private prompt/evidence |
| Offline quality | Deterministic checks + RAGAS; Promptfoo red-team trong CI | Không chạy judge/red-team trên critical request path |
| Offline batch | Provider Batch API qua job CI cho eval/embedding | Không dùng batch cho chat interactive; kết quả có manifest và checksum |
| Image registry | GHCR với digest bất biến | Build một lần, promote cùng digest |
| CI/CD | GitHub Actions + OIDC + SSM Run Command + Ansible bootstrap | Không dùng access key dài hạn; deploy/rollback bằng image digest; Ansible chỉ dựng host |
| Supply-chain | Gitleaks + Trivy + SBOM trong CI | Chặn secret, CVE nghiêm trọng và image không có provenance |
| Secrets | `.env` trên host, mode `0600` | Không commit, không đưa vào image, rotate theo runbook |

License OSS không làm compute, disk, egress và thời gian vận hành thành miễn phí.
Đơn vị theo dõi là `cost / 1.000 successful chats` và `cost / accepted answer`.

### 3.1. Thành phần cố ý không dùng trong release AWS này

Không thêm Kubernetes/Helm, APISIX, Jenkins, Celery/BullMQ/RabbitMQ, Jaeger/ELK/
Loki, S3 hay một model gateway riêng. Một EC2 + Compose + Nginx đáp ứng tải ban
đầu với chi phí thấp hơn; GitHub Actions/OIDC thay Jenkins; Valkey thay queue
cache trả phí; Langfuse + Prometheus/Grafana thay stack trace/log trùng. Research
worker chỉ là tooling tùy chọn ngoài request path. Chỉ đưa các thành phần này vào
roadmap khi load/evidence chứng minh single-host không đạt SLO hoặc chi phí biên
thấp hơn phương án hiện tại.

## 4. Cổng chất lượng

| Cổng | Mục tiêu pass |
|---|---|
| Accuracy pháp lý | ≥95% trên bộ độc lập 100 câu; 0 lỗi nghiêm trọng |
| Claim support | ≥98% claim pháp lý quan trọng có passage hỗ trợ |
| Bảng/tính toán | 100% fixture xác định đúng giá trị, đơn vị và làm tròn |
| Rò rỉ nội bộ | 0 internal ID, raw chunk, score, debug field hoặc secret |
| Simple query | p50 ≤2 s, p95 ≤5 s |
| Hybrid query | p50 ≤4 s, p95 ≤8 s |
| Temporal/relational query | p95 ≤15 s, quá deadline phải degrade rõ ràng |
| SSE | first useful event p95 ≤1 s; 100% stream kết thúc bằng `done` hoặc `error` hợp lệ |
| Output contract | 100% generation hợp lệ JSON Schema; 0 raw chunk/field nội bộ trong public response |
| Prompt lineage | 100% answer có `prompt_version`, `model_version`, `release_id` và trace correlation |
| Eval reproducibility | Cùng manifest/model/prompt cho phép tái chạy; thiếu metric không được PASS |
| Red-team | 0 lỗi nghiêm trọng trong bộ prompt injection/leakage offline |
| Single-host availability | SLO công bố 99%; RTO ≤30 phút, RPO ≤24 giờ |
| Outage | Neo4j/Qdrant/Redis/provider lỗi đều có test hành vi và fallback |
| Economics | Không nhận tối ưu làm accuracy giảm hoặc p95 tăng quá 10% |

Pytest giữ vai trò regression/contract. Model thật, bộ câu độc lập và reviewer
độc lập mới là cổng chất lượng pháp lý.

## 5. Hợp đồng dữ liệu và request

Luồng chuẩn:

```text
Firebase auth -> Nginx -> validate input/idempotency -> route budget
  -> exact/lexical PostgreSQL + semantic Qdrant (song song)
  -> relation expansion Neo4j cho query quan hệ/thời gian
  -> hydrate canonical passage -> fuse/dedupe/rerank
  -> currentness/evidence guard -> calculator khi cần
  -> resolve prompt version -> grounded JSON-schema synthesis
  -> Pydantic/output guard -> public renderer -> SSE
  -> persist turn + idempotency result
```

PostgreSQL giữ canonical text/HTML và `table_cells`. Qdrant chỉ giữ vector và
payload locator. Neo4j chỉ giữ quan hệ release-scoped. Mọi candidate dùng trong
answer phải khớp `release_id`, content hash và source span. Graph hoặc vector
không bao giờ tự trở thành citation.

`Idempotency-Key` được unique theo `uid + endpoint + key` (conversation ID nằm
trong request hash); request hash
khác nhau trả `409`, request đang chạy replay cùng `request_id`, request hoàn tất
được hydrate từ turn canonical. Không lưu prompt/evidence đầy đủ trong record
idempotency.

### GraphRAG với Neo4j (đang dùng trong runtime)

Neo4j không bị bỏ qua và cũng không phải nguồn văn bản độc lập. Với route
`relational` hoặc `temporal` (ví dụ sửa đổi, thay thế, căn cứ, chuỗi hiệu lực),
runtime thực hiện **seed → bounded expand → re-retrieve → verify**:

1. Seed lấy `document_id` từ exact/lexical/Qdrant trong release đang active.
2. Neo4j mở cạnh `approved_evidence` cùng `dataset_id`, depth tối đa 1 (temporal
   tối đa 2), fan-out/limit hữu hạn; typed fact walk chỉ nhận
   `review_status=accepted`.
3. Mọi document đích được truy vấn lại passage canonical từ PostgreSQL (và dense
   khi cần), sau đó fuse/rerank cùng evidence trực tiếp.
4. Citation chỉ trỏ passage/HTML đã hydrate; graph label, edge, score và ID nội
   bộ không đi vào public response.

Route topical/table không gọi graph nếu graph không tăng recall đã đo được. Khi
Neo4j timeout, parity fail hoặc outage, direct lexical+dense vẫn trả lời trong
deadline và ghi `fallback_reason=neo4j_unavailable`; câu hỏi mà quan hệ là điều
kiện bắt buộc thì abstain an toàn. Đây là GraphRAG có kiểm soát, không phải
Graph-only RAG.

### 5.1 LLMOps contract

- **Prompt:** mọi prompt production có tên, version bất biến, label môi trường và
  checksum. Request ghi `prompt_version` cùng `model_version`, `release_id` và
  `route`; prompt được cache phía client để không thêm network hop vào request.
- **Generation:** model trả response theo JSON Schema strict/Pydantic. Backend
  validate schema, chạy currentness/citation/leakage guard rồi mới render text và
  citations công khai. Không gửi text tự do của model thẳng ra SSE.
- **Trace:** OpenTelemetry exporter fail-open đã wiring với stage metadata, sampling,
  bounded queue và redaction. Collector thật, W3C continuity và semantic
  attributes OpenInference đầy đủ vẫn là production gate; Langfuse tiếp tục là
  control-plane không nằm trên critical answer path.
- **Evaluation:** deterministic checks là gate bắt buộc; RAGAS đo retrieval và
  groundedness; Promptfoo chạy regression/red-team trong job CI riêng. Mỗi run
  lưu dataset hash, release ID, prompt/model version, dependency lock và kết quả
  reviewer. Thiếu metric hoặc lỗi evaluator là `NOT_OBSERVABLE`, không được PASS.
- **Batch:** các eval/embedding không tương tác được gom thành JSONL và chạy qua
  provider Batch API khi endpoint hỗ trợ. Batch không được nằm trên chat path;
  output/error file có TTL, checksum và manifest.
- **Supply chain:** CI chạy Gitleaks, Trivy và tạo SBOM trước khi publish image.
  Image production chỉ được promote bằng digest đã scan.

### 5.2 API contract cho web

Web chỉ dùng một `ApiClient` với `baseUrl`, Firebase ID token và các hàm typed;
không gọi database, provider hoặc route nội bộ trực tiếp.

**Quy ước chung**

- Base path: `/api/v1`; JSON dùng UTF-8 và `Content-Type: application/json`.
- Request có `Authorization: Bearer <Firebase ID token>`; `/health`, `/ready` và
  `/metrics` chỉ phục vụ kiểm tra/giám sát server.
- `X-Request-ID` do client gửi hoặc server sinh; response luôn trả ID này để tra
  log. `Idempotency-Key` bắt buộc cho `POST /chat`, `POST /chat/stream` và các
  mutation; client retry phải giữ nguyên key.
- Thành công trả JSON theo schema; lỗi luôn trả `{code,message,request_id,
  retryable}`, không trả HTML. `401/403/404/409/422/429/502/503/504` có mã ổn định.
- OpenAPI được sinh từ Pydantic/FastAPI; CI lưu snapshot schema và chặn breaking
  change không có migration. Web dùng generated TypeScript types, không tự đoán
  field hoặc parse `dict` không schema.

**Public endpoints**

| Method | Path | Body/query | Response web dùng |
|---|---|---|---|
| `POST` | `/chat` | `{message, conversation_id?, turn_id?}` | `{response, citations[], request_id, conversation_id, turn_id}` |
| `POST` | `/chat/stream` | Cùng body như `/chat` | SSE `meta/status/final/done/error` |
| `GET` | `/conversations?limit&cursor` | Không có body | `{items[], next_cursor}` |
| `GET` | `/conversations/{id}/turns?limit&cursor` | Không có body | `{items[], next_cursor}` |
| `DELETE` | `/conversations/{id}` | Không có body | `204` |
| `GET` | `/documents/{number}/html` | Path đã encode | `text/html` đã sanitize |
| `GET` | `/legal/timeline?document_number&as_of` | Query bắt buộc `document_number` | Timeline public đã hydrate |
| `POST` | `/eligibility/checklist` | `{topic, facts, conversation_id?}` | `{complete, missing[], next_question, facts_persisted}` |
| `POST` | `/calculator/bhyt` | Input Decimal + provenance | `{insurer_pays, patient_pays, formula_id, provenance}` |
| `POST` | `/calculator/bhyt/scenarios` | Tối đa 8 calculation | `{results[]}` |
| `GET` | `/status` | Không có body | `{status, agent}` |

`/health` chỉ kiểm tra process; `/ready` kiểm tra config và dependency tối thiểu;
`/metrics` chỉ bind nội bộ/monitoring. Ba endpoint này không được web dùng để
hiển thị nội dung pháp lý.

`citations[]` chỉ có `title`, `document_number`, `section_title`, `quote`,
`source_url`, `source_checked_at`; không có database ID, score hoặc raw chunk.
Collection dùng `limit` bounded và `next_cursor` opaque để web không phải biết
kiểu phân trang của PostgreSQL.

**SSE contract**

```text
event: meta\ndata: {"schema":"chat.v1","request_id":"...","conversation_id":"..."}
event: status\ndata: {"stage":"retrieval","elapsed_ms":123}
event: final\ndata: {"response":"...","citations":[]}
event: done\ndata: {"ok":true}
```

Mỗi event có `id` tăng dần; `Cache-Control: no-cache, no-transform`,
`X-Accel-Buffering: no`. Server chỉ phát answer sau guardrail, không phát token
model hoặc chunk thô. Lỗi dùng `event: error` với cùng `request_id`; web hiển thị
`message` an toàn và quyết định retry theo `retryable`.

**Web adapter tối thiểu**

```text
api.chat(input) -> Promise<ChatResult>
api.streamChat(input, onEvent, signal) -> Promise<ChatResult>
api.listConversations(cursor) -> Promise<Page<ConversationSummary>>
api.getTurns(id, cursor) -> Promise<Page<ConversationTurn>>
api.deleteConversation(id) -> Promise<void>
api.getDocumentHtml(number) -> Promise<string>
api.getTimeline(number, asOf) -> Promise<LegalTimeline>
api.checkEligibility(input) -> Promise<EligibilityChecklist>
api.calculate(input) -> Promise<BenefitCalculation>
```

Web parser kiểm `Content-Type`, `event`, `data` và schema trước khi render; HTTP
error hoặc HTML body luôn đi qua `ApiError`, không đưa vào transcript.

## 6. Ma trận file và trạng thái

### Giữ làm lõi (đã có trong runtime)

| Đường dẫn | Trách nhiệm |
|---|---|
| `src/main.py`, `src/runtime_entrypoint.py`, `src/config.py` | Entrypoint và config duy nhất |
| `src/api/`, `src/application/`, `src/domain/`, `src/integrations/` | HTTP, use case, domain và provider boundary |
| `src/services/retrieval.py`, `reranker.py`, `planner.py`, `llm.py`, `claims.py`, `circuit.py`, `metrics.py` | Đường chat cốt lõi |
| `src/services/calculator.py`, `document_viewer.py`, `legal_timeline.py`, `eligibility_checklist.py` | Feature sản phẩm |
| `src/services/conversations.py`, `conversation_context.py`, `conversation_cache.py` | Context riêng tư và bounded |
| `src/agents/` | Orchestration hiện hành với typed state |
| `database/postgres/migrations/` và `runner.py` | DDL forward-only, checksum, lock |
| `database/{postgres,neo4j,qdrant}/` | Contract, release tool và restore tool từng DB |
| `Dockerfile`, `Dockerfile.migrate`, `docker-compose.yml`, `ops/compose/production.yml` | API, migration, local và AWS single-host runtime |
| `requirements/{runtime,dev,migrate}.{in,lock}` | Dependency profile đang dùng |
| `eval/`, `tests/` | Bộ độc lập, regression và benchmark |
| `eval/promptfoo.yaml`, `eval/redteam/` | Regression và red-team offline, không được import vào runtime |
| `web/app`, `web/components`, `web/lib` | UI chat, viewer, auth và feature panel |
| `.env` | Local/host secret, không commit |

### Còn phải hoàn tất trước production gate

| Đường dẫn | Việc phải làm |
|---|---|
| `src/integrations/otel.py`, `src/integrations/langfuse.py`, `src/main.py` | Đã nối OTel batch fail-open và Langfuse stage spans; còn kiểm tra collector/W3C/redaction live |
| `eval/` và production evidence | Chạy live provider benchmark, 100 câu độc lập, reviewer pháp lý và cost/latency ledger |
| `database/{postgres,qdrant,neo4j}` | Reconcile active release; Qdrant locator phải khớp projection, Neo4j phải đạt node/edge parity |
| `ops/ansible/`, `ops/compose/`, `ops/nginx/` | Bootstrap EC2 thật, cấp TLS, cấu hình CORS domain, migration one-shot và rollback |
| `.github/workflows/` | Dùng OIDC/SSM với repository secrets tối thiểu; publish/promote cùng image digest |
| `ops/runbooks/` | Bổ sung evidence restore/outage và lịch cleanup idempotency ngoài request path |

### Xóa khỏi runtime sau khi xác nhận backup

- generated artifact: `web/node_modules/`, `web/.next/`, cache và `__pycache__`;
- image/requirement dành cho worker hoặc pipeline không được runtime gọi;
- deployment manifest và script của nền tảng cũ;
- prototype graph/research/global/experience không được import từ entrypoint;
- dữ liệu staging, snapshot trùng và kết quả eval đã commit sau khi manifest được
  lưu ngoài Git.

Không xóa migration đã áp dụng, `.env`, release manifest hoặc backup trước restore
drill. Cleanup phải dry-run, liệt kê target tuyệt đối và kiểm tra trong repo.

## 7. Kế hoạch thực thi

### Phase 0 — Khóa hiện trạng

1. Tag commit, ghi image digest và active release ID.
2. Backup/restore thử PostgreSQL, Qdrant và Neo4j; kiểm tra content hash và release
   parity.
3. Rotate mọi credential từng xuất hiện ngoài secret store; `.env.example` chỉ còn
   placeholder.
4. Chạy Gitleaks trên Git/Docker context/CI artifact; chạy Trivy và tạo SBOM cho
   image ứng viên.

**Exit:** có rollback commit, backup manifest, restore evidence, không có secret
trong tracked files và image không có CVE vượt ngưỡng release.

### Phase 1 — Dọn repo

1. Dry-run cleanup generated artifact và đo kích thước fresh clone.
2. Archive dữ liệu raw/eval/history theo release manifest rồi loại khỏi runtime.
3. Giữ một migration authority, một requirements runtime và một Docker entrypoint.
4. CI chặn file lớn, generated output và secret quay lại.

**Exit:** fresh clone build được; source không chứa dependency/runtime trùng.

### Phase 2 — Khóa đường chat

1. Đóng băng SSE/event/citation contract bằng contract test.
2. Tách repository và orchestration theo boundary mục 6.
3. Chuẩn hóa route budget: exact, lexical, semantic, relation và table.
4. Đặt timeout, semaphore, pool, circuit và fallback cho từng dependency.
   Migration `20260837_retrieval_document_indexes.sql` bổ sung locator index cho
   `legal_units`; không tạo index trùng với access path chunks hiện có để giữ
   dung lượng Supabase.
5. Hoàn thiện idempotency replay/conflict và PostgreSQL persistence.
6. Hoàn thiện context cache UID-scoped, bounded token/turn, TTL và invalidation.
7. Đưa prompt vào Langfuse Prompt Registry, pin version/checksum và ghi lineage
   vào request.
8. Đổi generation sang JSON Schema strict/Pydantic; chỉ renderer sau guard mới
   được tạo public response/SSE.
9. Hoàn tất semantic attributes OpenInference, collector/W3C context và redaction live trên nền OTel đã wiring.
10. Xác nhận citation/output guard không lộ ID, score, chunk hoặc trace nội bộ.

**Exit:** smoke 5 câu pass, structured output 100%, stream luôn hợp lệ,
dependency outage không làm hỏng format và benchmark 1–2 câu không còn trả
nguyên chunk.

### Phase 3 — Dựng AWS một host

1. Tạo một EC2 Graviton, security group chỉ mở 80/443; quản trị đi qua SSM
   outbound, bật disk encryption, automatic patching và restart policy.
2. Dùng `ops/ansible/` để cài Docker Engine/Compose, Nginx, Valkey, Prometheus và
   Grafana; mount volume riêng với giới hạn disk/retention.
3. Đặt `.env` mode `0600` trên host; không truyền secret qua image hoặc log.
4. Trỏ DNS custom domain vào host; Nginx cấp TLS ACME, proxy web và SSE
   SSE tới API.
5. Chạy migration one-shot bằng `Dockerfile.migrate`, sau đó mới restart API.
6. Cấu hình OTel/OpenInference batch export tới Langfuse và `/metrics` cho
   Prometheus; kiểm tra prompt lineage, redaction, sampling, alert và dashboard.
7. Chạy smoke auth/chat/viewer/calculator/context và kiểm tra rollback image.

**Exit:** host tái tạo được từ runbook, domain/auth/chat/SSE/viewer pass và có
recovery drill.

### Phase 4 — CI/CD

1. Pull request chạy lint, type, unit, contract, Docker build, Gitleaks, Trivy và
   SBOM.
2. Main build một digest, publish GHCR, chạy migration và smoke trên host.
3. Deploy dùng OIDC/SSM; host pull đúng digest, health/readiness pass rồi switch.
4. Rollback bằng digest trước đó; migration chỉ forward-compatible.
5. Lưu release ID, dataset/prompt/model checksum, commit, image digest, security
   report và smoke result thành artifact.
6. Job CI riêng chạy Promptfoo regression/red-team với concurrency bounded; không
   đưa Promptfoo hoặc dữ liệu pháp lý vào production image.

**Exit:** deploy/rollback tự động lặp lại được mà không cần thao tác sửa tay trong
container đang chạy.

### Phase 5 — Accuracy, latency và chi phí

1. Chạy canary 5 câu; chỉ mở benchmark 100 câu sau khi health/stream pass.
2. Reviewer độc lập chấm correctness, completeness, citation và internal leakage.
3. Dùng RAGAS cho context precision/recall, faithfulness và answer relevance;
   deterministic/reviewer vẫn là gate cuối.
4. A/B từng thay đổi retrieval/rerank/prompt/model; không đổi nhiều biến một lần.
5. Load test cold/warm, concurrency ramp, cache hit/miss, Neo4j/Qdrant/Redis outage.
6. Tune Uvicorn worker, semaphore, DB/provider pool, Nginx buffering và cache TTL.
7. Đo TTFT, p95 stage, token, embedding, rerank, cache hit và cost/1.000 chat.
8. Dùng Batch API cho eval/embedding offline khi provider hỗ trợ; không dùng cho
   request tương tác.
9. Giữ thay đổi chỉ khi accuracy đạt cổng và latency/cost không regression quá 10%.

**Exit:** toàn bộ cổng mục 4 pass trên production-like load và recovery drill.

### Thứ tự sửa bắt buộc trước khi chạy lại benchmark

Đây là thứ tự đã chốt; không mở thêm framework hay ingest corpus mới trong khi
các bước này chưa có evidence:

1. **Ổn định release locator:** đối chiếu `release_projections` với số điểm
   Qdrant thực tế, ghi locator vật lý vào manifest, và làm một warm-up read-only
   trước benchmark. Mọi lần resolver không khớp phải là lỗi quan sát được, không
   âm thầm chạy collection alias.
2. **Giảm chi phí recall:** đo riêng thời gian checkout SQL, release recall,
   lexical, embedding, Qdrant và hydrate; thêm cache ngắn hạn cho release/title
   lookup và giới hạn đúng một pass lexical trong deadline. Không giảm candidate
   hoặc tắt currentness guard chỉ để đạt latency.
3. **Sửa currentness/authority:** kiểm thử các cặp văn bản hiện hành--lịch sử,
   bắt buộc citation phải thuộc `accepted_document_numbers` của fixture hoặc
   abstain có lý do. Không hardcode số hiệu theo từng câu hỏi.
4. **Khóa đường bảng:** xác nhận schema `table_cell_facts`, chỉ dùng fact đã
   review và chạy calculator fixture; nếu projection trống thì trả passage + yêu
   cầu thêm dữ kiện, tuyệt đối không tự suy diễn con số.
5. **Đo lại 7 câu trước 100 câu:** hai lần cold và ba lần warm, concurrency 1/3;
   lưu latency stage, TTFT, citation và reason code. Chỉ khi 7/7 deterministic
   pass mới mở suite 100 câu và human legal review.
6. **Sau cùng mới dựng AWS:** host, TLS, OTel collector, dashboard, rollback và
   restore drill không được che khuất lỗi chất lượng của request path.

### Việc cần làm ngay sau khi chốt tài liệu

| Thứ tự | Việc cụ thể | Artifact đóng việc |
|---:|---|---|
| 1 | Chụp read-only manifest của PostgreSQL/Qdrant/Neo4j; ghi locator vật lý Qdrant và parity report Neo4j | `release_manifest.json`, `qdrant_locator.json`, `neo4j_parity.json` |
| 2 | Chạy migration pending trên bản sao/host staging; kiểm tra table-fact index và giữ fallback khi accepted facts bằng 0 | migration log + calculator smoke |
| 3 | Đo 2 cold + 3 warm ở concurrency 1/3 cho critical-bhyt-7; lưu TTFT, từng stage, citation và fallback reason | benchmark JSONL + cost ledger |
| 4 | Đóng OTel collector/W3C/redaction và dashboard Prometheus/Grafana trên EC2 | trace screenshot/export + alert drill |
| 5 | Chạy auth/chat/SSE/viewer/calculator/context, sau đó rollback image và restore backup | smoke + rollback/restore evidence |
| 6 | Chỉ khi 1–5 pass mới chạy 100 câu độc lập và reviewer pháp lý; nếu không đạt thì giữ `production_promotion_allowed=false` | signed quality report |

Mọi thay đổi retrieval/rerank/prompt/model phải được A/B trên cùng release và
manifest. Một thay đổi chỉ được giữ khi không làm giảm accuracy và không làm
P95 hoặc chi phí trên accepted answer tăng quá 10%.

## 8. Vận hành bắt buộc

- Nginx không cache answer cá nhân; cache public document phải release-scoped.
- Valkey chỉ chứa context/retrieval/rate state bounded; PostgreSQL là source of record.
- Langfuse outage chỉ làm drop/buffer telemetry giới hạn, không chặn answer.
- Neo4j outage bỏ relation expansion và chạy direct lexical/semantic route.
- Qdrant outage chạy exact/lexical/PageIndex fallback; không bịa evidence.
- Redis outage đọc context từ PostgreSQL và fail closed cho route chi phí cao.
- Provider timeout trả SSE `error` hợp lệ, retry tối đa một lần trong deadline.
- Prompt/model/release đổi phải tạo evaluation run mới; không sửa đè version đang
  phục vụ production.
- Batch evaluation chỉ chạy ngoài request path và phải có checksum trước khi dùng
  làm release gate.
- Gitleaks/Trivy/SBOM là điều kiện trước khi image được promote.
- Backup phải có checksum, retention, restore drill và lịch rotation credential.

## 9. Definition of done

Repo chỉ còn một runtime và một migration authority; source không chứa secret hay
artifact sinh; AWS host dựng được bằng Ansible runbook; web, auth, chat, SSE,
viewer, calculator, timeline, checklist và context hoạt động; generation strict
schema và public renderer không lộ nội bộ; prompt/model/release lineage đầy đủ;
RAGAS và Promptfoo artifact tái lập được; 100 câu độc lập đạt accuracy;
latency/cost đạt cổng; security scan, rollback/restore/outage đã chạy thật; tài liệu
khớp code.

**Trạng thái hiện tại:** chưa đạt Definition of done. Code/contract local đã
được kiểm chứng, nhưng parity Neo4j, accepted table facts, telemetry/host,
security report, recovery drill và quality/latency evidence live còn thiếu.
Release chỉ được gắn `production-ready` khi tất cả artifact ở bảng trên tồn tại
và promotion verifier trả `production_promotion_allowed=true`.
