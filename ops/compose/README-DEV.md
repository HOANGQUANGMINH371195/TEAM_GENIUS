# Production Compose (developer)

`production.yml` is the single-host AWS profile. Set `API_IMAGE`, `WEB_IMAGE`
and `MIGRATION_IMAGE` to immutable GHCR digests and place `.env`, the rendered Nginx vhost and
monitoring secrets under `/opt/medipay` before starting it.

```bash
docker compose -f ops/compose/production.yml --profile monitoring config
docker compose -f ops/compose/production.yml --profile migration run --rm migrate
docker compose -f ops/compose/production.yml --profile monitoring up -d
```

Grafana is reachable at `https://<domain>/grafana/` through Nginx and always
requires its configured admin login (`GF_AUTH_ANONYMOUS_ENABLED=false`). The
monitoring profile must remain enabled because Nginx proxies this path to the
internal Grafana service; do not expose Grafana's container port directly.
