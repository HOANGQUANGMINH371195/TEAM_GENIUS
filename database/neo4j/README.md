# Neo4j knowledge graph

Neo4j là nơi duy nhất lưu knowledge graph: document nodes và các relationship
từ release authority đã qualify. Release hiện tại được dựng từ
`data/clean/medical_active_v31_fully_reviewed/relationships.csv`.
Supabase/PostgreSQL chỉ lưu canonical document, alias resolution, chunk và
table; không lưu relationship hoặc reference-only stub. Vector của release
Free-tier hiện tại được giữ ngoài PostgreSQL để chờ chuyển Qdrant.

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

Báo cáo ngày 2026-08-18 từng ghi `snapshot-c439751724ab7f10` parity-pass với
1.901 nodes (682 canonical, 1.211 reference-only, 8 alias), 5.808 legal
relationships và 8 `ALIAS_OF` edges. Ngày 2026-08-27, sau khi chạy đúng
builder `source_commit=1b98f44`, fingerprint/count/hash của PostgreSQL, Qdrant
và Neo4j đã khớp release lock. Snapshot cũ
`snapshot-c94d7b75195a67fa` đã được backup rồi xoá có kiểm soát; active release
được giữ nguyên (1.901 nodes, 5.816 relationships sau cleanup). Backup JSON
được lưu trong `.cache/` và bị git-ignore để điều tra/khôi phục thủ công khi
cần. Không dùng file `live_parity.json` cũ để quyết định deploy; mọi query
graph online vẫn phải lấy `dataset_id` active từ Supabase. Supabase vẫn giữ
text/chunk lexical; 14.393 vector 1536 chiều được offload vào artifact local.

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
