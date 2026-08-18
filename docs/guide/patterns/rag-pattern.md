---
title: "GraphRAG Pattern"
description: "GraphRAG với PageIndex, Supabase PostgreSQL và Neo4j"
weight: 1
---

## GraphRAG

MediPay kết hợp exact, lexical, semantic retrieval, PageIndex provenance và
Neo4j graph traversal.

### Query flow

```text
Query
→ Build query plan
→ Exact + PostgreSQL lexical + `text-embedding-3-small` semantic search
→ Seed bounded relationships in Neo4j
→ PageIndex/legal-unit provenance + RRF fusion
→ Configured LLM generate grounded answer
→ Citations + guardrail
```

### Boundaries

- `src/services/chat.py`: production retrieval service, phối hợp exact/lexical/Qdrant/PageIndex/graph.
- `src/db/repositories.py`: SQL query, không chứa prompt hay LLM logic.
- `src/integrations/embeddings.py`: OpenAI `text-embedding-3-small`, 1536 dimensions.
- `src/integrations/neo4j.py`: bounded graph traversal trên Neo4j Aura/local.
- `src/integrations/llm.py`: interface generation của backend.
- `src/agents/nodes/`: workflow nodes.

### Embedding adapter

```python
class EmbeddingModel(Protocol):
    async def embed_query(self, text: str) -> Sequence[float]: ...
```

Query và passage phải dùng cùng model/dimensions. Không tự ý đổi model,
truncate vector hoặc fallback sang model local nếu chưa tạo release mới.

### Supabase schema

`database/schema.sql` tạo documents, chunks, legal units và tables; Qdrant giữ vector semantic derived.
PageIndex được sinh từ dữ liệu raw và ánh xạ vào legal units. Knowledge graph
được lưu trong Neo4j; embedding dùng `text-embedding-3-small` với vector 1536
chiều. Text dùng làm citation luôn phải lấy lại từ Supabase.
