# Legacy deploy runbook — Render API / Vercel web

AWS EC2 + Docker Compose + Nginx is the sole production deployment target.
Render and Vercel are retained only for backwards-compatible migration or
temporary preview; neither is required for an AWS release and neither is
production evidence. Use [aws-single-host.md](aws-single-host.md) for the
active path. Never paste secret values into a shell transcript or CI log.

## External prerequisites

The Firebase service-account JSON previously shared during development is
compromised. Revoke that key in Firebase/Google Cloud IAM, generate a new key,
and install it only in the AWS backend secret store (Ansible Vault or an
ignored host `.env`). If an environment variable is used, set the new
single-line JSON as `FIREBASE_SERVICE_ACCOUNT_JSON`. Never put it in Vercel,
the browser bundle, Git, or this runbook.

Generate the backend metrics bearer token locally and install it directly in
the AWS secret store (do not commit or send it here):

```bash
python -c 'import secrets; print(secrets.token_urlsafe(32))'
```

Render/Vercel credentials are not needed for the AWS production path. If a
legacy migration preview is explicitly requested, keep those credentials
outside the repository and never reuse them as application runtime variables.

These are operator credentials, not application runtime variables. Do not add
them to `.env.example` or pass them as Docker build arguments.

1. Verify `ops.active_release` generation and the release fingerprint; all
   three projection rows must be `ready` with exact counts.
2. Create PostgreSQL and Neo4j backups and retain active plus previous release.
3. Build migration, API and web images from a clean checkout; inspect context,
   SBOM and non-root/read-only settings.
4. Run the migration job once with `MIGRATION_DATABASE_URL`; never give that
   credential to API replicas.
5. Provision the AWS EC2 host, install Docker/Compose and Nginx, and load the
   vaulted runtime secrets.
6. Start the Compose stack behind Nginx with `/health` liveness and `/ready`
   readiness. Only public Firebase client variables may enter the browser
   bundle; Admin JSON belongs only in the backend secret store.
7. Set the protected `METRICS_TOKEN` and configure the dashboard scraper with
   an Authorization bearer header; never expose `/metrics` publicly in
   production.
8. Run authenticated browser login → chat → SSE final/cancel → citation smoke,
   then the dependency-failure and 20-request readiness smoke.
9. Promote only if quality, latency, cost and security gates pass. Keep the
   previous service/image/projections until the observation window ends.

Rollback: stop promotion, restore the previous API/web images and release
pointer, restore matching Qdrant/Neo4j projections, verify parity, then
re-enable Nginx traffic. See `aws-single-host.md` and `rollback.md`.
