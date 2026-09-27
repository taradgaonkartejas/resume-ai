# ResumeAI — Manual Setup

> **Dependencies:** `backend/environment.yaml` is the single source of truth,
> installed with conda. There is no `requirements.txt`. Run conda and the task
> runners from `backend/`.

Scaffolding only: directories, infrastructure and config. No application code — that comes after, following `DESIGN.md` Phase 0 → 5.

**Prerequisites:** Docker + Compose · Python 3.11+ · Node 20+ · `make`

---

## 1. Folder structure

```
resumeai/
├── README.md
├── DESIGN.md
├── Makefile
├── docker-compose.yml
├── .gitignore
│
├── backend/
│   ├── .env                        # server settings + secrets (gitignored)
│   ├── .env.example                # committed template
│   ├── environment.yaml            # conda env spec — dependency source of truth
│   ├── pyproject.toml
│   ├── .venv/                      # gitignored
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                 # routes, startup, CORS
│   │   ├── config.py               # env settings, per-task model routing
│   │   ├── db.py                   # engine, pgvector, SQLite fallback
│   │   ├── identity.py             # X-User-Id resolution + query scoping
│   │   ├── models.py               # SQLAlchemy tables
│   │   ├── schemas.py              # API request/response contracts
│   │   ├── ai/
│   │   │   ├── __init__.py
│   │   │   ├── llm.py              # model gateway, fallback chain
│   │   │   ├── agents.py           # the six specialists
│   │   │   ├── prompts.py
│   │   │   ├── graphs.py           # analysis + tailor graphs, checkpointer
│   │   │   ├── vectorstore.py      # user-scoped retrieval
│   │   │   ├── heuristics.py       # deterministic rule engine
│   │   │   ├── taxonomy.py         # skill aliases, keyword matching
│   │   │   └── resume_ops.py       # target_ref addressing + patching
│   │   └── services/
│   │       ├── __init__.py
│   │       ├── storage.py          # MinIO, disk fallback
│   │       ├── parsing.py
│   │       ├── analysis.py
│   │       ├── tailoring.py
│   │       ├── suggestions.py      # accept/reject/edit lifecycle
│   │       ├── chat.py
│   │       ├── export.py           # PDF / DOCX / TXT
│   │       └── seed.py
│   └── tests/
│       ├── __init__.py
│       ├── test_isolation.py
│       ├── test_interrupt.py
│       ├── test_determinism.py
│       └── test_grounding.py
│
├── frontend/
│   ├── package.json
│   ├── vite.config.ts
│   ├── tailwind.config.js
│   ├── postcss.config.js
│   ├── tsconfig.json
│   ├── index.html
│   ├── node_modules/               # gitignored
│   └── src/
│       ├── main.tsx
│       ├── App.tsx
│       ├── index.css
│       ├── api/
│       │   ├── client.ts           # fetch wrapper, injects X-User-Id
│       │   └── types.gen.ts        # GENERATED — committed, never hand-edited
│       ├── components/
│       │   ├── UserSwitcher.tsx
│       │   ├── ImportResumeModal.tsx
│       │   ├── TemplateModal.tsx
│       │   ├── AnalysisPanel.tsx
│       │   ├── TailorPanel.tsx
│       │   ├── SuggestionCard.tsx
│       │   ├── ChatPane.tsx
│       │   └── ResumePreview.tsx
│       └── lib/
│
├── scripts/
│   └── gen-types.sh
│
└── data/                           # gitignored — fallback storage
    ├── uploads/
    └── exports/
```

---

## 2. Create the tree

```bash
mkdir -p resumeai && cd resumeai

mkdir -p backend/app/{ai,services} backend/tests \
         frontend/src/{api,components,lib} \
         scripts data/{uploads,exports}

# Python needs these or imports fail
touch backend/app/__init__.py \
      backend/app/ai/__init__.py \
      backend/app/services/__init__.py \
      backend/tests/__init__.py

git init
```

---

## 3. `.gitignore`

```gitignore
.venv/
__pycache__/
*.pyc
.pytest_cache/
.ruff_cache/
node_modules/
dist/
backend/.env
frontend/.env.local
data/
*.db
.DS_Store
```

---

## 4. `docker-compose.yml`

