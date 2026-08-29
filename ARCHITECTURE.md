# MediPay — kiến trúc AWS một host và LLMOps

> Bản chuẩn ngày 2026-08-29. Kiến trúc này là đường triển khai duy nhất của P-151:
> một EC2 Graviton, Docker Compose, Nginx, Valkey/Redis OSS, Prometheus và Grafana;
> Langfuse là telemetry/control-plane; OpenTelemetry exporter đã wiring fail-open,
> còn collector/semantic OpenInference là gate xác minh kế tiếp. PostgreSQL, Qdrant, Neo4j và Firebase
> là data plane hiện có; Promptfoo và RAGAS chỉ chạy ngoài request path.

`AUDIT.md` chỉ lưu lịch sử và không được dùng thay cho release manifest hoặc
quality gate trong kiến trúc vận hành hiện tại.

### Architecture decision record (đã chốt)

| Quyết định | Chọn | Lý do/giới hạn |
|---|---|---|
| Compute | EC2 Graviton + Docker Compose | Ít phí control-plane, đủ cho tải ban đầu; scale ngang chỉ sau load evidence |
| Edge | Nginx | TLS, SSE, CORS, header và rate-limit thô; không thay application auth |
| Cache/state ngắn hạn | Valkey/Redis OSS | Context UID-scoped, retrieval cache, quota; PostgreSQL vẫn là source of record |
| Metrics | Prometheus + Grafana OSS trên EC2 | Không phụ thuộc dịch vụ managed; retention/disk phải bounded |
| Tracing/prompt | OTel/OpenInference + Langfuse | OTel là contract fail-open; Langfuse không nằm trên critical path |
| Async/batch | CI job + provider Batch API cho eval/embedding | Không thêm queue broker và không dùng batch cho chat tương tác |
| Orchestration | LangGraph `StateGraph` hiện có + typed application state | Giữ một graph request path; không thêm graph framework/K8s/APISIX trước khi có bằng chứng SLO |
| Canonical data | PostgreSQL + Qdrant + Neo4j hiện có | Không ingest/extract lại và không tạo store mới trong release này |

Đây là quyết định triển khai, không phải tuyên bố mọi blocker đã đóng. Trạng
thái production vẫn bị khóa bởi parity, quality, latency, telemetry và recovery
evidence nêu ở dưới.

## 1. Nguyên tắc

### Trạng thái triển khai

Runtime đã có schema generation strict, public renderer, prompt/model/release
lineage, response IDs, PostgreSQL idempotency replay/conflict và các gate scan
supply-chain. AWS Compose/Nginx/Ansible artefacts đã có; 232 backend test và
112 corpus/eval test (tổng 344) hiện pass. OTel exporter đã wiring fail-open; collector/OpenInference semantic
validation, live dependency parity, load test, ACME
certificate và recovery drill vẫn là điều kiện trước promote production. Không
được coi một test pass là bằng chứng cho độ chính xác pháp lý hoặc SLO live.
Deploy contract hiện cấu hình đúng. Trivy local hiện báo 0 HIGH/CRITICAL cho bốn
image và SBOM đã được tạo; scan live theo image digest, rollback và restore drill
vẫn phải được ghi trên host production.
Migration rehearsal disposable cũng đã pass (22 migration áp dụng lần đầu, lần
hai skip toàn bộ); migrator và research-worker hiện dùng image riêng, không dùng
artifact `corpus-worker` cũ.
Production-like local Compose smoke ngày 2026-08-29 cũng pass: toàn bộ stack
PostgreSQL/Qdrant/Neo4j/Redis/migrate/API/web healthy, `/health` và `/ready`
đều trả HTTP 200; healthcheck dùng đúng Python path của image Slim.
Readiness load 20 request đồng thời đạt 20/20 (P50 ~309 ms, P95 ~312 ms),
nhưng không được dùng thay cho live chat/provider SLO.
External preflight cùng ngày xác nhận AWS credential hợp lệ nhưng chưa có EC2,
SSM-managed instance hoặc Lightsail instance. AWS EC2 + Compose là production
target duy nhất; Vercel/Render đã tồn tại chỉ là platform legacy và không phải
promotion prerequisite. Vercel project còn khoảng 80 env vars, trong đó có
backend/database secrets; không dùng chúng cho AWS và không đưa secret backend
vào frontend.
Live Langfuse probe xác nhận label `medipay-system:production` hiện chưa tồn tại;
resolver fail-open về local hash trong giới hạn 2 giây và cache kết quả, nhưng
prompt production thật phải được tạo/pin trước khi promote.
Render service legacy đang chạy commit `b416129`, trước bounded
release-collection resolver. Trạng thái đó không ảnh hưởng AWS production và
không cần sửa để đóng AWS gate. Khi dựng AWS, phải dùng source hiện tại, khai
báo đủ `QDRANT_COLLECTION`/`NEO4J_DATABASE`, sau đó xác minh parity; không
hardcode collection hay bỏ qua readiness.

