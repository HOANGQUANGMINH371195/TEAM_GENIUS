# Legal Evidence Timeline contract

The timeline is a read-only product surface over an immutable release.

- Input is a public document signature and optional `as_of` date.
- PostgreSQL resolves and hydrates every document. Internal dataset, document,
  unit, chunk and relationship IDs never cross the API boundary.
- Neo4j supplies only a bounded two-hop navigation walk over approved release
  relationships. An endpoint absent from canonical PostgreSQL is discarded.
- A graph timeout/outage returns canonical metadata with `degraded=true`; it
  never turns graph content into legal evidence.
- `state_at_date` is a deterministic comparison with reviewed effective dates,
  not a legal conclusion. Unknown dates return `unknown`.
- Each document links to the sanitized, hash-verified original HTML viewer.

Rollout flag: `FEATURE_TIMELINE_ENABLED`. Rollback disables the endpoint and UI
entry without changing the active release or any database projection.
