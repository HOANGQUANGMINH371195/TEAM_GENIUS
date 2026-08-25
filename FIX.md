# FIX — Accuracy, latency và production readiness

> Audit baseline: 2026-08-22 UTC
> Phạm vi: develop; main luôn bất biến.
> Mục tiêu: sửa nguyên nhân gốc theo thứ tự **độ chính xác → độ trễ → deploy**, không coi đổi model hoặc mở context window là bản sửa lỗi.

Đây là runbook thực thi sau audit trong PLAN.md. Mục chưa có artifact đạt gate
không được đánh dấu hoàn thành, dù đã có code tương ứng trong working tree.

### Implementation update — 2026-08-22 UTC (working tree, uncommitted)

### Live repair update — 2026-08-22 UTC

### Hybrid retrieval quality update — 2026-08-24 UTC (working tree, uncommitted)

- **P0-BM25-HYBRID:** **IMPLEMENTED / STAGING-VERIFIED** — Qdrant release
  `medical_legal_hybrid_snapshot-c439751724ab7f10` has named `dense` and
  sparse `bm25` vectors, cloud-side BM25 inference and RRF. Release parity
  passed at 14,393 points. The production alias is intentionally unchanged
  until the answer-quality gate passes.
- **P0-DOMAIN-RANKING:** **IMPLEMENTED / REGRESSION-VERIFIED** — canonical
  document categories now reach the reranker. Explicit BHYT questions demote
  `vien_phi` material; a regression protects against the historical
  09/BYT-TT false citation in a cross-route BHYT question.
- **P0-OPERATIVE-SCOPE:** **PARTIAL / LIVE-VERIFIED** — an operational query
  can retain a bounded same-document chain and follow a canonical legal
  reference. This recovered Law 51/2024 Article 22(4)(h) and NĐ 188/2025's
  50% student-support clause in live diagnostics. It is not yet a complete
  answer-quality gate: concurrent seven-case smoke remains flaky for
  cross-route phrasing and corpus coverage is incomplete for transfer-paper
  duration and cosmetic-service exclusion queries.
- **Current regression evidence:** `ruff` is clean; backend/eval suite is
  **141 passed, 1 expected source-artifact skip**. This is code regression
  evidence only, not proof of full legal accuracy or a release cutover.

- **P0-QDRANT-INDEX:** **REPAIRED / VERIFIED** — active collection
  `medical_legal_active` now has required payload indexes, including
  `legal_status_current`; the adapter also falls back safely for legacy points
  that have the index but no backfilled boolean value.
- **P0-NEO4J-SERVING:** **REPAIRED / VERIFIED** — release
  `snapshot-c439751724ab7f10` was backfilled with `answer_ready`, source span,
  official URL, checked-at and release-fingerprint properties. New imports write
  the same contract. The repair was additive and did not delete graph data.
- **P0-RETRIEVAL-SCOPE:** **FIXED / REGRESSION-VERIFIED** — lookup retrieval no
  longer returns `semantic_scope` unit IDs as a replacement for verified Qdrant
  chunks; both evidence paths are merged before provenance verification.
- **P0-BENCH-METADATA:** **IMPLEMENTED** — benchmark records provider metadata
  when the LangChain response exposes it and reports observed/complete counts.
- **Post-fix live smoke:** 12-case stratified run had 7 PASS, 0 quality or
  infrastructure failures, 3 fixture-unservable and 2 fixture-invalid cases;
  exact cases in the smoke were 2/2 PASS. This is a smoke result, not a full
  636-case accuracy gate.

- `P0-EXACT`: **PARTIAL / IMPLEMENTED_UNVERIFIED** — canonical legacy parser,
  exact candidate ambiguity/answer-ready gate, lexical/Qdrant/hydration/graph
  scope và batch-override filtering đã có regression tests.
- `P0-ANSWER`: **PARTIAL / IMPLEMENTED_UNVERIFIED** — fallback copy abstain,
  sentence-index audit, abstention-template guard và public answer sanitizer đã
  có; chưa có production benchmark/human citation report.
- `P0-GRAPH`: **PARTIAL / IMPLEMENTED_UNVERIFIED** — relation DTO có scope/hash/
  span/status, expansion hai chiều deterministic; live graph tuple parity và
  canonical edge hash chưa được kiểm chứng.
- `P0-HASH`: **OPEN** — readiness hiện fail-closed theo PostgreSQL semantic hash;
  chưa có live parity 14.393/14.393 và Qdrant locator đồng nhất.
- `P0-GOLD`: **PARTIAL** — builder/generator giữ gold signature/relation/scope/
  status/servable và detached hash sidecar; snapshot đã tái tạo từ canonical
  release source, nhưng graph direction/span vẫn unservable và human
  adjudication chưa có. Snapshot hiện có 636 cases; sidecar SHA256 là
  `0f31dfd232e0ca4d9ed7cd8dc7b928a1010a7ad7b3120e8d785b6d5668ffaaa2`.
- Regression evidence: `uv run pytest -q` → **108 passed** (30 dependency
  deprecation warnings); `uv run ruff check ...` → **pass**. Đây không phải
  bằng chứng live accuracy hay deploy readiness.
- `P0-CURRENTNESS`: **PARTIAL / IMPLEMENTED_UNVERIFIED** — SQL/Qdrant đều nhận
  nhãn trạng thái dài (`Còn hiệu lực và ...`), importer ghi thêm cờ chuẩn hóa;
  release pointer được đọc trước cache lookup và cắt cache khi cutover.
- `P0-PII-OBSERVABILITY`: **PARTIAL / IMPLEMENTED_UNVERIFIED** — input/output
  Langfuse chỉ hash+length mặc định; callback có capture plaintext chỉ bật rõ
  bằng `LANGFUSE_CAPTURE_CONTENT=true`.

Trạng thái hợp lệ của một mục là `OPEN → UNVERIFIED → PARTIAL → DONE`, hoặc
`BLOCKED`, `STOP`, `NOT_OBSERVABLE`, `FIXTURE_UNSERVABLE`, `MODEL_MISMATCH`,
`SCHEMA_INVALID`, `RELEASE_MISMATCH`. Mỗi trạng thái luôn kèm timestamp, owner,
dependency/rollback, commit/model/release fingerprint và URI+SHA+retention của
artifact. `BLOCKED`, `STOP`, `NOT_OBSERVABLE` và `FIXTURE_UNSERVABLE` không được
tính vào denominator hay báo cáo là pass.

## 0. Luật bắt buộc

- Mọi thay đổi chỉ thực hiện trên `develop`; không force-push, merge hoặc
  sửa main.
- Mỗi vòng phải ghi commit SHA, dirty-tree status, release ID/fingerprint,
  dataset hash, dependency lock hash, prompt/config hash và provider model metadata.
- Không tắt hash/provenance guard để tăng recall. Dữ liệu không xác minh được phải
  được sửa contract hoặc trả lời abstain.
- Khi query có số hiệu rõ ràng, không được in excerpt từ văn bản khác để che miss.
- Không gọi benchmark là accuracy nếu gold, denominator hoặc model provider chưa
  được xác minh.
- **Subagent accuracy bắt buộc:** mỗi lần chạy lại benchmark, Codex phải tự tạo
  một subagent nhỏ ở chế độ read-only để kiểm tra độc lập fixture, denominator,
  exact/graph recall, evidence hash/span, claim entailment, fallback, abstention,
  provider-call count, model/usage/retry/error và p50/p95/queue/cache. Nếu subagent
  không trả report thì run là NOT_OBSERVABLE và không được coi là pass.

## 1. Đóng băng baseline trước khi sửa

### Bằng chứng baseline

- Super benchmark cũ có 656 case, báo cáo 185/656 (28,2%); đây là diagnostic
  source-selection, chưa phải factual accuracy.
- Exact/deep chỉ đạt 8/200 ở gate kết hợp; multi-hop temporal chỉ 16/200.
- Run provider-backed trước đó có service p50 khoảng 4,6 giây và p95 khoảng
  10 giây ở concurrency 6; hàng trăm giây trong report cũ là queue wait, không
  phải model service latency.
- Run 36 case completion gần nhất ghi agent gpt-4.1-mini/evaluator gpt-4o-mini;
  đó chỉ là historical smoke, không phải bằng chứng GPT-5.6 Luna.
- Các gate exact/graph 100/100 trong evaluator release gọi SQL/raw projection
  trực tiếp, không chạy online planner, hydration, verifier hay answer synthesis.