Verifier promotion đã được đồng bộ với status ledger tiếng Việt của PLAN và
không còn báo false-positive; trạng thái production hiện là `false` khi còn
blocker external/live.

Live preflight gần nhất: PostgreSQL, Qdrant và Neo4j đều kết nối được. Runtime
đã thêm resolver Qdrant read-only, chọn collection vật lý theo `dataset_id` và
exact point count nên Qdrant readiness đạt dù host env còn alias cũ;
`release_projections.locator` vẫn cần được ghi lại đúng physical locator. Neo4j
đang lệch parity so với manifest (1914 nodes/197 approved edges so với 1901/187).
Cách xử lý đúng là cập nhật locator/reconcile có backup và chạy parity report;
không tắt readiness, xóa mù hoặc fallback thành dữ liệu không có căn cứ.

### Quyết định vận hành sau live benchmark (2026-08-29)

Smoke 7 câu và suite độc lập 100 câu đã chạy read-only bằng `gpt-5.6-luna`,
PostgreSQL/Qdrant/Neo4j managed thật, release
`snapshot-c439751724ab7f10`, collection vật lý theo release. Smoke đạt 6/7;
hai lượt 100 câu đạt 74--75/100 deterministic pass, P50 11,33--11,93 giây và
P95 20,59--20,98 giây. Usage thực mỗi lượt khoảng 341--350 nghìn input và
26,9--27,7 nghìn output tokens; cost generation ledger $0,101424--$0,102354
theo rate model, chưa gồm embedding/cache/discount. Đây là đo usage tái lập được
chứ không phải hóa đơn billing. Các patch authority scoping/anchor và chống DB
stampede cải thiện recall nhưng vẫn còn 25--26 lỗi cơ học và tail latency cao.
Kiến trúc vì vậy **chưa production-ready**; blocker nằm ở
release-scoped recall/rerank, SQL hydration, projection bảng trống, Neo4j parity,
observability và recovery, không phải ở việc thiếu thêm graph framework.

Smoke read-only ngày 2026-08-29 đã xác nhận GraphRAG wiring live: trace
`provider:neo4j` thành công khoảng 83 ms và sau khi re-hydrate/filter còn 3
relation `legal_graph`. Đây là bằng chứng đường chạy, không phải bằng chứng
Neo4j parity hay độ đúng pháp lý.

Verifier parity read-only cũng đã resolve đúng collection Qdrant vật lý có
14.393 điểm thay vì alias cũ; report vẫn fail vì source fingerprint/chunk
artifact và Neo4j parity chưa khớp manifest. Đây là lỗi dữ liệu/release cần
reconcile có backup, không phải lỗi GraphRAG routing.

Không được bù các blocker bằng cách thêm graph/community store, hardcode câu hỏi
hoặc trả node/edge/chunk trực tiếp cho người dùng. Direct lexical + semantic
retrieval là đường chính; graph chỉ là accelerator có bounded budget.

Route deadline hiện bao trùm cả hydration và document-rescue SQL; nếu phase này
quá chậm, runtime hủy nhánh tùy chọn và trả về lexical evidence đã xác minh thay
vì để một truy vấn managed Postgres kéo treo toàn bộ request.

