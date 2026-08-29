SHELL := /usr/bin/env bash

COMPOSE ?= docker compose
UV_CACHE_DIR ?= $(CURDIR)/.cache/uv
# Keep every developer command self-contained: a fresh clone may not have a
# pre-created .venv yet, so resolve the locked development environment through
# uv instead of relying on whichever interpreter happens to be on PATH.
PYTHON ?= UV_CACHE_DIR=$(UV_CACHE_DIR) uv run --python 3.11 --with-requirements requirements/dev.lock python
WEB_NPM ?= npm --prefix web
ENV_FILE ?= .env
LOCAL_PROFILE ?= local-full
RELEASE_ROOT ?= .

.DEFAULT_GOAL := help
.PHONY: help env-check env-check-production setup typegen typecheck lint test check verify-plan implementation-gate promotion-gate verify-attestation verify-release-artifacts typed-facts-export typed-facts-check typed-facts-stage calibrate-claims research-worker collect-production-evidence migrate plan-completion \
	build up dev down restart logs health deploy-contract render-validate \
	build-worker deploy-render deploy-vercel aws-config aws-up aws-migrate ansible-bootstrap promptfoo clean

PROD_COMPOSE ?= ops/compose/production.yml
ANSIBLE_INVENTORY ?= ops/ansible/inventory.ini

help:
	@echo "MediPay developer commands"
	@echo "  make setup              Install locked Python/frontend dependencies"
	@echo "  make env-check          Validate .env without printing secrets"
	@echo "  make dev                Start the complete local Docker stack"
	@echo "  make up / down / logs   Manage local services"
	@echo "  make check              Run backend, database, frontend and contract gates"
	@echo "  make build              Build all deployable images and frontend"
	@echo "  make build-worker       Build the dedicated Redis research worker image"
	@echo "  make deploy-contract    Verify Render/Vercel/Docker contracts locally"
	@echo "  make verify-plan        Verify forward-plan delivery contracts"
	@echo "  make implementation-gate Verify all PLAN capabilities exist before benchmark"
	@echo "  make promotion-gate     Report benchmark readiness vs production promotion blockers"
	@echo "  make plan-completion    Print the external-evidence closing runbook"
	@echo "  make verify-attestation Validate the external production gate artifact (ATTESTATION_FILE)"
	@echo "  make verify-release-artifacts Validate mounted release hashes (RELEASE_ROOT/REQUIRE_RELEASE_ARTIFACTS)"
	@echo "  make typed-facts-check  Validate an accepted release fact JSONL (FACTS_FILE/RELEASE_ID)"
	@echo "  make typed-facts-stage  Stage reviewer facts into PostgreSQL (FACTS_FILE/RELEASE_ID)"
	@echo "  make calibrate-claims   Fit an isotonic calibrator from reviewed labels (LABELS_FILE/OUTPUT)"
	@echo "  make research-worker    Run the durable Redis research worker (RESEARCH_QUEUE_BACKEND=redis)"
	@echo "  make up-research-worker Start the local worker container profile"
	@echo "  make collect-production-evidence Collect live SSE latency/TTFT evidence (ENDPOINT/FIXTURE/OUTPUT)"
	@echo "  make render-validate    Validate render.yaml (CLI if installed, structural fallback otherwise)"
	@echo "  make deploy-render      Trigger an existing Render service deploy"
	@echo "  make deploy-vercel      Deploy web/ through Vercel CLI (requires VERCEL_TOKEN)"
	@echo "  make aws-config        Validate the immutable AWS Compose profile"
	@echo "  make aws-up            Start the AWS single-host Compose profile"
	@echo "  make aws-migrate       Run the pinned one-shot migration image"
	@echo "  make ansible-bootstrap Bootstrap EC2 with ops/ansible (vars are external)"
	@echo "  make promptfoo         Run offline Promptfoo red-team checks"
	@echo "  make typed-facts-export Export reviewed legal_facts for Neo4j (FACTS_FILE/RELEASE_ID)"
	@echo "  make migrate           Apply ordered PostgreSQL migrations with advisory lock (ENV_FILE)"
	@echo "  make clean              Remove only reproducible caches/build output"

env-check:
	$(PYTHON) scripts/check_env_contract.py --env-file $(ENV_FILE) --mode local

env-check-production:
	$(PYTHON) scripts/check_env_contract.py --env-file $(ENV_FILE) --mode production

setup:
	@command -v uv >/dev/null 2>&1 || { echo "uv is required: https://docs.astral.sh/uv/getting-started/installation/"; exit 2; }
	@test -x .venv/bin/python || uv venv --python 3.11 .venv
	uv pip install --python .venv/bin/python --require-hashes -r requirements/dev.lock
	$(WEB_NPM) ci
	$(MAKE) env-check

typegen:
	$(WEB_NPM) run typegen

typecheck: typegen
	$(WEB_NPM) run typecheck

lint:
	$(PYTHON) -m ruff check src tests database scripts eval
	$(WEB_NPM) run lint

test:
	$(PYTHON) -m pytest -q tests
	PYTHONPATH=database/pipeline:. $(PYTHON) -m pytest -q database/pipeline/tests database/corpus/tests database/neo4j/tests eval/test_*.py

check: env-check lint typecheck test
	$(PYTHON) -m compileall -q src database scripts eval
	git diff --check

build:
	$(COMPOSE) --profile $(LOCAL_PROFILE) build
	$(MAKE) build-worker
	$(WEB_NPM) run build

build-worker:
	docker build --pull --file Dockerfile.worker --tag medipay-research-worker:latest .

up: env-check
	$(COMPOSE) --profile $(LOCAL_PROFILE) up -d --build

dev: up
	@echo "API: http://localhost:8000 | Web: http://localhost:3000"