- PLAN.md tự ghi production readiness 40–45% và còn các gate human, rollback,
  staging, key rotation chưa mở.
- Working tree có thay đổi chưa commit và eval files chưa track; HEAD hiện tại
  là 442030b.
- parity artifact ghi 14.393 vector nhưng đồng thời ghi
  missing_semantic_embeddings=14393; locator registry và physical Qdrant chưa
  được chứng minh là một contract.
- Runtime hiện pack khoảng 8 evidence block, max context mặc định 12k token và
  max output 900 token; 1M context của model chưa được dùng trong production path.
- Live DB probe phải được chạy lại từ môi trường có DNS/credential; lỗi hạ tầng
  không được chấm thành lỗi model.

### Checklist P0.0

- [ ] Chụp git status, git SHA, diff/untracked manifest và giữ trong artifact audit.
- [ ] Hash requirements locks, prompts, config, runner, corpus manifest và image
      metadata; không lưu secret values.
- [ ] Ghi response model ID, usage, request ID, finish reason, retries và provider
      error; không chỉ ghi MODEL_NAME từ env.
- [ ] Nếu provider không trả response model/usage/request ID hoặc model khác Luna
      so với manifest thì đánh dấu MODEL_MISMATCH/NOT_OBSERVABLE và dừng run.
- [ ] Ghi active dataset, release fingerprint, PostgreSQL/Qdrant/Neo4j locator,
      point/node/edge counts và serving status trong cùng manifest.
- [ ] Đối chiếu mọi mục Resolved trong PLAN.md với artifact có timestamp; nếu
  mâu thuẫn thì chuyển về OPEN.
- [ ] Tách projection/data gate khỏi online-agent gate trong mọi report; parity
      pass không được suy ra là answer accuracy.
- [ ] Lưu một redacted manifest được track hoặc một CI artifact có URI/retention;
      không dựa vào các file bị .gitignore mà người khác không thể tái lập.

**Exit gate:** có manifest bất biến gắn git + release + lock + model metadata.
Thiếu manifest thì dừng mọi so sánh accuracy và deploy.

## 2. Sửa benchmark/gold contract trước runtime

### 2.1. Khôi phục gold fields

- [ ] Mỗi case giữ expected_signature, issuer, jurisdiction, title/date
      disambiguator, accepted document set, answer_ready, external/reference-only,
      servable, expected abstention, corpus as-of date, status-check cutoff và
      policy version.
- [ ] Exact tách identity_pass khỏi answer content; không bắt câu tổng hợp lặp
      nguyên số hiệu.
- [ ] Graph giữ relation ID, type, direction, scope (toàn bộ/một phần),
      serving_status, evidence span/hash và official URL.
- [ ] Thematic/semantic giữ expected facts, numbers, units, điều kiện/ngoại lệ
      và accepted source set; không ép đúng một document ID khi nguồn tương đương.
- [ ] Case external, answer_ready=false, suppressed hoặc navigation-only phải
      có fixture_unservable và bị tách khỏi agent denominator.
- [ ] Snapshot validator phải fail nếu mất signature, scope, relation, hash,
      answerability hoặc provenance; test hiện tại chỉ kiểm count/ID là chưa đủ.
- [ ] Validator phải fail nếu expected_signature/expected facts rỗng ở case cần
      identity/content; phải ghi agent_path=production-agent/API cho mọi case
      dùng để chấm answer.
- [ ] Builder phải verify hash của input release/semantic source trước khi tạo
      snapshot và ghi final dataset hash vào manifest.

### 2.2. Tách gate chấm

- [ ] retrieval_identity: exact ID/accepted set, Recall@1/5/20, MRR.
- [ ] evidence_integrity: release, source span, text hash, input hash.
- [ ] graph_relation: source→target, direction, type, scope, edge evidence.
- [ ] answer_factuality: facts, số, ngày, điều kiện và status polarity.
- [ ] abstention: câu không có nguồn phải abstain; expected_documents=[] không
      tự động là pass.
- [ ] policy/safety: kiểm forbidden behavior, không chỉ bool(answer).
- [ ] table_numeric: kiểm cell, row, unit, tolerance, phép tính.
- [ ] presentation: internal vocabulary, raw UUID/relationship ID, source-copy.

### 2.3. Protocol rerun

- [ ] Cold accuracy run trong process mới theo từng stratum; tắt retrieval/answer
      cache và reset circuit breaker.