Thứ tự xử lý bắt buộc là: (1) xác minh locator vật lý và warm-up release, (2)
đo và rút ngắn từng stage SQL/lexical/embedding/Qdrant/hydrate trong cùng
deadline, (3) ổn định citation/currentness theo manifest mà không hardcode mapping,
(4) xác nhận `table_cell_facts` accepted hoặc fallback canonical passage, rồi
(5) chạy lặp 7 câu cold/warm và kiểm tra độ ổn định trước khi mở suite 100 câu.
Neo4j chỉ là bounded
relation accelerator; khi parity chưa khớp hoặc service lỗi, direct
lexical+semantic retrieval vẫn là đường trả lời chính.

1. Passage pháp luật canonical là evidence duy nhất; vector, graph, summary và
   memory chỉ là chỉ mục hoặc context điều hướng.
2. Mọi candidate phải hydrate lại từ PostgreSQL với đúng release, content hash và
   source span trước khi đưa vào prompt.
3. Câu hỏi pháp lý luôn re-retrieve evidence mới; context hội thoại chỉ bổ sung
   tham chiếu đã giới hạn theo owner.
4. Mỗi dependency có timeout, deadline, circuit breaker, fallback và telemetry.
5. Không gọi graph, embedding, planner hoặc model khi route xác định không cần.
6. Image, web artifact và corpus release có checksum; deploy/rollback dùng digest.
7. Secret không nằm trong Git, image, log, prompt trace hoặc câu trả lời.
8. Generation dùng JSON Schema strict/Pydantic; model output không bao giờ đi thẳng
   ra browser.
9. Nếu model phát generic empty-result sau khi retrieval đã có passage hợp lệ,
   guardrail chỉ trích xuất đoạn ngắn từ nguồn và vẫn chạy citation/claim audit;
   không được tự bịa con số hoặc điều kiện còn thiếu.
10. Prompt version, model version và release ID là một lineage bất biến cho mỗi
   answer.
11. Tối ưu theo `cost / accepted answer`, không tối ưu bằng cách làm giảm accuracy.

## 2. Sơ đồ triển khai

```text
DNS
 |
 v
Nginx (TLS ACME, web/SSE proxy, header/rate-limit)
 |
 +--> API container (FastAPI + Uvicorn)
 |       +--> Valkey/Redis OSS (context, cache, rate state)
 |       +--> Supabase PostgreSQL (canonical + conversation + idempotency)
 |       +--> Qdrant Cloud (semantic candidates)
 |       +--> Neo4j Aura (bounded document relations)
 |       +--> Firebase Auth / model provider
 |       `--> OTel/OpenInference batch -> Langfuse Prompt/Trace/Eval
 |
 +--> Prometheus OSS -> Grafana OSS
 |
 `--> journald/Docker JSON logs (bounded retention)

GitHub Actions --OIDC/SSM--> EC2 -> GHCR image digest -> ops/compose/production.yml
 |
 +--> RAGAS quality gate
 +--> Promptfoo regression/red-team
 `--> Gitleaks + Trivy + SBOM
