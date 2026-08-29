# Nginx (developer)

`nginx.conf` is the immutable base configuration. `medipay.conf` is generated
on the host from `medipay.conf.example` after `MEDIPAY_DOMAIN` and ACME paths
are set; it is intentionally not committed because certificates are host-only.

Validate before restart:

```bash
docker run --rm -v "$PWD/ops/nginx/nginx.conf:/etc/nginx/nginx.conf:ro" \
  nginx:1.27-alpine nginx -t
```