```yaml
services:
  db:
    image: pgvector/pgvector:pg16
    container_name: resumeai-db
    restart: unless-stopped
    environment:
      POSTGRES_USER: resumeai
      POSTGRES_PASSWORD: resumeai
      POSTGRES_DB: resumeai
    ports:
      - "5433:5432"          # 5433 avoids clashing with a local 5432
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U resumeai -d resumeai"]
      interval: 5s
      timeout: 5s
      retries: 20

  redis:
    image: redis:7-alpine
    container_name: resumeai-redis
    restart: unless-stopped
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 20

  minio:
    image: quay.io/minio/minio:latest
    container_name: resumeai-minio
    restart: unless-stopped
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: resumeai
      MINIO_ROOT_PASSWORD: resumeai123
    ports:
      - "9000:9000"          # S3 API
      - "9001:9001"          # web console
    volumes:
      - miniodata:/data
    healthcheck:
      test: ["CMD", "mc", "ready", "local"]
      interval: 5s
      timeout: 5s
      retries: 20

  minio-init:
    image: quay.io/minio/mc:latest
    container_name: resumeai-minio-init
    depends_on:
      minio:
        condition: service_healthy
    restart: on-failure
    entrypoint: >
      /bin/sh -c "
      mc alias set local http://minio:9000 resumeai resumeai123;
      mc mb --ignore-existing local/resumeai-uploads;
      mc mb --ignore-existing local/resumeai-exports;
      mc anonymous set download local/resumeai-exports;
      echo 'buckets ready';
      exit 0;
      "

volumes:
  pgdata:
  miniodata:
```

Start and verify:

```bash
docker compose up -d
docker compose ps                 # db + redis + minio healthy, minio-init exited 0
```

MinIO console → http://localhost:9001 (`resumeai` / `resumeai123`). You should see both buckets.

---

## 5. `backend/.env.example`

Configuration is split by trust boundary, not by convenience: the server file
holds secrets, the frontend file ships to the browser. Neither sits at the repo
root, because one shared file is how a server key ends up in a client bundle.

```bash
# ---- LLM (optional: the app runs without a key, with degraded features) ----
UNOROUTER_API_KEY=
UNOROUTER_BASE_URL=https://api.unorouter.com/v1
MODEL_DEFAULT=gemini-3.5-flash-lite:free
EMBEDDING_MODEL=gemini-embedding-2:free

# ---- Database ----
DATABASE_URL=postgresql+psycopg2://resumeai:resumeai@localhost:5433/resumeai
ALLOW_SQLITE_FALLBACK=true

# ---- Object storage ----
S3_ENDPOINT_URL=http://localhost:9000
S3_ACCESS_KEY=resumeai
S3_SECRET_KEY=resumeai123
S3_BUCKET_UPLOADS=resumeai-uploads
S3_BUCKET_EXPORTS=resumeai-exports
ALLOW_DISK_FALLBACK=true

# ---- Redis ----
REDIS_URL=redis://localhost:6379/0

# ---- Local users ----
SEED_USER_COUNT=5
CHAT_TOKEN_ALLOWANCE=25
MAX_UPLOAD_MB=10
```

```bash
cd backend && make env-file          # or: cp .env.example .env
cd ../frontend && cp .env.example .env.local
```

`make env-file` (`.\make.ps1 env-file` on Windows) will not overwrite an
existing `.env` — that file holds the only copy of your key.

Get a key at https://unorouter.com (free tier, no card) and paste it into
`backend/.env`. You can skip this and add it later; the app runs without one.

---

## 6. Backend

Dependencies live in **`backend/environment.yaml`** (conda, conda-forge only)
— that is the single source of truth and the only supported install path.
There is no `requirements.txt`.

`backend/pyproject.toml`:

```toml
[tool.ruff]
line-length = 100
target-version = "py311"

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
```

Install:

```bash
cd backend
conda env create -f environment.yaml
conda activate resume-ai
python -c "import fastapi, langgraph, langchain_openai, boto3, fpdf, docx, pypdf; print('backend deps OK')"
cd ..
```

---

## 7. Frontend

```bash
npm create vite@latest frontend -- --template react-ts
cd frontend
npm install
npm install -D tailwindcss@3 postcss autoprefixer openapi-typescript
npx tailwindcss init -p
```

`frontend/vite.config.ts` — the proxy is what keeps the browser on one origin:

```ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
```

`frontend/tailwind.config.js`:

```js
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: { extend: {} },
  plugins: [],
};
```

Replace `frontend/src/index.css` with:

```css
@tailwind base;
@tailwind components;
@tailwind utilities;
```

```bash
cd ..
```

---

## 8. Type generation

`scripts/gen-types.sh`:

```bash
#!/usr/bin/env bash
# Regenerate frontend types from the live OpenAPI schema.
# The API must be running on :8000.
set -euo pipefail

curl -sf http://localhost:8000/openapi.json > /tmp/resumeai-openapi.json \
  || { echo "API not reachable on :8000 — start it first (make api)"; exit 1; }

npx --prefix frontend openapi-typescript /tmp/resumeai-openapi.json \
  -o frontend/src/api/types.gen.ts

echo "wrote frontend/src/api/types.gen.ts"
```

```bash
chmod +x scripts/gen-types.sh
```

---

## 9. `Makefile`

Tabs, not spaces, for the recipe lines.

```makefile
.PHONY: up down dev api web types seed test lint clean

PY := backend/.venv/bin/python
PIP := backend/.venv/bin/pip

up:
	docker compose up -d
	@echo "Postgres :5433 · Redis :6379 · MinIO :9000 (console :9001)"

down:
	docker compose down

api:
	cd backend && .venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

web:
	cd frontend && npm run dev

dev: up
	@echo "Run 'make api' and 'make web' in two terminals."

types:
	./scripts/gen-types.sh

seed:
	rm -f data/resumeai.db data/agent_checkpoints.db
	$(PY) -c "import sys; sys.path.insert(0,'backend'); \
	from app.db import init_db, SessionLocal; from app.services import seed; \
	init_db(); db=SessionLocal(); seed.seed(db); db.close(); print('seeded')"

test:
	cd backend && .venv/bin/pytest -v

lint:
	cd backend && .venv/bin/ruff check app tests

clean:
	docker compose down -v
	rm -rf data/*.db data/uploads/* data/exports/*
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
```

---

## 10. Verify the scaffold

```bash
docker compose ps                                  # 3 healthy, minio-init exited 0
backend/.venv/bin/python -c "import langgraph, fastapi; print('py OK')"
cd frontend && npm run build && cd ..              # vite builds
```

Postgres reachable:

```bash
docker exec resumeai-db psql -U resumeai -d resumeai -c "SELECT version();"
docker exec resumeai-db psql -U resumeai -d resumeai -c "CREATE EXTENSION IF NOT EXISTS vector; SELECT extname FROM pg_extension WHERE extname='vector';"
```

That last query must return `vector`. If it doesn't, the image is wrong — confirm it's `pgvector/pgvector:pg16`, not plain `postgres:16`.

---

## 11. What comes next

The tree is empty of application code by design. Fill it in the order in `DESIGN.md` §11:

| Phase | Build |
|---|---|
| 0 | `config.py`, `db.py`, `models.py`, `identity.py` → `make seed` succeeds |
| 1 | `ai/` — llm, vectorstore, agents, graphs, heuristics, taxonomy, resume_ops |
| 2 | `services/` — storage, parsing, analysis, tailoring, suggestions, chat, export, seed |
| 3 | `main.py` routes → `make types` produces `types.gen.ts` |
| 4 | `frontend/src/` — switcher, modals, 3-pane workspace |
| 5 | `tests/` — the four acceptance-gate tests |

Recommended first checkpoint: after Phase 0, `make seed` then `make api`, and confirm `curl localhost:8000/api/health` reports `"database": "postgresql"` and `"pgvector": true`. If it says `sqlite`, the fallback engaged — Docker isn't reachable, and it's worth fixing before building on top.

---

## Gotchas

| Symptom | Cause |
|---|---|
| `ModuleNotFoundError: No module named 'app'` | Missing `__init__.py`, or not running from `backend/` |
| Health reports `"database": "sqlite"` | Postgres unreachable; check `docker compose ps` and that the port is 5433 |
| `"pgvector": false` | Image isn't `pgvector/pgvector` |
| Port 5432 conflict | Intentional — host port is 5433. Don't change it to 5432 |
| Storage mode `disk` | MinIO down, or `minio-init` never ran; `docker compose logs minio-init` |
| Frontend 404s on `/api/*` | Vite proxy missing, or the API isn't on :8000 |
| `make` "missing separator" | Recipe lines must start with a real tab |
| Embedding calls failing | Expected — `gemini-embedding-2:free` is ~74% reliable; the local fallback covers it |
