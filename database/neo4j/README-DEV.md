# Neo4j — developer guide

Import only the release-scoped graph and mark each edge's serving status,
direction, scope, evidence text/hash/span and release fingerprint. Expansion
must be bounded, deterministic and safe for inbound/outbound paths. Run graph
tests before changing Cypher or relationship properties.

For an already imported release, backfill the serving contract additively:

```bash
.venv/bin/python database/neo4j/scripts/repair_serving_properties.py \
  --dataset-id snapshot-c439751724ab7f10
```

The repair sets canonical/reference `answer_ready`, evidence offsets, checked
time, official URL and release fingerprint; it does not delete nodes or edges.
