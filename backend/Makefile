# Short aliases for the schema CLI — the `npx prisma ...` equivalent.
# Every target just forwards to `python -m app.cli`.

# Use the local venv if it exists, otherwise whatever python is on PATH
# (i.e. the active conda env). Hardcoding .venv/bin/python breaks conda users.
PY := $(shell test -x .venv/bin/python && echo .venv/bin/python || echo python)

# Windows: use .\make.ps1 <target> instead — same target names.

.PHONY: help up down logs ps env env-update db-push db-status db-seed db-reset \
        db-inspect db-sql db-create db-drop migrate migrate-deploy migrate-status \
        migrate-down test lint run

help:
	@echo "Services (docker compose, run from the repo root)"
	@echo "  make up               start Postgres + MinIO + Redis, wait for the db"
	@echo "  make ps               service status"
	@echo "  make logs             follow logs"
	@echo "  make down             stop (volumes survive)"
	@echo ""
	@echo "Environment (environment.yaml is the source of truth)"
	@echo "  make env              conda env create -f environment.yaml"
	@echo "  make env-update       conda env update --prune"
	@echo ""
	@echo "Schema (app/models.py is the source of truth)"
	@echo "  make db-push          create database + tables to match the models"
	@echo "  make db-push-seed     ... and seed"
	@echo "  make db-status        show drift between models and database"
	@echo "  make db-inspect       tables, columns, row counts"
	@echo "  make db-seed          run the seeder"
	@echo "  make db-reset         drop, recreate, push, seed (destructive)"
	@echo "  make db-sql           write schema.sql"
	@echo ""
	@echo "Migrations"
	@echo '  make migrate m="add x"  autogenerate a revision and apply it'
	@echo "  make migrate-deploy     apply pending revisions"
	@echo "  make migrate-status     current revision and history"
	@echo "  make migrate-down       roll back one revision"
	@echo ""
	@echo "  make test / make lint / make run"

# `up` waits on db only: a bare --wait fails when the one-shot minio-init exits.
up:
	cd .. && docker compose up -d && docker compose up -d db --wait
	@cd .. && docker compose ps

ps:
	cd .. && docker compose ps

logs:
	cd .. && docker compose logs -f

down:
	cd .. && docker compose down

env:
	conda env create -f environment.yaml

env-update:
	conda env update -f environment.yaml --prune

db-push:
	$(PY) -m app.cli db push

db-push-seed:
	$(PY) -m app.cli db push --seed

db-create:
	$(PY) -m app.cli db create

db-drop:
	$(PY) -m app.cli db drop

db-status:
	$(PY) -m app.cli db status

db-inspect:
	$(PY) -m app.cli db inspect

db-seed:
	$(PY) -m app.cli db seed

db-reset:
	$(PY) -m app.cli db reset

db-sql:
	$(PY) -m app.cli db sql --out ../schema.sql

# usage: make migrate m="add users.linkedin_url"
migrate:
	@test -n "$(m)" || (echo 'usage: make migrate m="your message"'; exit 1)
	$(PY) -m app.cli migrate dev -m "$(m)"

migrate-deploy:
	$(PY) -m app.cli migrate deploy

migrate-status:
	$(PY) -m app.cli migrate status

migrate-down:
	$(PY) -m app.cli migrate down

test:
	$(PY) -m pytest -q

lint:
	$(PY) -m ruff check app tests

run:
	$(PY) -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
