# Supabase retention and storage runbook

PostgreSQL keeps one active immutable release and the minimum previous release
needed for rollback. Qdrant and Neo4j projections are release-scoped and can be
rebuilt from canonical PostgreSQL/data artifacts; they are not a reason to keep
duplicate raw HTML in Supabase.

Before removing a named superseded release:

1. record its row/table sizes and release checksum;
2. verify the active pointer, Qdrant point count/hash parity and Neo4j approved
   edge count;
3. create and checksum a recoverable backup;
4. run the rollback rehearsal against the retained previous release;
5. delete only the explicitly named release in a reviewed migration.

Never delete `table_cells`, canonical `documents.raw_html`, or the active
release to save space. `table_cell_facts` is a rebuildable projection, but its
provenance must be retained in the source artifact before pruning.