- [ ] Warm-cache run riêng; ghi cache_hit từng case.
- [ ] Randomize bằng seed cố định, không chạy category theo thứ tự có thể làm
      warm cache/circuit state gây nhiễu.
- [ ] Concurrency thấp cho accuracy; load test riêng cho queue/throughput.
- [ ] Chạy tối thiểu 3 lần, báo min/mean/max/variance và flaky case.
- [ ] Với duplicate query, dùng accepted candidate set hoặc process/cache sạch;
      không để retrieval/answer cache của case trước quyết định expected ID.
- [ ] Phân biệt PASS, FAIL_QUALITY, FIXTURE_UNSERVABLE, INFRA_FAILURE,
      TIMEOUT, PROVIDER_ERROR và NOT_OBSERVABLE.
- [ ] Lưu answer đầy đủ có giới hạn hoặc hash+length, raw evidence/relations,
      spans/hashes, stage timing, token usage, retries, request ID và model ID.
- [ ] Human-adjudicated holdout tối thiểu 200–300 câu; thêm temporal/document
      holdout và câu hỏi multi-turn. Snapshot 636 case hiện tại chỉ là release
      regression và còn fixture unservable.

**Exit gate:** không còn false PASS do fallback/non-empty answer; denominator và
gold schema được kiểm chứng; accuracy subagent có report.

## 3. P0 — Exact identifier và hard document scope

### Nguyên nhân

chat.py hiện trích số hiệu nhưng chỉ hard-scope một số metadata/legal-unit path.
Content/scope query có thể rơi vào lexical/semantic search toàn corpus, để
distractor cũ thắng RRF. Regex còn thiếu dạng legacy; duplicate signature và
answer_ready=false tạo thêm case không khả thi.

### Checklist

- [ ] Canonical parser hỗ trợ dạng năm, viết tắt, khoảng trắng, gạch, slash,
      Đ/Ð, TC/QĐ/TCCB, TT/LB, số có chữ cái đầu như 18B/2010 và chuỗi có &.
- [ ] Exact lookup dùng equality trên canonical signature, thêm issuer/jurisdiction/
      date khi có.
- [ ] Candidate duy nhất + answer_ready=true: lexical, semantic, PageIndex,
      hydration và rerank đều bị giới hạn bởi document ID đó.
- [ ] Nhiều candidate: hỏi làm rõ số hiệu/cơ quan/ngày; không chọn ngẫu nhiên,
      không broad-search thay thế.
- [ ] Candidate answer_ready=false: báo nguồn chưa phục vụ hoặc chỉ trả metadata
      đã xác minh; không âm thầm thay bằng văn bản gần nghĩa.
- [ ] Broad retrieval chỉ được phép khi không có explicit identifier hoặc user
      yêu cầu so sánh nhiều văn bản.
- [ ] Hard-scope áp dụng cho lexical, Qdrant, graph seed, hydration và answer cache.
- [ ] Thêm regression cho duplicate, legacy signature, near-identifier,
      content/scope, issuer ambiguity và exact unavailable.

**Acceptance:** serveable exact Recall@1 = 100%; ambiguous phải clarify/abstain;
exact evidence không chứa distractor document.

## 4. P0 — Semantic provenance và projection parity

- [ ] Chọn một authoritative source cho embedding_input_sha256: backfill
      PostgreSQL theo artifact hoặc chứng minh registry artifact là nguồn tin cậy.
- [ ] Kiểm đủ 14.393/14.393: Qdrant payload = canonical row = manifest.
- [ ] Đồng nhất physical collection, alias, release locator và fingerprint;
      readiness phải kiểm đúng physical projection, không chỉ count.
- [ ] Ghi metric vector received, hydrated, dropped-missing-hash,
      dropped-mismatch-hash và accepted-after-RRF.
- [ ] Readiness phải fail hoặc báo degraded rõ ràng khi accepted-after-hydration
      < 14.393; không chỉ báo Qdrant healthy vì raw point count đúng.
- [ ] Rerun semantic Recall@20 sau hydration/provenance; raw Qdrant recall không
      được dùng làm runtime recall.
- [ ] Giữ fail-closed khi hash sai; không hạ guard để “đạt benchmark”.