```

EC2 chỉ mở 80/443; quản trị đi qua SSM outbound. Service nội bộ bind loopback hoặc
network Docker. Nginx kết thúc TLS, proxy web/SSE. API không
expose trực tiếp ra Internet.

Grafana chỉ được truy cập qua Nginx tại `/grafana/` với đăng nhập bắt buộc; không
publish port Grafana hoặc Prometheus ra Internet.

## 3. Thành phần và trách nhiệm

| Thành phần | Trách nhiệm | Không được làm |
|---|---|---|
| Nginx | TLS, web/SSE proxy, gzip, security headers, coarse rate-limit | Không thay database, context cache hay idempotency |
| FastAPI API | Auth boundary, use case, SSE envelope, error mapping | Không query DB trực tiếp từ route |
| PostgreSQL | Canonical text/HTML, legal units, table cells, release, conversation, idempotency | Không chứa snapshot trùng hoặc staging kéo dài |
| Qdrant | Dense candidate locator theo release | Không là nguồn text/citation cuối |
| Neo4j | Document/reference relation và bounded expansion | Không trả node/edge text làm căn cứ |
| Valkey/Redis | Context cache, retrieval cache, rate state, single-flight | Không là source of record hoặc durable queue |
| Calculator | Decimal/rational arithmetic và kiểm tra đơn vị | Không để LLM tự tính |
| Prompt resolver | Lấy prompt production theo version/checksum và cache client-side | Không fetch prompt không version hoặc thay đổi giữa request |
| Structured renderer | Validate Pydantic/JSON Schema, chạy output guard và render public text | Không phát raw model text/chunk ra SSE |
| OpenTelemetry/OpenInference | Trace context và stage attributes chuẩn hóa | Không ghi secret/full private content |
| Langfuse | LLM/retrieval trace, token/cost, prompt version và evaluation | Không nằm trên critical answer path |
| Prometheus | SLO, latency, saturation, error và cache metrics | Không dùng user/query/document làm label |
| Grafana | Dashboard và alert từ Prometheus | Không lưu prompt/evidence riêng tư |
| RAGAS | Offline context/faithfulness/relevance scoring | Không là gate duy nhất và không chạy live |
| Promptfoo | CI regression và red-team prompt injection/leakage | Không vào production image hoặc request path |
| GitHub Actions | Test, scan, build digest, deploy và rollback command | Không giữ access key dài hạn |
| GHCR | Lưu image immutable theo digest | Không dùng tag mutable cho production |
| Ansible | Bootstrap EC2/Docker/Nginx/Valkey/monitoring idempotent | Không điều phối request hoặc thay migration runner |
| Gitleaks/Trivy/SBOM | Chặn secret, CVE và image thiếu provenance trước promote | Không chạy trong hot path |
| SQL migration runner | Áp dụng DDL tuần tự, checksum và advisory lock | Không chạy DDL khi API startup |

### Thành phần không nằm trong release AWS hiện tại

Kubernetes/Helm, APISIX, Jenkins, Celery/BullMQ/RabbitMQ, Jaeger/ELK/Loki, S3 và
model gateway riêng đều bị loại khỏi request path. GitHub Actions/OIDC thay
Jenkins; Nginx + Valkey + Langfuse + Prometheus/Grafana đủ cho single-host ban
đầu. Chỉ xem xét mở rộng sau khi có số liệu chứng minh SLO hoặc chi phí không còn
đạt trên EC2 Compose.

## 4. Dữ liệu và release

PostgreSQL là nguồn sự thật:

- `documents`, `legal_units`, `chunks`, HTML và provenance;
- `table_cells` cho bảng và giá trị có source row;
- `table_cell_facts` chỉ là projection typed tùy chọn; chỉ row có
  `payload.review_status=accepted` mới được dùng cho tính toán, còn projection
  legacy/pending luôn rơi về passage canonical;
- release pointer, content hash, currentness/effective interval;
- conversation turns, bounded facts và idempotency records.

Operative retrieval dùng access path `(dataset_id, document_id, chunk_order)` có
sẵn trên `chunks` và locator index `legal_units(dataset_id, document_id, ...)`
được thêm ở migration `20260837`; không nhân đôi index trên bảng chunks để tránh
tăng dung lượng Supabase.

Qdrant payload chỉ chứa locator, release và vector metadata. Neo4j lưu relation
release-scoped. Mọi index rebuild có manifest và parity check với PostgreSQL trước
khi đổi active release. Không có distributed transaction giữa ba store; PostgreSQL
release pointer là control plane.

Một release gồm `release_id`, source commit, input manifest, content hash, index
hash, migration head và timestamp. Mỗi answer còn gắn `prompt_version` và
`model_version`; ba định danh này tạo thành lineage bất biến. Rollback chỉ đổi
pointer hoặc image digest đã được kiểm chứng; không sửa trực tiếp dữ liệu active.

## 5. Request path

```text
authenticate Firebase UID
 -> Nginx limit/header
 -> validate body + Idempotency-Key
 -> normalize query + resolve date/reference
 -> route budget
 -> exact/lexical PostgreSQL || semantic Qdrant
 -> relation expansion Neo4j cho query quan hệ/thời gian
 -> hydrate canonical passages
 -> dedupe + fuse + rerank + diversity
 -> currentness/evidence/claim guard
 -> calculator/table formatter khi query số
 -> resolve cached prompt version
 -> grounded LLM synthesis (JSON Schema strict)
 -> Pydantic validation + citation/output/leakage guard
 -> public renderer tạo response/citations
 -> persist turn and idempotency result
 -> versioned SSE events
