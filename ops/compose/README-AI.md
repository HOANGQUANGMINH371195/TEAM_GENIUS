# AI contract — production Compose

`production.yml` is the one-host runtime boundary: API, web, Valkey, Nginx and
optional Prometheus/Grafana plus a one-shot migration profile. Images must be
digest-pinned; canonical legal data remains in external PostgreSQL/Qdrant/Neo4j.
