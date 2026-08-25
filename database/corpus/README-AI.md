# Corpus — AI contract

Canonical PostgreSQL text is authoritative. Qdrant passages are accepted only
when `dataset_id`, `text_sha256` and `embedding_input_sha256` match the
canonical row. Neo4j edges require `approved_evidence`, typed direction/scope,
and a verified evidence hash/span. External/reference-only nodes are navigation
metadata, not document text.
