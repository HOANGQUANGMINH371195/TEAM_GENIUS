#!/usr/bin/env python3
"""Verify AWS backend and Vercel frontend contracts without network calls.

This is intentionally a structural gate. AWS EC2/Compose is the backend
production target and Vercel hosts the Next.js frontend; authenticated host,
DNS and browser smoke remain external checks.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("eval/results/platform-contract-current.json"))
    args = parser.parse_args()

    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    worker_dockerfile = (ROOT / "Dockerfile.worker").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    web_dockerfile = (ROOT / "web/Dockerfile").read_text(encoding="utf-8")
    vercel = json.loads((ROOT / "web/vercel.json").read_text(encoding="utf-8"))
    vercel_text = json.dumps(vercel, ensure_ascii=False)

    checks: dict[str, bool] = {
        "research_worker_dockerfile_exists": bool(worker_dockerfile),
        "research_worker_has_no_http_healthcheck": "HEALTHCHECK" not in worker_dockerfile,
        "research_worker_cmd_is_module": 'CMD ["-m", "src.research_worker"]' in worker_dockerfile,
        "research_worker_is_non_root": "FROM python:3.11-slim-bookworm" in worker_dockerfile
        and "USER 10001:10001" in worker_dockerfile
        and "apt-get dist-upgrade -y" in worker_dockerfile,
        "compose_worker_requires_redis": "RESEARCH_QUEUE_BACKEND: redis" in compose,
        "compose_declares_research_worker_profile": "research-worker:" in compose
        and "profiles: [research-worker]" in compose,
        "compose_worker_uses_internal_redis": "RESEARCH_QUEUE_REDIS_URL: redis://redis:6379/1" in compose,
        "compose_worker_depends_on_redis": compose.count("service_healthy") >= 4
        and "research-worker" in compose,
        "managed_profile_forces_production": "APP_ENV: production" in compose,
        "backend_reads_port": 'os.environ.get("PORT", "8000")' in dockerfile
        or 'os.environ.get("PORT", "8000")' in (ROOT / "src/runtime_entrypoint.py").read_text(encoding="utf-8"),
        "backend_binds_all_interfaces": 'host="0.0.0.0"' in (ROOT / "src/runtime_entrypoint.py").read_text(encoding="utf-8"),
        "backend_healthcheck_uses_port": "os.getenv('PORT', '8000')" in dockerfile,
        "compose_passes_langfuse_without_baking_secrets": all(
            f"LANGFUSE_{name}:" in compose
            for name in ("PUBLIC_KEY", "SECRET_KEY", "HOST", "BASE_URL")
        ),
        "backend_runtime_is_non_root": "USER 10001:10001" in dockerfile
        and "apt-get dist-upgrade -y" in dockerfile,
        "web_runtime_is_non_root": "cgr.dev/chainguard/node" in web_dockerfile,
        "vercel_is_nextjs": vercel.get("framework") == "nextjs",
        "vercel_has_security_headers": "Content-Security-Policy" in vercel_text
        and "X-Content-Type-Options" in vercel_text,
        "vercel_has_no_admin_secret": "FIREBASE_SERVICE_ACCOUNT_JSON" not in vercel_text,
    }
    report: dict[str, Any] = {
        "structural_pass": all(checks.values()),
        "external_attestation_required": True,
        "checks": checks,
        "external_checks": [
            "AWS EC2/SSM host, security group, TLS and authenticated production smoke",
            "AWS Compose rollback/restore and provider-outage drill",
            "Firebase authorized domains and production secret installation",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"structural_pass": report["structural_pass"], "external_attestation_required": True}))
    return 0 if report["structural_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