**Acceptance:** hash parity 14.393/14.393 và accepted-after-hydration 14.393/14.393
trên release đang phục vụ; zero mixed-release evidence; readiness fail khi thiếu.

## 5. P0 — Graph relation evidence và temporal correctness

### Relation contract

- [ ] Relation payload có public source/target signature, type, direction, scope,
      serving status, evidence text/span/hash, official URL, checked-at và release.
- [ ] Tách canonical_document_text khỏi navigation_reference; external reference
      không bị chấm như document text nếu không có edge evidence.
- [ ] Chỉ approved_evidence được production answer; suppressed edge là
      fixture-unservable hoặc abstention.
- [ ] Gold graph key là ordered tuple source→target→direction→type→scope, không
      chỉ relationship_id; forward/reverse query không được dùng cùng một pass.

### Retrieval và verifier

- [ ] Query có identifier/relation intent seed graph từ exact candidates trước
      top-k RRF.
- [ ] Expansion hai chiều/mixed direction có giới hạn và ORDER BY deterministic
      trước LIMIT; ưu tiên exact predicate/date/type.
- [ ] Verifier chấp nhận approved edge có scope/hash/span cho relation claim;
      claim “còn hiệu lực” vẫn cần official status metadata.
- [ ] Reserve token budget cho relation blocks; không để passage chiếm hết context.
- [ ] Chấm relation ID/type/scope/evidence độc lập canonical target text.
- [ ] Test toàn bộ/một phần, bãi bỏ/thay thế, inbound/outbound, suppressed,
      external reference và nhiều cạnh cùng source.

**Acceptance:** approved relation recall và evidence integrity 100% trên serveable
set; unsupported high-risk relation claims = 0.

## 6. P0 — Currentness, fallback và answer correctness

### Currentness

- [ ] Mặc định ưu tiên nguồn đã xác minh còn áp dụng.
- [ ] Dùng effective date/status/jurisdiction/issuer trong lexical, Qdrant filter,
      rerank và graph ranking.
- [ ] Chỉ ưu tiên văn bản cũ khi query yêu cầu lịch sử/ngày cụ thể/signature cũ.
- [ ] Với thay thế/bãi bỏ, trả timeline ngắn từ nguồn trạng thái và không trộn
      chunks giữa release; precedence phải kết hợp status metadata, approved
      temporal edge và ngày hiệu lực, không chỉ hạ điểm theo năm.
- [ ] Thêm paired cases current-vs-historical, “tại ngày X”, jurisdiction
      collision và superseded source; chấm status/date riêng với text relevance.

### Fallback/guardrail

- [ ] Exact miss/ambiguous: clarify hoặc abstain; không in top-3 RRF excerpt
      không kiểm identifier relevance.
- [ ] Giữ supported claims và loại riêng unsupported claims; không thay toàn answer
      bằng generic caveat.
- [ ] Thay positional sentence/claim zip bằng structured claim ID.
- [ ] Semantic entailment verifier kiểm số, ngày và status polarity; lexical
      overlap chỉ là tín hiệu phụ.
- [ ] Legal-unit extractive chỉ dùng khi user yêu cầu liệt kê; câu tổng hợp phải
      synthesis ngắn, không raw chunk.
- [ ] Cấm UUID/relationship ID và nhãn nội bộ trong public answer; map sang title,
      số hiệu và citation public.
- [ ] Post-render sanitizer kiểm toàn answer, SSE và UI; thêm copy-overlap gate.
- [ ] Public DTO tách khỏi internal audit DTO; không để API/UI lộ claim/span/
      provenance/citation nếu không thuộc public contract.

### Social/policy

- [ ] Social/chitchat trả lời trước embedding/Qdrant/Neo4j/LLM.
- [ ] Test qua production API/agent và trace provider calls = 0; không gọi helper
      trực tiếp trong benchmark.
- [ ] Bổ sung typo, mixed greeting+legal, injection, multilingual và SSE/JSON.
- [ ] Bổ sung out-of-scope holdout (thời tiết, lập trình, câu hỏi ngoài corpus)
      và đo false-positive/false-negative trước khi cho truy hồi pháp lý.

**Acceptance:** high-risk unsupported = 0; human citation precision ≥95%;
claim recall ≥95%; internal vocabulary/raw ID = 0; exact miss không hiện văn bản cũ.

## 7. P1 — Conversation và release isolation

