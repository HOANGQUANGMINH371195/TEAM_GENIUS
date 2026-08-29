# Reconciled BHYT / viện phí corpus

`build_active_corpus.py` combines the curated CSV authority files with the two
active JSON exports. It never trusts JSON `content_text`; visible text is built
later from selected HTML by the canonical pipeline. Selected `content_html` is
serialized without whitespace normalization, then both its raw SHA-256 and the
regenerated visible-text SHA-256 are rechecked from the written CSV.

Run from the repository root:

```bash
python3 database/corpus/build_active_corpus.py
```

The default output is `data/clean/medical_active_v2/`. Four damaged/missing
documents are recovered from reviewed HTML sources and checked against
official metadata. Cached fragments make subsequent builds reproducible:

```bash
python3 database/corpus/build_active_corpus.py --offline
```

Important outputs:

- `metadata.csv`, `content.csv`, `relationships.csv`: candidate authority input
  for the canonical pipeline;
- `aliases.csv`: old/duplicate IDs mapped to one legal identity;
- `source_provenance.csv` and `recovery_cache/`: evidence lineage;
- `quality_issues.csv`, `crawl_backlog.csv`, `rejected_records.csv`,
  `reference_nodes.csv`: explicit
  unresolved data rather than silently fabricated values;
- `build_manifest.json`: input and artifact hashes plus release counts.

Do not overwrite `data/raw/` or publish to Supabase until the candidate passes
the canonical build and storage-capacity gates.

## Selective Hugging Face intake (review-only)

`intake_hf_vbpl.py` turns explicitly named documents from
[`tmquan/vbpl-vn`](https://huggingface.co/datasets/tmquan/vbpl-vn) into a
local, immutable review intake. It rejects provincial rows, missing bodies and
non-VBPL URLs. It does **not** index, publish or activate anything: that
dataset has no legal-force field, so each record is initially
`needs_official_status_verification`.

Download only the necessary Parquet shards to a temporary location, then run:

```bash
uv run --with pyarrow python database/corpus/intake_hf_vbpl.py \
  --input /tmp/documents-00013-of-00032.parquet \
  --input /tmp/documents-00015-of-00032.parquet \
  --document-number 51/2024/QH15 \
  --document-number 01/2025/TT-BYT \
  --document-number 188/2025/NĐ-CP \
  --output-dir data/intake/hf-vbpl-bhyt-2026-08-24
```

The resulting `selected_documents.jsonl` and `manifest.json` remain ignored
local data. Review legal force against VBPL, map approved content into the
canonical candidate format, run `validate_candidate.py`, ingest only to a
new staging snapshot, and pass the locked evaluation suite before activation.

### Completeness gate

Before a staging release, compare the candidate with the compact metadata
export from [`th1nhng0/vietnamese-legal-documents`](https://huggingface.co/datasets/th1nhng0/vietnamese-legal-documents):

```bash
uv run --with pyarrow python database/corpus/audit_hf_bhyt_coverage.py \
  --hf-metadata /tmp/hf-vietnamese-legal-metadata.parquet \
  --candidate-dir /absolute/path/to/medical_active_candidate \
  --output /absolute/path/to/medical_active_candidate/HF_BHYT_COVERAGE.json
```

The gate covers only `Trung ương` documents marked `Còn hiệu lực` or `Hết
hiệu lực một phần` and explicitly labelled BHYT by the source. It accepts
benign differences such as `Luật số 51/2024/QH15` versus `51/2024/QH15`, but
fails if any legal identity is absent.

## Free-tier external-vector release

Release production `snapshot-c439751724ab7f10` (2026-08-18) giữ canonical
documents, lexical chunks và provenance trong Supabase, graph trong Neo4j, còn
14.393 embedding được giữ ở artifact local để chuyển sang Qdrant. Cách này giữ
Supabase dưới quota 500 MB mà không bỏ mất vector đã tạo.

Quy trình kiểm soát:

1. `reuse_embedding_backup.py` chỉ tái sử dụng vector khi cả passage ID và
   embedding-input SHA-256 khớp; input mới phải được embed lại.
2. `offload_staging_embeddings.py` chỉ dọn staging vector metadata sau khi
   artifact local khớp dataset ID, row count, dimensions và toàn bộ vector hữu hạn.
3. `verify_live_corpus_parity.py --release-lock docs/data/release-lock-...json
   --pipeline-root <exact-builder>/database/pipeline
   --external-embedding-artifact ...` đối chiếu
   source, Supabase, Neo4j và artifact theo từng passage/edge trước khi báo pass.

`verify_live_corpus_parity.py` cũng kiểm tra `snapshot_id`/fingerprint và các
đếm canonical trước khi đối chiếu database. Không được dùng lại một
`dataset_id` cũ cho thư mục clean đã được rebuild bằng parser/chunker khác:
hãy cung cấp đúng source manifest của release hoặc tạo release mới rồi parity
ở cả PostgreSQL, Qdrant và Neo4j. Báo cáo sẽ ghi rõ `source_snapshot` và vẫn
fail an toàn nếu embedding artifact không khớp, thay vì coi file
`live_parity.json` cũ là bằng chứng hiện tại.

Artifact và candidate nằm dưới `data/clean/` nên không được commit. Không xóa
`data/clean/embeddings-reused/snapshot-c439751724ab7f10/` trước khi import và
verify Qdrant.

## Typed-fact review boundary

Facts extracted by an annotator or an offline model must pass the immutable
review boundary before they can affect Neo4j. `stage_reviewed_facts.py` checks
the release ID, ontology predicate, reviewer decision/note, canonical
document/unit source span and SHA-256, then inserts idempotently into
`public.legal_facts`. A conflicting replay of an existing `fact_id` fails
closed; a same-content replay is counted and skipped. Pending rows may be
staged for review, but only `accepted` rows are exported to Neo4j or exposed
through the active-release read policy.

```bash
make typed-facts-stage \
  RELEASE_ID=snapshot-c439751724ab7f10 \
  FACTS_FILE=/tmp/reviewed-facts.jsonl \
  ENV_FILE=/absolute/path/to/.env
```

The command performs no LLM extraction and never mutates canonical text. Run
`typed-facts-check`/the Neo4j importer only after staging and independent
review; an empty export is an explicit safe state, not a failed import.

## Curated community/global index

Global and DRIFT-style retrieval is intentionally asynchronous and opt-in. A
reviewed annotation JSONL can be compiled into a release-scoped summary index:

```bash
uv run python database/corpus/build_community_index.py \
  /absolute/path/community-passages.jsonl \
  --release-id snapshot-c439751724ab7f10 \
  --output /absolute/path/community-index.jsonl
```

The input must supply `community_id`, `document_id`, `passage_id`, and
canonical passage `text`. The builder only concatenates bounded source text
and records a source hash; it does not infer clusters or call an LLM. Online
code may use the index to choose document IDs, but must hydrate and verify the
canonical PostgreSQL passages before generation or citation.