```

SSE chỉ phát các event `meta`, `status`, `final`, `done`, `error`. Lỗi dependency
được map thành event hợp lệ trước khi đóng stream; HTML error page và token model
thô không bao giờ được gửi vào stream.

### 5.1 LLMOps runtime contract

Prompt production được quản lý trong Langfuse Prompt Registry, có version,
environment label và checksum. SDK cache prompt ở phía client; request không gọi
thêm một network hop cho mỗi câu hỏi. Trace phải ghi prompt/model/release/route
nhưng chỉ lưu nội dung đã redaction.

Model không trả public text trực tiếp. Response schema tối thiểu gồm:

```json
{
  "conclusion": "string",
  "conditions": ["string"],
  "exceptions": ["string"],
  "citations": [
    {"title": "string", "document_number": "string", "section_title": "string", "quote": "string", "source_url": "string"}
  ],
  "uncertainty": "string|null"
}
```

Pydantic kiểm tra kiểu, độ dài, trường bắt buộc và citation provenance. Renderer
chuyển schema thành văn bản tiếng Việt và SSE envelope; raw chunk, model metadata,
database ID và debug field không thể đi ra browser.

RAGAS chỉ chạy offline trên cùng `release_id`, prompt version và model version đã
định nghĩa cho production. Promptfoo chạy trong CI job riêng để regression và
red-team prompt injection/leakage; không cài vào image runtime và không đưa dữ liệu
pháp lý lên hosted grader.

Eval/embedding không tương tác được gom thành JSONL Batch API khi provider hỗ trợ.
Batch có manifest, checksum, giới hạn concurrency, TTL output và không được nằm
trên đường chat.

### 5.2 API contract cho web

API public có base path `/api/v1`, JSON UTF-8 và một error envelope duy nhất:

```json
{
  "code": "dependency_timeout",
  "message": "Dịch vụ đang bận, vui lòng thử lại.",
  "request_id": "req_01J...",
  "retryable": true
}
```

Browser gửi Firebase ID token trong `Authorization: Bearer`. `X-Request-ID` được
giữ xuyên Nginx/API/trace; server tự sinh khi thiếu. `Idempotency-Key` là header
bắt buộc cho chat và mutation, giữ nguyên khi retry.

| Method | Path | Request | Response |
|---|---|---|---|
| `POST` | `/chat` | `{message, conversation_id?, turn_id?}` | `{response, citations[], request_id, conversation_id, turn_id}` |
| `POST` | `/chat/stream` | Cùng request `/chat` | SSE `meta`, `status`, `final`, `done`, `error` |
| `GET` | `/conversations` | `limit`, opaque `cursor` | `{items[], next_cursor}` |
| `GET` | `/conversations/{id}/turns` | `limit`, opaque `cursor` | `{items[], next_cursor}` |
| `DELETE` | `/conversations/{id}` | — | `204` |
| `GET` | `/documents/{number}/html` | Encoded public number | Sanitized `text/html` |
| `GET` | `/legal/timeline` | `document_number`, `as_of` | Public timeline |
| `POST` | `/eligibility/checklist` | `{topic, facts, conversation_id?}` | Missing facts và next question |
| `POST` | `/calculator/bhyt` | Decimal inputs + provenance | Formula, result, units, provenance |
| `POST` | `/calculator/bhyt/scenarios` | Tối đa 8 calculation | `{results[]}` |
| `GET` | `/status` | — | `{status, agent}` |

Citation chỉ gồm title, số hiệu công khai, section, quote, source URL và thời điểm
kiểm tra. Không trả internal ID, score, chunk marker hay graph label.

`/health` chỉ trả liveness, `/ready` trả readiness dependency tối thiểu và
`/metrics` phục vụ Prometheus; ba endpoint không nằm trong UI flow. Status `401`
đại diện token thiếu/sai, `403` owner không hợp lệ, `404` resource không tồn tại,
`409` idempotency conflict, `422` payload sai, `429` quota, `502` provider, `503`
dependency và `504` deadline. Mọi status đều dùng error envelope ổn định.

SSE có `Content-Type: text/event-stream`, `Cache-Control: no-cache, no-transform`,
`X-Accel-Buffering: no`, `id` tăng dần và JSON `data` hợp lệ. Server phát status
ngay sau validate, chỉ phát `final` sau evidence/output guard, rồi phát `done`.
HTTP error hoặc HTML body không được coi là SSE event. Web chỉ cần một typed
`ApiClient` với `chat`, `streamChat`, `listConversations`, `getTurns`,
`deleteConversation`, `getDocumentHtml`, `getTimeline`, `checkEligibility` và
`calculate`.

### 5.3 Route budget

Router dùng feature rẻ và query shape:

- greeting/casual: trả lời ngắn, không truy vấn pháp luật;
- identifier/legal-unit: exact và lexical;
- topical: lexical + Qdrant;
- temporal/relational: direct retrieval + Neo4j expansion giới hạn;
- table/numeric: table cell + calculator;
- thiếu fact: hỏi đúng field làm thay đổi outcome.

Mỗi route có `retrieval_deadline`, `generation_deadline`, top-k, max relation hop,
context token budget và verifier policy. Hết deadline thì bỏ stage tốn thời gian,
giữ evidence đã kiểm chứng và ghi metric.

Route budget không được dùng để che lỗi chất lượng: mọi lần bỏ stage phải ghi
`fallback_reason` và stage latency. Release recall rỗng, citation không thuộc
authority hiện hành hoặc Qdrant locator không khớp là trạng thái cần alert; chỉ
được abstain hoặc trả evidence đã hydrate, không dùng model để lấp khoảng trống.

### 5.4 Retrieval và rerank

Lexical và dense chạy song song trong một deadline. Candidate được chuẩn hóa về:

```text
candidate_id, passage_id, document_id, release_id,
source_span, channel, rank, score, text_hash
```

RRF chỉ dùng sau khi normalize rank; không cộng score thô khác thang. Dedupe theo
text hash và document diversity trước cross-encoder/late-interaction rerank. Chỉ
6–10 evidence blocks compact đi vào synthesis; mỗi block giữ title, provision,
effective interval và source span. Graph seed luôn quay lại lexical/dense trong
document đích.

### 5.4.1 GraphRAG execution contract

Neo4j là một nhánh GraphRAG production, không phải một database bị để không:

```text
exact/lexical/Qdrant seed (release-scoped document IDs)
        -> Neo4j approved edges, depth 1 (temporal <= 2), bounded fan-out
        -> target document IDs
        -> PostgreSQL passage re-retrieval (+ Qdrant khi cần)
        -> fuse/rerank/currentness/claim guard