- [ ] Persist dataset_id/release ID với mỗi turn; không để caller ghi NULL.
- [ ] Anchor chỉ lấy citation verified thuộc active release; invalidate khi cutover.
- [ ] Re-retrieve legal claim ở mỗi follow-up; memory chỉ giải quyết đại từ,
      không trở thành evidence.
- [ ] RLS test bằng Firebase UID thực tế, gồm token rotation, cross-user leakage,
      delete/retention và concurrent requests.
- [ ] Browser E2E cho refresh, multi-device, history, SSE cancel và auth failure.
- [ ] Không nuốt lỗi /auth/me thành role=user; auth outage phải observable.

**Acceptance:** multi-turn resolution ≥95% human set; memory unsupported = 0;
cross-user leakage = 0.

## 8. P1 — LangGraph planner và context profile

- [ ] Giữ fast path tuyến tính cho social, policy, exact metadata và simple lookup.
- [ ] Typed query plan cho exact/content, thematic, temporal, relation, table,
      abstention; mọi subquery giữ identifier anchor.
- [ ] Decomposition phải copy anchor số hiệu/cơ quan/ngày vào từng subquery; nếu
      không giữ được anchor thì không split.
- [ ] Tối đa một repair/retrieval retry có deadline, reason và max-step guard.
- [ ] Tách answer plan, relation validation và final synthesis bằng schema; không
      dựa vào “thinking” text tự do.
- [ ] Tạo standard/deep profile; deep chỉ bật cho multi-document/long-form với
      cost/latency/cancel gate.
- [ ] Validate model capability/context window và output budget; config hiện chỉ
      pack mặc định khoảng 12k token và output 900 token, nên không được tuyên bố
      đã tận dụng 1M context khi chưa có deep-profile artifact.
- [ ] Context packer reserve exact source, relation evidence và instruction;
      không gửi toàn corpus mặc định.
- [ ] Chỉ thêm reranker/PPR/community/global sau ablation cùng gold và không làm
      p95/safety/exact regression.

## 9. P1 — Tối ưu tốc độ sau correctness

### Đo trước

- [ ] Tách TTFT, retrieval, embedding, lexical, Qdrant, Neo4j, hydration,
      generation, guardrail, queue wait và total service time.
- [ ] Ghi token in/out, cache hit, retry, circuit state, provider status/model.
- [ ] Chạy cold, warm và load riêng; không trộn queue latency với service latency.

### Tối ưu theo ROI

- [ ] Social/policy/exact metadata: zero provider call.
- [ ] Exact hard-scope: bỏ embedding/graph khi không cần.
- [ ] Chạy lexical DB session ngắn song song embedding/Qdrant khi không hard-scope.
- [ ] Cache key gồm release fingerprint, prompt/config hash; benchmark có cache-off.
- [ ] Invalidate active-release cache theo pointer generation/fingerprint ngay khi
      cutover/rollback; không chấp nhận cửa sổ stale.
- [ ] Giảm candidate trước rerank; deduplicate document/unit; giới hạn graph.
- [ ] Pack theo legal-unit boundary, token budget và reserved relation tokens.
- [ ] Chỉ tăng output/context khi chứng minh truncation; không dùng 1M token mặc định.
- [ ] Đo Luna với cấu hình khóa và response metadata thật; model ablation phải cùng
      prompt/gold/release.
- [ ] Batch embedding/subquery khi bảo toàn ordering; final answer chấm từng request.

**Speed gates:** TTFT p95 < 2,5 giây, full concise answer p95 < 8 giây; exact/
policy p95 mục tiêu < 2 giây; provider error/timeout tách khỏi quality fail.

## 10. P1 — Database/pipeline cleanup

- [ ] Một migration/DDL authority; API không chạy DDL.
- [ ] Thống nhất ops.active_release với offline/runtime tool; xử lý dataset_state
      join mơ hồ và status enum/check không nhất quán.
- [ ] Xóa/đánh dấu duplicate DDL trong pipeline storage; status verified phải
      khớp CHECK constraint/migration head và có test transition.
- [ ] Sửa conversation persistence để mọi turn ghi dataset_id/release; kiểm
      bootstrap/RLS không cast Firebase UID tùy ý sang UUID.
- [ ] Di chuyển/đánh dấu pgvector caller, duplicate DDL, sys.path hack và
      compatibility shim khỏi production path.
