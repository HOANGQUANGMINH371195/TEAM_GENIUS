# AWS single-host runbook

This is the operational path for the architecture in `ops/compose/production.yml`.
It keeps the API/web images immutable and leaves PostgreSQL, Qdrant, Neo4j,
Firebase and OpenAI credentials in the host `.env` only.

## First bootstrap

1. Create an EC2 Graviton instance with an encrypted EBS volume and a security
   group that exposes only TCP 80/443. Attach the SSM role; do not open SSH to
   the Internet.
2. Point the web DNS record at the instance and install Docker through
   `ops/ansible/site.yml` (the playbook assertions require image digests and a
   real domain).
3. Before starting the Compose service, issue the certificate into the path the
   playbook expects:

```bash
sudo certbot certonly --standalone -d app.example.com
sudo cp -a /etc/letsencrypt /opt/medipay/
```

4. Store the production `.env`, metrics token and Grafana password in an
   Ansible Vault vars file. Set `DATABASE_URL`/`RUNTIME_DATABASE_URL` to the
   Supabase runtime pooler URL and keep `FIREBASE_SERVICE_ACCOUNT_JSON` only in
   the backend `.env`.
5. Run the playbook with `medipay_api_image` and `medipay_web_image` pinned to
   GHCR `@sha256:` digests. It installs the systemd unit and starts the API,
   web, Valkey, Nginx, Prometheus and Grafana services.
6. Apply SQL migrations once using the migration image before promoting traffic:

```bash
docker run --rm --env-file /opt/medipay/compose/.env \
  -e MIGRATION_DATABASE_URL="$RUNTIME_DATABASE_URL" \
  ghcr.io/your-org/medipay-migrate@sha256:<attested-digest>
```

The migration image is built from `Dockerfile.migrate` and is run once, before
the API is promoted; it is never placed in the API systemd restart loop.

## Verification and rollback

Run `make health` from a trusted host, then authenticated chat/SSE, viewer,
calculator and conversation-context smoke checks. Inspect `/ready` and the
Prometheus dashboard before changing the active digest. Rollback means stopping
promotion, switching both image digests to the previous attested pair, and
restoring the matching PostgreSQL release pointer/Qdrant and Neo4j projections.