```

Chỉ route `relational` và `temporal` tự động mở nhánh graph; `topical`, `exact`
và `table` không trả thêm một remote hop nếu benchmark không chứng minh lợi ích.
Graph text không phải citation. Mỗi target phải hydrate lại passage canonical cùng
`dataset_id`, content hash và source span. Typed graph walk chỉ đọc cạnh
`review_status=accepted`; khi projection chưa có fact đã review thì nhánh này
trả rỗng và route direct vẫn hoạt động.

Neo4j timeout, parity mismatch hoặc outage kích hoạt circuit breaker và direct
lexical+dense fallback trong cùng route deadline. Nếu quan hệ là điều kiện bắt
buộc để kết luận, renderer trả uncertainty/abstain có lý do thay vì suy diễn từ
graph topology. Các metric `neo4j_expand_ms`, `graph_candidates` và
`fallback_reason` được ghi với label bounded, không chứa query hay UID.

### 5.5 Grounding guard

Guard kiểm tra từng claim về subject, điều kiện, tỷ lệ, ngoại lệ, ngày hiệu lực,
jurisdiction, authority và citation span. Claim thiếu evidence bị loại hoặc hạ
thành uncertainty cụ thể. Answer không chứa DB ID, rank, raw score, chunk marker,
tool trace hoặc secret.

## 6. Feature contracts

### Calculator và bảng

`table_cells` là canonical. Parser giữ merged-cell/header context; calculator dùng
`Decimal`, formula ID, input/unit, rounding và provenance. Giá trị tính toán không
được lấy từ memory, graph text hoặc LLM output. Thiếu input vật chất thì trả form
clarification thay vì đoán.

### HTML viewer

API resolve public document/unit locator tới canonical HTML, sanitize server-side,
thêm anchor ổn định và CSP. UI deep-link tới điều/khoản/bảng; response không hiện
internal database identifier.

### Timeline và checklist

Timeline trả trạng thái tại ngày hỏi và chuỗi sửa đổi/thay thế; conflict hoặc thiếu
effective date là uncertainty. Checklist lưu facts theo Firebase UID và chỉ tạo
điều kiện xác định, không thay legal answer.

### Conversation context

PostgreSQL giữ toàn bộ turns. Valkey giữ bounded summary/recent turns/navigation
facts với key:

```text
medipay:conversation-context:<sha256(v3,uid,conversation,release,prompt-version)>
```

Cache có giới hạn turn/token/TTL, single-flight, encryption in transit và explicit
invalidation khi logout, xóa conversation, release hoặc prompt đổi. Context không
được dùng để thay evidence mới hoặc chia giữa UID.

## 7. Idempotency, concurrency và rate limit

Idempotency record nằm trong PostgreSQL, unique theo `uid + endpoint + key`
(conversation ID nằm trong request hash). Payload khác hash trả `409`; request đang chạy replay cùng
`request_id`; request hoàn tất trả lại turn canonical. Cleanup TTL chạy ngoài request.

API khởi đầu một Uvicorn worker và bounded async semaphore. Pool PostgreSQL, Qdrant,
Neo4j, Redis và model provider có limit riêng; retrieval độc lập chạy song song
trong shared deadline. Không tăng worker hoặc pool khi chưa có load result.

Nginx chặn burst/IP thô. Redis sliding-window giới hạn theo UID/IP/endpoint/cost
quota. Response `429` luôn có `Retry-After`. Redis lỗi thì đọc context từ
PostgreSQL, route chi phí cao fail closed và phát alarm.

## 8. Observability

Đích kiến trúc là OpenTelemetry/OpenInference làm trace contract duy nhất. Runtime
đã khởi tạo SDK OTel với batch exporter, bounded queue, sampling, redaction và
fail-open trong `src/main.py`; Langfuse adapter vẫn ghi stage spans. Trước
production gate phải xác minh collector thật, W3C trace continuity và semantic
attributes OpenInference. Sau khi xác minh, mỗi request dùng W3C trace context và
stage span cho auth, route, SQL, Qdrant, Neo4j, Redis, rerank, model, guard và SSE.
Thuộc tính OpenInference tối thiểu gồm `route`, `model_version`, `prompt_version`,
`release_id`, `stage`, `outcome`, token/cost và fallback; không dùng UID, query,
document hoặc nội dung riêng tư làm label. SDK batch async tới Langfuse với sampling,
timeout, redaction và bounded queue; Langfuse không thể làm chậm hoặc làm hỏng answer.

Prometheus scrape `/metrics`; Grafana đọc dashboard:

1. API traffic/error/latency/TTFT/SSE completion;
2. retrieval channel, candidate count, rerank, guard và fallback;
3. DB/provider pool, timeout, circuit và queue time;
4. cache hit/eviction, idempotency conflict, rate reject;
5. accuracy proxy: unsupported claim, citation failure, malformed stream;
6. host CPU/RAM/disk/container restart.

Label chỉ dùng `route`, `stage`, `outcome`, `model_version`, `release_id` đã hash.
Log JSON redact Authorization, cookie, private prompt/evidence và service account.
Retention metric/log được giới hạn để không làm đầy disk.

Mọi prompt/model/release change tạo một evaluation run mới. Không thay đổi prompt
version đang phục vụ production; rollback quay về version đã được kiểm chứng.

## 9. Deploy, migration và recovery

GitHub Actions chạy lint/type/unit/contract, Gitleaks, Trivy, SBOM và Promptfoo
regression/red-team trong job tách biệt; build image một lần và publish digest lên
GHCR. OIDC cấp quyền ngắn hạn cho SSM; command trên EC2 pull đúng digest, chạy
migration one-shot, kiểm `/health` và `/ready`, rồi restart Compose. Static web được
build thành artifact có hash và Nginx switch atomically.

`ops/ansible/` bootstrap host idempotent (Docker, Compose, Nginx, Valkey,
Prometheus, Grafana, user và permission). Ansible không chạy khi xử lý request và
không thay migration runner.

Migration authority là `database/postgres/migrations/runner.py`:

1. kiểm tra tên/version và checksum;
2. giữ advisory lock;
3. chạy từng SQL migration trong transaction;
4. ghi `schema_migrations`;
5. dừng fail-closed khi checksum đã áp dụng bị thay đổi.

Không chạy `create_all` hoặc DDL từ API. Rollback dùng image digest trước đó và
forward-compatible migration; restore drill phải kiểm chứng release pointer,
Qdrant alias, Neo4j release và conversation integrity.

### Production promotion gate

Promotion chỉ được phép khi `verify_promotion_gate.py` trả đủ ba điều kiện:

1. implementation gate pass và contract tests pass;
2. dependency/host evidence có timestamp (DB parity, Qdrant locator, OTel,
   TLS, readiness, security scan);
3. quality report độc lập đạt accuracy/citation/latency/cost và có rollback +
   restore drill.

Nếu thiếu bất kỳ artifact nào, deploy có thể chạy ở môi trường staging nhưng
không được đổi DNS/active release thành production. Readiness false của Neo4j
hoặc projection locator không khớp phải hiển thị cảnh báo và kích hoạt fallback,
không được che bằng biến môi trường.

## 10. Bảo mật và chi phí

- EC2 disk mã hóa, patch tự động, firewall chỉ mở cổng cần thiết và Docker socket
  không public.
- `.env` trên host mode `0600`; secret rotation có runbook và không ghi giá trị ra
  output CI.
- Firebase client config là public build config; Firebase Admin chỉ ở backend env.
- CORS chỉ allowlist custom web domain; SSE kiểm tra origin/auth/content type.
- Nginx cache hashed public asset/document release; không cache answer cá nhân.
- Valkey memory/eviction/TTL có giới hạn; PostgreSQL/Qdrant/Neo4j backup có checksum.
- EC2 Graviton và Compose dùng một host để tránh phí control-plane; theo dõi CPU,
  RAM, disk, egress và model token theo environment.
- Eval/embedding offline dùng Batch API của provider khi được hỗ trợ; giới hạn
  concurrency, TTL output và lưu manifest/checksum, tuyệt đối không dùng batch cho
  chat tương tác.
- Langfuse trace success được sample; error và latency outlier giữ đủ để điều tra.
- Không lưu full private chat trong metric label hoặc trace mặc định.

## 11. Boundary DDD

```text
src/api/             HTTP, auth, SSE, serialization
src/application/     use case, route budget, deadline orchestration
src/domain/          legal evidence, calculator, timeline, checklist contracts
src/db/              Postgres repositories/session/models; provider adapters ở integrations
src/integrations/    Firebase, model, OTel/Langfuse clients
src/services/        retrieval, rerank, guard, context, product services
```

Domain không import framework/provider. Repository không sinh answer. Agent node
không giữ secret hoặc global mutable state. `chat.py` là facade mỏng gọi application
use case.

## 12. Cây runtime mục tiêu

```text
src/
  api/ application/ domain/ db/ integrations/ services/ agents/