- [ ] Đưa generated corpus/vector/backup ra ngoài source tree/Docker context.
- [ ] Sửa README/script stale (embed_snapshot, model cũ, pgvector cũ,
      database/firebase scaffold); mỗi DB có README DEV/AI và có source-lock
      manifest cho outsource (URL/commit/license/SHA, không vendor code).
- [ ] Chứng minh active/previous rollback ở managed environment, không chỉ local.
- [ ] Cập nhật README API schema, admin labels/mock data và docs model cũ; docs
      drift phải là CI failure khi hướng dẫn command/model không tồn tại.

## 11. P1 — CI, Docker, Render/Vercel và secrets

- [ ] Sửa CI assertion user image khớp Dockerfile; build runtime/migrate/web/
      compose từ empty volumes.
- [ ] Pin base image/action/web image bằng digest; kiểm forbidden path, secret,
      SBOM và non-root.
- [ ] Chốt branch deploy đúng artifact; không deploy dirty `develop` hoặc release stale.
- [ ] Tách staging/preview khỏi production; deploy script phải có commit check,
      approval, smoke và rollback gate.
- [ ] Render staging: auth, readiness, SSE, load, restart, migration và rollback.
- [ ] Vercel preview→staging browser E2E, Firebase domains, CSP narrow, không
      Admin secret trong bundle.
- [ ] Revoke Firebase Admin key đã lộ, tạo key mới và cài qua secret manager;
      kiểm key cũ không còn hiệu lực.
- [ ] Xử lý secret incident cho mọi provider key từng nằm trong .env/log/history
      (bao gồm OpenAI nếu đã lộ): revoke/rotate, kiểm key cũ bị từ chối, rà shell
      history/Langfuse/build log; tuyệt đối không đưa value vào artifact.
- [ ] Metrics/Langfuse có PII redaction, retention, external scrape/alert,
      aggregatable histogram, quality/latency/cost/provider dashboard.
- [ ] Đếm cả 413/429/rejection trước middleware metrics; trace phải có owner,
      retention và redaction policy.
- [ ] Rate/cost limit phải dùng verified UID sau auth hoặc có pre-auth IP bucket
      + post-auth UID bucket; test token refresh không bypass quota.

**Deploy gate:** Không public production khi key rotation, managed rollback,
staging smoke, CI/Docker contract hoặc external browser/load evidence còn OPEN.

## 12. Definition of Done

| Gate | Mục tiêu |
|---|---:|
| Serveable exact identity Recall@1 | 100% |
| Exact content/scope không distractor | 100% |
| Semantic input-hash parity | 14.393/14.393 |
| Approved graph ID/type/scope/evidence | 100% |
| Thematic Recall@20 | ≥88% baseline, target ≥92% |
| Human citation precision | ≥95% |
| Claim-level citation recall | ≥95% |
| Unsupported high-risk claim | 0 |
| Abstention precision/recall | đạt rubric human, không bool gate |
| Table numeric/source-cell validation | pass |
| Internal vocabulary/raw ID trong answer | 0 |
| Social provider calls | 0 |
| TTFT p95 | <2,5 giây |
| Full answer p95 | <8 giây |
| Multi-turn release isolation | ≥95% |
| Cross-user leakage | 0 |
| Docker clean build/restore/rollback | pass |
| Render staging + Vercel browser smoke | pass |
| Firebase key cũ | revoked/0 |

## 13. Execution ledger và rollback

| ID | Chủ trì | Phụ thuộc | Artifact bắt buộc | Dừng/rollback khi |
|---|---|---|---|---|
| P0-GOLD | Eval/data | Baseline manifest | gold schema + servable denominator + hash | mất field, duplicate ambiguity chưa phân loại |
| P0-EXACT | Retrieval/DB | P0-GOLD | exact route report + Recall@1 + distractor diff | wrong-document high-risk > 0 |
| P0-HASH | Data/Qdrant | P0-GOLD | 14.393 hash parity + accepted-hit recall | hash mismatch/mixed release |
| P0-GRAPH | Graph/retrieval | P0-GOLD, P0-HASH | edge tuple/scope/span report | suppressed edge bị chấm như approved |
| P0-ANSWER | Agent/UX | P0-EXACT/P0-GRAPH | claim/fallback/vocabulary report | unsupported high-risk hoặc raw ID > 0 |
| P1-SPEED | Runtime/ops | P0 accuracy gates | cold/warm/load latency report | p95 tăng >10%, accuracy giảm >2 điểm % hoặc error >1% |
| P1-DEPLOY | Platform/security | P0/P1 + key rotation | staging, rollback, browser/SSE, secret scan | bất kỳ external gate nào OPEN |

Mỗi hàng phải có owner cụ thể trong issue/PR, một baseline artifact, một target
artifact và một rollback plan. Không gộp nhiều thay đổi vào một ablation; promote
chỉ khi không làm giảm safety/exact/human holdout trong ba lần chạy.

## 14. Thứ tự thực thi

1. Đóng băng manifest/commit/model/env; xác minh DB parity.
2. Sửa gold schema, fixture denominator và runner; tạo human holdout.
3. Sửa exact parser, hard scope, ambiguity và cấm distractor fallback.
4. Sửa semantic hash/locator và đo accepted-hit recall.
5. Sửa graph relation evidence/verifier/deterministic expansion.
6. Thêm currentness, structured claims, sanitizer và conversation isolation.
7. Accuracy rerun: cache-off, process mới, 3 repeats, accuracy subagent.
8. Chỉ khi accuracy gate đạt mới tối ưu latency/cache/context/model.
9. Load/cold/warm benchmark, kiểm TTFT/full p95/variance.
10. Dọn database/pipeline/docs, sửa CI/Docker, rotate secrets.
11. Render/Vercel staging, browser/SSE/load/restore/rollback rehearsal.
12. Canary có giám sát; chỉ sau observation window mới gọi production-ready.

## 15. Không được làm để “chữa điểm”

- Không mở context lên 1M trước khi hard-scope, currentness và graph evidence đúng.
- Không đổi model giữa các run để che variance.
- Không giảm threshold/bỏ hash guard để tăng Recall@20.
- Không coi bool(answer), machine-only RAGAS hoặc 36/36 model cũ là legal accuracy.
- Không đưa external/reference-only node vào document-text denominator.
- Không dùng fallback excerpt để che retrieval miss.
- Không deploy thẳng production bằng script thiếu staging/approval/rollback.

## 16. Artifact bắt buộc sau mỗi vòng

- git SHA, dirty-tree status, lock/prompt/config hashes.
- release manifest, projection locator/fingerprint, parity/hash report.
- dataset hash, gold schema/servable-denominator report, seed/concurrency/cache mode.
- per-case answer/evidence/relation/error taxonomy và latency breakdown.
- human adjudication report và accuracy subagent report.
- regression diff: exact, graph, thematic, abstention, policy, vocabulary, TTFT,
  p95, token/cost và provider errors.

Nếu artifact thiếu hoặc mâu thuẫn, trạng thái vòng chạy là
BLOCKED/NOT_OBSERVABLE; không cập nhật PLAN thành Resolved và không deploy public.

## 17. Evidence map để người thực thi truy ngược

- Exact extraction/scope: src/services/chat.py:356-520 và
  src/services/retrieval.py:15-105.
- RRF/ranking: src/services/retrieval.py:219-259.
- Semantic hash rejection: src/services/chat.py:805-826.
- Graph seed/expansion: src/services/chat.py:519-577 và
  src/integrations/neo4j.py:58-109.
- Temporal verifier/fallback/claim audit: src/agents/nodes/graphrag_nodes.py:177-217,
  257-324 và 403-503.
- Context/model limits: src/config.py:33-79,
  src/agents/nodes/graphrag_nodes.py:102-152 và src/services/llm.py:21-28.
- Benchmark scoring/gold loss: eval/run_super_golden_benchmark.py:67-165,
  eval/build_super_golden_dataset.py:28-123 và
  eval/generate_release_locked_suite.py:48-70.
- Projection/hash artifact: eval/results/live-corpus-parity-current.json.
- Plan gates/DoD: PLAN.md:377-419, 1468-1525 và 2762-2828.
- CI/Docker/platform: .github/workflows/ci.yml:62-70,
  Dockerfile:21-33, render.yaml:1-53 và scripts/verify_platform_contract.py:1-73.
- Conversation/auth/observability: src/api/routes.py:100-152,
  src/services/conversation_context.py:27-93 và src/main.py:104-179.
