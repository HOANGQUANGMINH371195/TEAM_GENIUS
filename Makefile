SHELL := /usr/bin/env bash

COMPOSE ?= docker compose
UV_CACHE_DIR ?= $(CURDIR)/.cache/uv
PYTHON ?= UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python
WEB_NPM ?= npm --prefix web
ENV_FILE ?= .env
LOCAL_PROFILE ?= local-full

.DEFAULT_GOAL := help
.PHONY: help env-check env-check-production setup typegen typecheck lint test check verify-plan promotion-gate verify-attestation typed-facts-export typed-facts-check typed-facts-stage calibrate-claims \
	build up dev down restart logs health deploy-contract render-validate \
	deploy-render deploy-vercel clean

help:
	@echo "MediPay developer commands"
	@echo "  make setup              Install locked Python/frontend dependencies"
	@echo "  make env-check          Validate .env without printing secrets"
	@echo "  make dev                Start the complete local Docker stack"
	@echo "  make up / down / logs   Manage local services"
	@echo "  make check              Run backend, database, frontend and contract gates"
	@echo "  make build              Build all deployable images and frontend"
	@echo "  make deploy-contract    Verify Render/Vercel/Docker contracts locally"
	@echo "  make verify-plan        Verify forward-plan delivery contracts"
	@echo "  make promotion-gate     Refuse model benchmark while PLAN has blockers"
	@echo "  make verify-attestation Validate the external production gate artifact (ATTESTATION_FILE)"
	@echo "  make typed-facts-check  Validate an accepted release fact JSONL (FACTS_FILE/RELEASE_ID)"
	@echo "  make typed-facts-stage  Stage reviewer facts into PostgreSQL (FACTS_FILE/RELEASE_ID)"
	@echo "  make calibrate-claims   Fit an isotonic calibrator from reviewed labels (LABELS_FILE/OUTPUT)"
	@echo "  make render-validate    Validate render.yaml (CLI if installed, structural fallback otherwise)"
	@echo "  make deploy-render      Trigger an existing Render service deploy"
	@echo "  make deploy-vercel      Deploy web/ through Vercel CLI (requires VERCEL_TOKEN)"
	@echo "  make typed-facts-export Export reviewed legal_facts for Neo4j (FACTS_FILE/RELEASE_ID)"
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
	$(WEB_NPM) run build

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

promotion-gate:
	$(PYTHON) scripts/verify_promotion_gate.py

verify-attestation:
	@test -n "$(ATTESTATION_FILE)" || { echo "Set ATTESTATION_FILE"; exit 2; }
	$(PYTHON) scripts/verify_production_attestation.py "$(ATTESTATION_FILE)" --output "$(ATTESTATION_FILE).report.json"

typed-facts-check:
	@test -n "$(FACTS_FILE)" -a -n "$(RELEASE_ID)" || { echo "Set FACTS_FILE and RELEASE_ID"; exit 2; }
	PYTHONPATH=. $(PYTHON) database/neo4j/scripts/import_typed_facts.py "$(FACTS_FILE)" --release-id "$(RELEASE_ID)" --dry-run

typed-facts-stage:
	@test -n "$(FACTS_FILE)" -a -n "$(RELEASE_ID)" || { echo "Set FACTS_FILE and RELEASE_ID"; exit 2; }
	PYTHONPATH=. $(PYTHON) database/corpus/stage_reviewed_facts.py "$(FACTS_FILE)" --release-id "$(RELEASE_ID)" --env-file "$(ENV_FILE)"

calibrate-claims:
	@test -n "$(LABELS_FILE)" -a -n "$(OUTPUT)" || { echo "Set LABELS_FILE and OUTPUT"; exit 2; }
	PYTHONPATH=. $(PYTHON) eval/calibrate_claims.py "$(LABELS_FILE)" --output "$(OUTPUT)"

typed-facts-export:
	@test -n "$(FACTS_FILE)" -a -n "$(RELEASE_ID)" || { echo "Set FACTS_FILE and RELEASE_ID"; exit 2; }
	PYTHONPATH=. $(PYTHON) database/neo4j/scripts/export_typed_facts.py --env-file "$(ENV_FILE)" --release-id "$(RELEASE_ID)" --output "$(FACTS_FILE)"

render-validate:
	@if command -v render >/dev/null 2>&1; then \
		render blueprints validate render.yaml; \
	else \
		echo "Render CLI not installed; running repository structural contract"; \
		$(PYTHON) scripts/verify_platform_contract.py; \
	fi

deploy-render: env-check-production render-validate
	$(PYTHON) scripts/deploy.py render --env-file $(ENV_FILE)

deploy-vercel: check
	$(PYTHON) scripts/deploy.py vercel --env-file $(ENV_FILE)

clean:
	find . -type d \( -name __pycache__ -o -name .pytest_cache -o -name .ruff_cache \) -prune -exec rm -rf {} +
	rm -rf web/.next web/tsconfig.tsbuildinfo
	find . -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete
