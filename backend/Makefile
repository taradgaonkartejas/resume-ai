# Short aliases for the schema CLI — the `npx prisma ...` equivalent.
# Every target just forwards to `python -m app.cli`.

# Interpreter resolution, in priority order:
#   1. a local .venv, if you use one
#   2. $CONDA_PREFIX — the ACTIVATED env, which is authoritative
#   3. plain `python` from PATH
#
# Step 2 exists because "python on PATH" and "the activated conda env" are not
# the same thing. On Anaconda installs that put base on the system PATH, the
# prompt can read (resume-ai) while `python` still resolves to base — and then
# every package installed into the env is invisible. The symptom is a
# ModuleNotFoundError for something `conda list` plainly shows as installed.
# $CONDA_PREFIX is set by `conda activate` itself, so it cannot disagree.
PY := $(shell \
	if [ -x .venv/bin/python ]; then echo .venv/bin/python; \
	elif [ -n "$$CONDA_PREFIX" ] && [ -x "$$CONDA_PREFIX/bin/python" ]; then echo "$$CONDA_PREFIX/bin/python"; \
	else echo python; fi)

# Windows: use .\make.ps1 <target> instead — same target names.

.PHONY: help up down logs ps doctor which-python env env-file env-update db-push db-status db-seed db-reset \
        db-inspect db-sql db-create db-drop migrate migrate-deploy migrate-status \
        migrate-down test lint run models critic-report critic-report-llm

help:
	@echo "Services (docker compose, run from the repo root)"
	@echo "  make up               start Postgres + MinIO + Redis, wait for the db"
	@echo "  make ps               service status"
	@echo "  make logs             follow logs"
	@echo "  make down             stop (volumes survive)"
	@echo ""
	@echo "Environment (environment.yaml = packages, .env = settings)"
	@echo "  make env-file         create backend/.env from .env.example"
	@echo "  make which-python     show which interpreter these targets use"
	@echo "  make doctor           diagnose a broken environment"
	@echo "  make env              conda env create -f environment.yaml"
	@echo "  make env-update       conda env update --prune"
	@echo ""
	@echo "  backend/.env is THIS directory's config and holds real secrets."
	@echo "  The browser app is configured separately in frontend/.env.local."
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
	@echo ""
	@echo "AI transport (needs UNOROUTER_API_KEY in backend/.env)"
	@echo "  make models              which models the key can actually call"
	@echo "  make critic-report       score the rule critic offline"
	@echo "  make critic-report-llm   ... and the live LLM critic, paced for the free tier"

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

# Print the interpreter these targets will actually use. First thing to check
# when an import fails for a package `conda list` says is installed.
which-python:
	@$(PY) doctor.py --which

doctor:
	$(PY) doctor.py

# Never clobber an existing .env: it holds the only copy of the API key, and
# a task runner that silently overwrites secrets is a task runner nobody can
# trust. Re-running this is always safe.
env-file:
	@if [ -f .env ]; then \
		echo "backend/.env already exists — leaving it alone."; \
	else \
		cp .env.example .env; \
		echo "created backend/.env — add UNOROUTER_API_KEY (optional)."; \
	fi

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

# Answers "which models can this key actually call?" -- listing a model is not
# the same as being entitled to it, so this makes one real call per model.
models:
	$(PY) -m scripts.check_model_access

critic-report:
	$(PY) -m scripts.critic_report

# --sleep because the free tier allows one request per minute per model per
# account: an unpaced run throttles and every case degrades to the rule
# engine, which once produced a confident and completely false quality verdict.
critic-report-llm:
	$(PY) -m scripts.critic_report --llm --sleep 60

run:
	$(PY) -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
