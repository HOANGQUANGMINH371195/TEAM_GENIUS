# Neo4j knowledge graph

Neo4j là nơi duy nhất lưu knowledge graph: document nodes và các relationship
từ release authority đã qualify. Import phải dùng đúng source archive và release
lock tương ứng; thư mục `data/clean/` trong checkout có thể không chứa đủ source
CSV để tái dựng active release.
Supabase/PostgreSQL chỉ lưu canonical document, alias resolution, chunk và
table; không lưu relationship hoặc reference-only stub. Vector derived được giữ
ở Qdrant, không đưa trở lại PostgreSQL Free-tier.

## Local development

```bash
docker compose --profile local-full up -d neo4j
```

Thiết lập `NEO4J_URI=bolt://localhost:7687`, `NEO4J_USERNAME=neo4j` và
`NEO4J_PASSWORD` trong `.env`. Với Aura dùng `neo4j+s://...` và password Aura.
Import release bằng script pipeline sau khi snapshot canonical đã pass:

```bash
python3 database/neo4j/scripts/import_relationships.py \
  --source-dir data/clean/medical_active_v22_production_hotfix_source --dataset-id <release>
```

Importer tạo `graph_id=<dataset_id>:<document_id>`, giữ nguyên
`relationship_id`, predicate gốc, adverse/provenance flags và alias. Canonical
và alias nodes đồng bộ cả `title` và `so_ky_hieu`; `content_text` vẫn chỉ nằm
ở Supabase và được hydrate theo cùng `dataset_id`. Toàn bộ
release nằm trong một transaction. Trước commit, script đối chiếu node kinds,
edge count theo predicate, ID uniqueness, alias count và cross-release edges;
bất kỳ sai lệch nào cũng rollback.

Không dùng `--replace-existing` trừ khi retry có kiểm soát đúng cùng
`dataset_id`. Mọi Cypher query online phải filter `dataset_id` lấy từ
Supabase `dataset_state`; graph release mới không tự trở thành active chỉ vì đã
import vào Neo4j. Online expansion và endpoint relationships phải bổ sung
`r.serving_status = 'approved_evidence'`; các cạnh audit-only không được dùng
để tạo câu trả lời hay cảnh báo hiệu lực.

Các báo cáo parity cũ chỉ là lịch sử, không phải bằng chứng deploy hiện tại.
Preflight live ngày 2026-08-28 xác nhận Neo4j kết nối được nhưng đang có
1.914 nodes và 197 cạnh `approved_evidence`, trong khi release lock yêu cầu
1.901 và 187. Vì vậy readiness Neo4j vẫn fail. Phải backup, chạy parity report
và chỉ reconcile release được chỉ định; không xóa active release hoặc dùng file
`live_parity.json` cũ để vượt gate. Supabase vẫn giữ text/chunk lexical; Qdrant
đang giữ 14.393 vector 1536 chiều ở collection vật lý theo release.

Typed facts có một bước export riêng để bảo đảm Neo4j chỉ nhận các dòng đã
được reviewer duyệt trong `public.legal_facts`:

```bash
make typed-facts-export \
  RELEASE_ID=snapshot-c439751724ab7f10 \
  FACTS_FILE=/tmp/snapshot-c439751724ab7f10-facts.jsonl
make typed-facts-check \
  RELEASE_ID=snapshot-c439751724ab7f10 \
  FACTS_FILE=/tmp/snapshot-c439751724ab7f10-facts.jsonl
PYTHONPATH=. python database/neo4j/scripts/import_typed_facts.py \
  /tmp/snapshot-c439751724ab7f10-facts.jsonl \
  --release-id snapshot-c439751724ab7f10
```

Export không tự nhận dạng hoặc tự chấp thuận facts; khi chưa có review rows,
file sẽ rỗng và importer không tạo typed graph giả. Importer cũng kiểm tra
predicate với `docs/data/typed-bhyt-ontology.json`, source span và trạng thái
`accepted` trước khi mở transaction Neo4j.

### Removing a stale release safely

When parity identifies an obsolete release, back it up and remove only that
dataset. Never target the retained active release; the command requires an
exact confirmation string and verifies zero remaining nodes:

```bash
PYTHONPATH=. uv run python database/neo4j/scripts/cleanup_stale_release.py \
  --target-dataset snapshot-c94d7b75195a67fa \
  --retain-dataset snapshot-c439751724ab7f10 \
  --confirm 'DELETE snapshot-c94d7b75195a67fa' \
  --backup /secure/path/neo4j-snapshot-c94-backup.json \
  --env-file /absolute/path/to/.env \
  --dry-run
```

Inspect the backup/counts, then rerun without `--dry-run`. This mutation is
isolated to Neo4j; PostgreSQL, Qdrant and the active-release pointer are not
changed.