database/
  postgres/migrations/  qdrant/  neo4j/
ops/
  monitoring/ runbooks/ nginx/ compose/ ansible/ security/
web/
eval/ tests/ (RAGAS + Promptfoo offline artifacts)
Dockerfile  Dockerfile.migrate  docker-compose.yml  Makefile
```

Chỉ giữ một runtime API, một migration runner, một requirements runtime và một
đường deploy. Promptfoo/RAGAS, Batch runner, security scanner và Ansible chỉ là
tooling ngoài request path. Generated artifact, snapshot trùng, worker/pipeline
không được gọi và cấu hình deploy cũ bị loại khỏi production sau backup/restore
drill.

## 13. Vùng không thuộc release này

Kubernetes/Helm, APISIX, Jenkins, Celery/BullMQ/RabbitMQ, Jaeger/ELK/Loki, S3,
model gateway, community/global GraphRAG và corpus extraction mới không nằm trong
runtime hoặc deployment hiện tại. **Bounded Neo4j GraphRAG ở mục 5.4.1 vẫn là
thành phần runtime hiện hành.** Những thành phần bị loại chỉ được đánh giá lại qua
ADR mới khi số liệu tải, độ chính xác hoặc chi phí chứng minh kiến trúc một host
không còn đạt cổng ở PLAN.md.
