---
title: "GraphRAG Pattern"
description: "GraphRAG trên Supabase PostgreSQL"
weight: 1
---

## GraphRAG

MediPay dùng semantic retrieval và graph traversal trong cùng Supabase PostgreSQL + `pgvector`.

### Query flow

```text
Query
→ Extract entities
→ Embed query (provider-neutral adapter)
→ Search document_chunks bằng pgvector
→ Expand relations quanh entities
→ Merge context + provenance
→ Local LLM generate grounded answer
→ Citations + guardrail
```

### Boundaries

- `src/graph_rag/retrieval.py`: phối hợp vector result và graph result.
- `src/db/repositories.py`: SQL query, không chứa prompt hay LLM logic.
- `src/integrations/embeddings.py`: interface embedding, model chưa chốt.
- `src/integrations/llm.py`: interface generation, model chưa chốt.
- `src/agents/nodes/`: workflow nodes.

### Provider-neutral adapter

```python
class EmbeddingModel(Protocol):
    async def embed_query(self, text: str) -> Sequence[float]: ...
```

Khi team chọn Ollama, llama.cpp, vLLM hoặc runtime khác, thêm adapter implement protocol và cấu hình env. Không hardcode OpenAI embedding.

### Supabase schema

`supabase/migrations/0001_initial_graphrag.sql` tạo `documents`, `document_chunks`, `entities`, `relations`, `conversations`, `messages`. Dimension vector và index vector chỉ chốt sau khi chọn embedding model.