down:
	$(COMPOSE) --profile $(LOCAL_PROFILE) down

restart:
	$(COMPOSE) --profile $(LOCAL_PROFILE) up -d --force-recreate

logs:
	$(COMPOSE) --profile $(LOCAL_PROFILE) logs -f --tail=200

health:
	curl --fail --silent --show-error http://localhost:8000/health
	@echo
	curl --fail --silent --show-error http://localhost:8000/ready
	@echo

deploy-contract:
	$(PYTHON) scripts/verify_platform_contract.py
	$(PYTHON) scripts/verify_deploy_contract.py

verify-plan:
	$(PYTHON) scripts/verify_plan_contract.py

implementation-gate:
	$(PYTHON) scripts/verify_implementation_gate.py

promotion-gate:
	$(PYTHON) scripts/verify_promotion_gate.py

plan-completion:
	@sed -n '1,240p' ops/runbooks/plan-completion.md

verify-attestation:
	@test -n "$(ATTESTATION_FILE)" || { echo "Set ATTESTATION_FILE"; exit 2; }
	$(PYTHON) scripts/verify_production_attestation.py "$(ATTESTATION_FILE)" --output "$(ATTESTATION_FILE).report.json"

verify-release-artifacts:
	$(PYTHON) scripts/verify_release_artifacts.py --root "$(RELEASE_ROOT)" $(if $(REQUIRE_RELEASE_ARTIFACTS),--require,)

typed-facts-check:
	@test -n "$(FACTS_FILE)" -a -n "$(RELEASE_ID)" || { echo "Set FACTS_FILE and RELEASE_ID"; exit 2; }
	PYTHONPATH=. $(PYTHON) database/neo4j/scripts/import_typed_facts.py "$(FACTS_FILE)" --release-id "$(RELEASE_ID)" --dry-run

typed-facts-stage:
	@test -n "$(FACTS_FILE)" -a -n "$(RELEASE_ID)" || { echo "Set FACTS_FILE and RELEASE_ID"; exit 2; }
	PYTHONPATH=. $(PYTHON) database/corpus/stage_reviewed_facts.py "$(FACTS_FILE)" --release-id "$(RELEASE_ID)" --env-file "$(ENV_FILE)"

calibrate-claims:
	@test -n "$(LABELS_FILE)" -a -n "$(OUTPUT)" || { echo "Set LABELS_FILE and OUTPUT"; exit 2; }
	PYTHONPATH=. $(PYTHON) eval/calibrate_claims.py "$(LABELS_FILE)" --output "$(OUTPUT)"

research-worker: env-check
	$(PYTHON) -m src.research_worker

up-research-worker: env-check
	$(COMPOSE) --profile local-full --profile research-worker up -d --build research-worker

collect-production-evidence: implementation-gate
	@test -n "$(ENDPOINT)" -a -n "$(FIXTURE)" -a -n "$(OUTPUT)" || { echo "Set ENDPOINT, FIXTURE and OUTPUT"; exit 2; }
	$(PYTHON) eval/collect_production_evidence.py --endpoint "$(ENDPOINT)" --fixture "$(FIXTURE)" --output "$(OUTPUT)"

typed-facts-export:
	@test -n "$(FACTS_FILE)" -a -n "$(RELEASE_ID)" || { echo "Set FACTS_FILE and RELEASE_ID"; exit 2; }
	PYTHONPATH=. $(PYTHON) database/neo4j/scripts/export_typed_facts.py --env-file "$(ENV_FILE)" --release-id "$(RELEASE_ID)" --output "$(FACTS_FILE)"

migrate:
	$(PYTHON) database/postgres/migrations/runner.py --env-file "$(ENV_FILE)"

render-validate:
	@if command -v render >/dev/null 2>&1 && render whoami --output json >/dev/null 2>&1; then \
		render blueprints validate render.yaml && render blueprints validate render-research-worker.yaml; \
	else \
		echo "Render CLI unavailable or unauthenticated; running repository structural contract"; \
		$(PYTHON) scripts/verify_platform_contract.py; \
	fi

aws-config:
	@test -n "$(API_IMAGE)" -a -n "$(WEB_IMAGE)" -a -n "$(MIGRATION_IMAGE)" -a -n "$(MEDIPAY_DOMAIN)" || { echo "Set API_IMAGE, WEB_IMAGE, MIGRATION_IMAGE and MEDIPAY_DOMAIN"; exit 2; }
	docker compose -f $(PROD_COMPOSE) --profile monitoring config --quiet

aws-up: aws-config
	docker compose -f $(PROD_COMPOSE) --profile monitoring up -d --remove-orphans

aws-migrate:
	@test -n "$(MIGRATION_IMAGE)" || { echo "Set MIGRATION_IMAGE to an immutable migration digest"; exit 2; }
	docker compose -f $(PROD_COMPOSE) --profile migration run --rm migrate

ansible-bootstrap:
	@test -f "$(ANSIBLE_INVENTORY)" || { echo "Copy ops/ansible/inventory.ini.example to $(ANSIBLE_INVENTORY)"; exit 2; }
	ansible-playbook -i "$(ANSIBLE_INVENTORY)" ops/ansible/site.yml --ask-become-pass --extra-vars @vault-production.yml

promptfoo:
	npx --yes promptfoo@latest eval -c eval/promptfoo.yaml --no-cache

deploy-render: env-check-production render-validate
	$(PYTHON) scripts/deploy.py render --env-file $(ENV_FILE)

deploy-vercel: check
	$(PYTHON) scripts/deploy.py vercel --env-file $(ENV_FILE)

clean:
	find . -type d \( -name __pycache__ -o -name .pytest_cache -o -name .ruff_cache \) -prune -exec rm -rf {} +
	rm -rf web/.next web/tsconfig.tsbuildinfo
	find . -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete
