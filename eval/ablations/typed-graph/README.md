# Typed graph ablation

Compare document-only Neo4j expansion with typed BHYT fact/PPR expansion on
relational and multi-hop cases. Every graph result must hydrate to canonical
PostgreSQL evidence. Report path precision, latency and outage degradation.

`evaluate.py` consumes a reviewed trace artifact containing `gold_path_ids`,
both path variants, source hashes, and explicit outage fallback outcomes. It
reports IR/path metrics only and never treats graph node counts as legal truth.
