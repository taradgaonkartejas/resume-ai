# ResumeAI Backend

FastAPI + SQLAlchemy 2.0, three layers: **controller → service → repository**.
28 operations, 152 tests passing, ruff clean.

## Quick start

Docker Compose provides Postgres, MinIO and Redis. Three steps from a clean
checkout to a running API.

Every command below runs from **`backend/`** — that is where the task runners,
`environment.yaml` and the app live. The `up` target steps into the repo root
on its own, so you never have to.

**Linux / macOS**

```bash
cd backend
conda env create -f environment.yaml && conda activate resume-ai
make env-file                 # backend/.env from .env.example
make up                       # docker compose up -d, waits for the db
make db-push-seed             # tables + demo data
make run                      # API on :8000
```

**Windows (PowerShell)**

```powershell
cd backend
conda env create -f environment.yaml ; conda activate resume-ai
.\make.ps1 env-file            # backend\.env from .env.example
.\make.ps1 up                  # docker compose up -d, waits for the db
.\make.ps1 db-push-seed        # tables + demo data
.\make.ps1 run                 # API on :8000
```

> `.\make.ps1` only resolves from inside `backend\`. Running it from the repo
> root gives *"The term '.\make.ps1' is not recognized"* — `cd backend` first.
> PowerShell needs the leading `.\`; a bare `make.ps1` will not run either.

Docs at http://localhost:8000/docs · health at `/api/health`.

> **Windows has no `make`.** `make.ps1` is the same task runner with the same
> target names, so every `make X` below is `.\make.ps1 X`. Both are thin
> wrappers — you can always call the CLI directly:
> `python -m app.cli db push --seed`.
>
> If PowerShell blocks the script, run
> `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` for the session,
> or just use `python -m app.cli ...`.

### What Compose starts

`docker compose up -d` runs from the **repo root**, not `backend/`.

| Service | Host port | Purpose |
|---|---|---|
| `db` | **5433** → 5432 | Postgres 16 + pgvector |
| `minio` | 9000 (API), 9001 (console) | S3 object storage |
| `minio-init` | — | one-shot: creates both buckets, then exits |
| `redis` | 6379 | cache |
| `redis-insight` | 5540 | Redis web UI |

Credentials come from `docker-compose.yml` and must match `backend/.env`:
Postgres `admin` / `Admin123` / db `resumeai`; MinIO `minio` / `minio123`.

Useful checks:

```bash
docker compose ps                 # all services should be healthy
docker compose logs -f db         # or minio / minio-init
docker compose logs minio-init    # expect "buckets ready"
docker compose down               # stop; volumes survive
docker compose down -v            # stop AND delete all data
```

Two things that trip people up:

- **Postgres is on host port 5433**, not 5432, so it never collides with a
  local install. `backend/.env` already points at 5433.
- **`minio-init` is supposed to exit.** It creates `resumeai-uploads` and
  `resumeai-exports`, prints `buckets ready`, and stops. `docker compose ps`
  showing it as `Exited (0)` is success, not a failure.

Wait for the database before pushing the schema — `db-push` fails while
Postgres is still starting. `docker compose ps` showing `db` as `healthy` is
the signal, or gate on it directly:

```bash
docker compose up -d db --wait    # waits for the db healthcheck only
```

Avoid a bare `docker compose up -d --wait` here: on current Compose versions
`--wait` treats *any* exited container as a failure and returns a non-zero exit
code when `minio-init` completes normally, even though the whole stack is
healthy. Naming `db` sidesteps that.

### 1. Environment

Two different things are called "environment" here, and they are separate
files: `environment.yaml` pins **packages**, `.env` holds **settings**.

#### `backend/.env` — settings and secrets

```bash
make env-file            # Windows: .\make.ps1 env-file
```

Copies `.env.example` to `.env` if it does not already exist. It will **never**
overwrite an existing file — that file holds the only copy of your API key.

`.env` lives in `backend/`, not the repo root. The backend owns its own
configuration so the browser app can own `frontend/.env.local` without the two
fighting over one file:

| | `backend/.env` | `frontend/.env.local` |
|---|---|---|
| Read by | the FastAPI process | Vite, at build time |
| Visible to the browser | no | **yes, always** |
| Safe for secrets | yes | **no** |
| Holds | `UNOROUTER_API_KEY`, `DATABASE_URL`, MinIO keys | `VITE_API_BASE_URL`, `VITE_API_TIMEOUT_MS` |

`app/config.py` resolves it as `BACKEND_DIR / ".env"` — an absolute path
derived from `__file__`. That matters more than it looks: pydantic-settings
resolves a *relative* `env_file` against the current working directory, so a
bare `".env"` would load when uvicorn starts in `backend/` and silently not
load from anywhere else. There is no error when an env file is missing; every
setting just falls back to its default. You would get `llm_configured: false`
with a valid key sitting in the file, and SQLite instead of Postgres.

Anchoring on `__file__` means the working directory is irrelevant:

```bash
cd backend && python -m uvicorn app.main:app     # loads it
cd .. && python -m uvicorn app.main:app          # also loads it
```

`docker-compose.yml` is unaffected by the move — it hardcodes every value and
performs no `${VAR}` interpolation, so it never read the root `.env` in the
first place.

Two more things worth knowing:

- **The shell beats the file.** `DATABASE_URL=... make run` overrides `.env`,
  which is how the test suite and CI inject settings.
- **`extra="ignore"`** lets `.env` carry keys the `Settings` model does not
  declare. Without it, one unrecognised line raises `ValidationError` at
  startup.

Check what actually loaded:

```bash
python doctor.py         # make doctor
curl localhost:8000/api/health
```

#### `environment.yaml` — packages

`environment.yaml` lives in `backend/` and is the **single source of truth**
for dependencies. Run these from `backend/`:

```bash
conda env create -f environment.yaml   # first time
conda activate resume-ai
conda env update -f environment.yaml --prune   # after it changes
```

`make env` and `make env-update` are aliases for those two.

**Conda is the only supported install path.** There is no `requirements.txt`:
one pin list, one place to change it, nothing to keep in sync. Adding a
dependency means editing `environment.yaml` and running `make env-update`.

Two of the pins cannot come from pip alone, which is why the file is a conda
env rather than a pip list: `libpq=17` from conda-forge (so psycopg3 runs in
binary mode) and `numpy` from conda-forge ahead of the pip block.

Two details worth knowing. Both psycopg2 **and** psycopg3 are required and are
different drivers: SQLAlchemy uses psycopg2, while LangGraph's `PostgresSaver`
requires psycopg3. And psycopg3 is installed as `psycopg[binary]`; without the
extra it silently falls back to a pure-Python libpq, which is slower and needs
a system libpq present.

### 2. Services

```bash
make up        # .\make.ps1 up  — compose up -d, then waits for the db
make ps        # service status
make logs      # follow logs
make down      # stop; volumes survive
```

`make up` runs `docker compose` from the repo root for you. Plain
`docker compose up -d` from `..` is equivalent. This is the supported path on
every platform, and the only one on Windows.

<details>
<summary>Linux/macOS without Docker</summary>

`../START-POSTGRES.sh` builds a native Postgres cluster in `~/pgdata` on the
same port 5433. It is idempotent, and repairs the two things that break a
restored cluster (directory permissions, and empty runtime directories that
archiving drops) before starting.

This gives you **Postgres only** — no MinIO and no Redis — so `/api/health`
will report `"storage":"disk"`. That is a supported mode; uploads and exports
go to `../data/<bucket>/` instead.
</details>

The app does **not** fall back to SQLite — `backend/.env` sets
`ALLOW_SQLITE_FALLBACK=false`, so an unreachable Postgres is a hard startup
error rather than a silent downgrade that has you debugging the wrong
database. Object storage *does* still fall back to disk
(`ALLOW_DISK_FALLBACK=true`), because losing a file is recoverable in a way
that writing to the wrong database is not.

### 3. Schema and data

```bash
make db-push-seed    # create tables to match the models, then seed
make db-status       # verify
```

See [Database](#database) for the full command set.

### 4. Run and verify

```bash
make run             # uvicorn --reload on 0.0.0.0:8000
make test            # 57 tests
make lint            # ruff
```

`/api/health` reports which backends are actually live — check it first when
something looks wrong:

```json
{"status":"ok","database":"postgresql","pgvector":true,
 "storage":"minio","llm_configured":false,"model":"...","users":5}
```

| Field | With Compose up | Meaning if different |
|---|---|---|
| `database` | `postgresql` | startup fails instead of falling back |
| `pgvector` | `true` | `false` → vector search uses slow Python cosine |
| `storage` | `minio` | `disk` → MinIO unreachable, using `../data/<bucket>/` |
| `llm_configured` | `false` | `true` once `UNOROUTER_API_KEY` is set in `backend/.env` |
| `users` | `5` | `0` → not seeded yet; `-1` → query failed |

`llm_configured: false` is normal and fully supported: every agent falls back
to its deterministic path, which is why all 57 tests pass without an API key.

## Layout

```
backend/
├── Makefile           short aliases for everything below (Linux/macOS)
├── make.ps1           the same targets for Windows PowerShell
├── .env               real settings and secrets (git-ignored)
├── .env.example       committed template; `make env-file` copies it
├── environment.yaml   conda env spec — the dependency source of truth
├── doctor.py          environment diagnostics (stdlib only)
├── alembic.ini        generated on first `make migrate`
├── migrations/        Alembic env + versions/
└── app/
    ├── cli.py         db push/status/reset + migrate dev/deploy/down
    ├── dbinit.py      schema create/verify/DDL helpers used by the CLI
    ├── config.py      settings; .env anchored to backend/ via BACKEND_DIR
    ├── db.py          engine, pgvector detection, FK pragma, DATA_DIR
    ├── identity.py    X-User-Id -> ?user_id= -> first seeded user
    ├── models.py      11 tables — the schema; JSONB/Vector SQLite variants
    ├── schemas.py     Pydantic request/response models
    ├── api/           controllers — HTTP only
    │   └── deps.py    one Session per request, shared by all repositories
    ├── repositories/  every query; all user-scoped
    ├── services/      business rules, transactions, domain exceptions
    └── ai/            model gateway, agents, LangGraph graphs, vector store
```

## Design rules this code follows

**Repositories never take a bare id.** `get_owned(resume_id, user_id)` is the
only accessor, so scoping is structural rather than remembered. Cross-user
reads return **404, not 403** — a 403 confirms the row exists.

**Services raise domain exceptions, never `HTTPException`.** `main.py`
translates them once (`NotFoundError` → 404, `QuotaExceeded` → 429,
`ValidationError` → 422). Services stay callable from tests, the seed script
and future LangGraph nodes.

**Repositories never commit.** The service owns the unit of work, so accepting
a suggestion — patch JSON, append version, mark status, recompute match — is
one transaction. A suggestion marked accepted whose text never landed is the
worst possible state.

**Scoring is a rule engine, not an LLM.** Same input, same number, every time.
Weights: contact 15 / summary 20 / experience 45 / format 20.

**The critic drops ungrounded drafts.** A suggestion is persisted only if its
`target_ref` resolves *and* `original_text` matches the resume verbatim.

## Verified behaviour

| Gate | Test | Result |
|---|---|---|
| 1 · per-user isolation | `test_isolation.py` | Acme/Globex Kubernetes bullets never cross; cross-user read/write/patch all 404 |
| 3 · score determinism | `test_determinism.py` | 10 runs identical; endpoint stable |
| 4 · grounding | `test_grounding.py` | Unresolvable `target_ref` dropped; accept fails if the target vanishes |
| 5 · keyless operation | `test_api_contract.py` | Full flow with `llm_configured: false` |

Gate 2 (durable interrupt) arrives with the LangGraph layer; the pattern is
verified in `../SETUP-BACKEND.md` §12.

End-to-end over HTTP: seed → analyze (69/100) → tailor (40% match, 6
suggestions) → accept (match rises, version_cursor bumps) → export PDF
(1,799 bytes, text extracts correctly).

## Database

Postgres + pgvector — **16** via `docker-compose.yml`
(`pgvector/pgvector:pg16`), 17 if you install natively. Nothing here is
version-specific; the schema builds identically on both.

The app refuses to start if it cannot reach Postgres
(`ALLOW_SQLITE_FALLBACK=false` in `backend/.env`) — a silent downgrade to SQLite is how
you end up debugging the wrong database.

```bash
make db-push          # create database + tables to match app/models.py
make db-push-seed     # ... and seed
make db-status        # drift: models vs live database
make db-inspect       # tables, columns, row counts
make db-reset         # drop, recreate, push, seed (destructive)
make db-sql           # write ../schema.sql

make migrate m="add users.linkedin_url"   # autogenerate a revision + apply
make migrate-deploy                        # apply pending revisions
make migrate-status                        # current revision + history
make migrate-down                          # roll back one
```

On Windows use `.\make.ps1 <same-target>`, from `backend\`. Both runners are
thin aliases for `python -m app.cli`, which is always available as a fallback:

| Task | make | PowerShell | Direct |
|---|---|---|---|
| create tables | `make db-push` | `.\make.ps1 db-push` | `python -m app.cli db push` |
| + seed | `make db-push-seed` | `.\make.ps1 db-push-seed` | `python -m app.cli db push --seed` |
| check drift | `make db-status` | `.\make.ps1 db-status` | `python -m app.cli db status` |
| new migration | `make migrate m="msg"` | `.\make.ps1 migrate -m "msg"` | `python -m app.cli migrate dev -m "msg"` |
| apply pending | `make migrate-deploy` | `.\make.ps1 migrate-deploy` | `python -m app.cli migrate deploy` |
| roll back | `make migrate-down` | `.\make.ps1 migrate-down` | `python -m app.cli migrate down` |

`--url` targets another database and must come before the subcommand:
`python -m app.cli --url postgresql+psycopg2://.../scratch db push --seed`.

### Which command to use

`db push` runs `create_all`: it creates the database and any missing tables,
and is the fast loop while the shape is still moving. It **cannot** alter an
existing table — adding a column, changing a type or dropping a constraint are
all invisible to it. So `push` finishes by diffing the models against the live
schema (Alembic's autogenerate comparison) and *fails* if anything is left
over, rather than reporting a success it did not achieve.

When that happens, `make migrate m="..."` generates a real revision and applies
it. Migrations are the answer for any change to a table that already exists,
and for anything that has to run on a database with data in it.

### Updating a table

The workflow after editing `app/models.py`, end to end:

```bash
# 1. see what changed
make db-status                            # .\make.ps1 db-status
#    ! 1 drift item(s):
#        - add_column: users.linkedin_url

# 2. generate a revision and apply it
make migrate m="add users.linkedin_url"   # .\make.ps1 migrate -m "add users.linkedin_url"

# 3. confirm
make db-status                            # ✔ In sync with app/models.py.
```

If step 2 fails because the column is `NOT NULL` and the table already has
rows, the CLI prints the fix rather than a traceback. Add a `server_default`
to the generated file in `migrations/versions/`, then:

```bash
make migrate-deploy     # apply the edited revision
make migrate-down       # or roll back one revision
```

A worked example, verified end to end: adding `users.linkedin_url` as
`String(300), nullable=False` produced

```python
op.add_column('users', sa.Column('linkedin_url', sa.String(length=300),
                                 nullable=False, server_default=''))
```

which applied cleanly and backfilled the five existing users with `''`.

Two notes the generator cannot infer for you:

- Adding a `NOT NULL` column to a populated table fails — existing rows have no
  value. Add `server_default=...` to the revision to backfill them. The CLI
  detects this case and prints the fix instead of a traceback.
- The LangGraph checkpoint tables (`checkpoints`, `checkpoint_*`) are created
  by `PostgresSaver`, not by our models. They are excluded from both the drift
  report and autogenerate, otherwise every revision would try to drop them.

Without Docker, `../START-POSTGRES.sh` builds an equivalent cluster natively
on Linux/macOS (`apt-get install postgresql postgresql-17-pgvector`, `initdb`
into `~/pgdata`, port 5433, then creates the `resumeai` database).

`init_db()` runs `CREATE EXTENSION IF NOT EXISTS vector` then `create_all`, so
the schema is applied on first boot. `app/models.py` is the source of truth.

`../schema.sql` is **generated output, and nothing reads it.** It is not an
init script — the schema reaches the database through `create_all()` and
Alembic, never by running that file. It exists so the shape of the database is
reviewable in a diff without a running server, which is also why `make db-sql`
works while Postgres is down. Regenerate it after any model change; it is safe
to delete if you do not want it.

**15 tables:** 11 application tables (`users`, `templates`, `resumes`,
`resume_versions`, `analysis_reports`, `job_descriptions`, `tailoring_sessions`,
`suggestions`, `chat_messages`, `vector_docs`, `agent_runs`) plus the four
LangGraph checkpoint tables (`checkpoints`, `checkpoint_blobs`,
`checkpoint_writes`, `checkpoint_migrations`).

Keeping the checkpoints in the same database matters: a paused tailoring run is
recoverable by *any* worker, and one `pg_dump` captures the whole application
state. On Postgres the graph uses `PostgresSaver` (its own psycopg3 pool, since
SQLAlchemy here uses psycopg2); it falls back to `SqliteSaver` otherwise.

Seeding is idempotent and produces 5 users, 5 templates, 1 demo resume, 1
sample JD and **35 vector docs** — 18 skill-taxonomy, 10 ATS-rule and 7 resume
bullets. The global corpora matter: without them retrieval returns nothing and
the agents run ungrounded. Re-running re-indexes rather than duplicating.

Two invariants are enforced by the database itself, not just application code:

- `ck_vector_docs_scope` — global corpora (`skill_taxonomy`, `ats_rules`) must
  have `user_id IS NULL`; every other corpus must have a `user_id`.
- `ON DELETE CASCADE` from `users` and `resumes`.

`embedding` is a native `vector(384)`, so search uses the pgvector `<=>`
operator. `EXPLAIN` confirms the user scoping runs as an `Index Cond` feeding
the sort — the filter is applied *before* ranking, so cross-user leakage is not
possible even in principle.

## Troubleshooting

**`UndefinedColumn: column "kind" of relation "resumes" does not exist`**

Schema drift. Your database was created before a column was added to
`app/models.py` — commonly `kind` / `parent_id` / `tailored_for`, which arrived
with resume forking.

`db push` **cannot** fix this. It runs `create_all`, which only ever adds
missing *tables*; it will not add a column to a table that already exists. That
is why the table looks fine and the INSERT still fails.

```bash
make db-status      # .\make.ps1 db-status  — lists the drift
make db-reset       # .\make.ps1 db-reset -Force  — DROPS ALL DATA, reseeds
```

This project resets rather than migrating for model changes, so `db-reset` is
the expected route and the demo data is regenerated by the seeder. If you have
data you actually care about, generate a revision instead:

```bash
python -m app.cli migrate dev -m "add resume fork columns"
```

**`ModuleNotFoundError` for a package `conda list` says is installed**

The prompt reads `(resume-ai)` but `python` is resolving to a different
interpreter — usually base Anaconda, when the installer added it to the system
PATH. Packages installed into the env are then invisible.

The giveaway is in the traceback: every frame sits under the *base* prefix
rather than the env.

```
File "C:\ProgramData\anaconda3\Lib\site-packages\uvicorn\...
                ^^^^^^^^^^^^^^^^^^^ base, not C:\Users\<you>\.conda\envs\resume-ai
```

Confirm it:

```powershell
.\make.ps1 which-python      # make which-python
```

```
Interpreter
-----------
exe          C:\ProgramData\anaconda3\python.exe
prefix       C:\ProgramData\anaconda3
CONDA_PREFIX C:\Users\you\.conda\envs\resume-ai     <-- disagrees

Key packages
------------
  [FAIL] fastapi      MISSING
  [ok]   uvicorn      OK                              <-- base has some, not all
```

If `prefix` and `CONDA_PREFIX` disagree, that is the bug. **Do not reinstall
anything** — the packages are fine, the interpreter is wrong.

The task runners now prefer `$CONDA_PREFIX` over PATH, so `make` / `make.ps1`
targets pick the right interpreter on their own. To fix your shell generally:

```powershell
conda init powershell          # then open a NEW terminal
conda activate resume-ai
```

Or bypass the shell entirely:

```powershell
conda run -n resume-ai --no-capture-output python -m uvicorn app.main:app --reload
```

Note that `conda activate` inside a script does not affect the parent shell,
and on Windows PowerShell it is a no-op unless `conda init powershell` has been
run at least once.

**Settings are all at their defaults and there is no error**

A missing or unfound `.env` is silent by design — every field falls back to its
default and the app boots happily. Symptoms: `llm_configured: false` with a key
in the file, or SQLite when you expected Postgres.

Check that the file is where the code looks for it:

```bash
cd backend
ls -l .env                      # must be HERE, not in the repo root
python doctor.py
```

If you have just pulled a change that moved `.env` into `backend/`, a stale
copy at the repo root is now ignored. Move it:

```bash
mv ../.env .env                 # Windows: move ..\.env .env
```

**`The term '.\make.ps1' is not recognized` (PowerShell)**

You are in the repo root; the script is one level down. Change into the
backend directory first — it is where every target expects to run:

```powershell
cd backend
.\make.ps1 db-push-seed
```

`Get-Location` confirms where you are. Two related cases: PowerShell will not
run `make.ps1` without the leading `.\`, and if it refuses with a *"running
scripts is disabled"* error, either unblock the session with
`Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` or skip the
wrapper entirely — `python -m app.cli db push --seed` is the same command.

**`Cannot find path ...\db because it does not exist` / `Clear-Item` errors**

A stale copy of `make.ps1` from before the helper function was renamed. `cli`
is a built-in PowerShell alias for `Clear-Item`, and PowerShell resolves
aliases *before* functions, so a function named `Cli` is never reached — every
target silently ran `Clear-Item` on its first argument instead. The function is
now `Invoke-Cli`. Pull the current `make.ps1`, or use the CLI directly:
`python -m app.cli db push --seed`.

**`ModuleNotFoundError: No module named 'pydantic_core._pydantic_core'`**

A broken install, not a code problem. `pydantic_core` ships a compiled
extension (`.pyd` on Windows); Python found the Python package but not its
native module. Usually a half-finished install, an interrupted download, or a
cached wheel built for a different Python version. Reinstall both halves
together — the versions are tightly coupled:

```powershell
pip install --force-reinstall --no-cache-dir pydantic==2.13.5 pydantic-core==2.46.5
python -c "import pydantic; print(pydantic.VERSION)"
```

`--no-cache-dir` matters: without it pip reuses the same bad wheel. If it
still fails, the environment itself is suspect — rebuild it:
`conda env remove -n resume-ai` then `conda env create -f environment.yaml`.

Do not pin `pydantic-core` in `environment.yaml`. It is resolved by `pydantic`
and pinning both invites a conflict on the next upgrade.

**Anything failing before the app starts — run the doctor first**

```powershell
.\make.ps1 doctor          # or: python doctor.py
```

It checks the interpreter, every compiled extension (the usual culprit), the
pure-Python imports, how `DATABASE_URL` actually parses, and whether anything
is listening on the port — then prints the exact fix for whatever is broken.
It uses only the standard library, so it still runs when pydantic or psycopg2
are the things that are broken.

**`No module named 'psycopg2._psycopg'`**

Same class of problem as the `pydantic_core` entry above: `psycopg2-binary`
ships a compiled extension and this one is missing. It is *not* a connectivity
failure, despite the "Cannot reach the server" wrapper:

```powershell
pip install --force-reinstall --no-cache-dir psycopg2-binary==2.9.13
python -c "import psycopg2; print(psycopg2.__version__)"
```

Never install plain `psycopg2` (no `-binary`) here — it compiles from source
and needs a C toolchain plus libpq headers.

**`Cannot reach the server at <something>@localhost:5433`**

If the host in that message contains an `@`, the DSN was mis-parsed, not the
server unreachable. An unencoded `@` in the password splits
`DATABASE_URL` at the wrong place — the parser takes the *last* `@` as the
delimiter, so the password tail becomes part of the hostname:

```
postgresql+psycopg2://admin:Admin@123@localhost:5433/resumeai
                                ^ password ends here; host = "123@localhost"
```

Percent-encode it as `%40`:

```
postgresql+psycopg2://admin:Admin%40123@localhost:5433/resumeai
```

The password in `backend/.env` must also match `POSTGRES_PASSWORD` in
`docker-compose.yml` — which is `Admin123`, with no `@` at all. If your DSN has
one, that is the more likely mistake.

**`connection refused` on port 5433 / `db-push` fails**

Postgres is not up yet, or not up at all. `docker compose up -d` returns as
soon as containers are *created*, not when Postgres is ready to accept
connections:

```bash
docker compose ps             # db should be "healthy", not "starting"
docker compose logs db
docker compose up -d db --wait   # or block until the db healthcheck passes
```

The app will not silently fall back to SQLite, so this surfaces as a hard
error rather than a confusing empty database.

**Port 5433 or 9000 already allocated**

Something else holds the port. Either stop it, or change the *host* side of
the mapping in `docker-compose.yml` (`"5434:5432"`) and update `DATABASE_URL`
in `backend/.env` to match. Only the left number is yours to change.

**`password authentication failed for user "admin"`**

`backend/.env` and `docker-compose.yml` disagree. They must match exactly:
`admin` / `Admin123` / `resumeai`. If you edited the Postgres credentials after
the first `up`, the old password is baked into the `pgdata` volume — recreate
it with `docker compose down -v` (this deletes all data).

Also note any `@ : / ? # [ ]` in a password must be percent-encoded in
`DATABASE_URL`; an unencoded `@` parses as a host separator and the error
blames the host, not the password.

**`/api/health` says `"storage":"disk"` while Compose is running**

The app could not reach MinIO at `S3_ENDPOINT_URL`. From the host that is
`http://localhost:9000`. Check `docker compose logs minio`, and confirm
`minio-init` printed `buckets ready`.

**`minio-init` shows `Exited (0)`**

That is correct — it is a one-shot job that creates the buckets and stops.

**`pgvector: false` in `/api/health`**

The `vector` extension is missing. The `pgvector/pgvector:pg16` image ships
it and `db push` runs `CREATE EXTENSION IF NOT EXISTS vector`. If you pointed
`DATABASE_URL` at a plain `postgres:16` image instead, vector search silently
degrades to Python cosine — switch images and re-run `make db-push`.

**Reset everything**

```bash
docker compose down -v     # deletes Postgres, MinIO and Redis volumes
docker compose up -d
docker compose ps          # wait for "healthy"
make db-push-seed
```

## The AI layer

`app/ai/` is built and wired into `analysis_service`, `tailoring_service` and
`chat_service`. It runs with or without an LLM key: every agent has a
deterministic fallback, which is why all 57 tests pass with
`llm_configured: false`.

| Module | Role |
|---|---|
| `llm.py` | Model gateway. Owns retry and the fallback chain, returns a `CallResult` carrying latency and token counts for `agent_runs`. |
| `embeddings.py` | Remote embeddings first, local blake2b hashed vectors on any failure. Never raises. |
| `vectorstore.py` | Per-user scoped retrieval. pgvector when available, Python cosine otherwise. |
| `corpora.py` | 18 skill-taxonomy and 10 ATS-rule entries — the global grounding corpora. |
| `agents.py` | The six agents: JD analyst, scoring, writer, critic, chat, plus shared retrieval. |
| `graphs.py` | The analysis graph and the tailor graph with the durable interrupt. |

### The two graphs

```
analysis:  RetrieveATS -> RuleEngine -> ScoringAgent
tailor:    ParseJD -> ExtractKeywords -> RetrieveResumeContext
             -> GenerateSuggestions -> CriticReview
             -> (revise, max 2) or AwaitUserReview
             -> RecomputeMatchScore
```

`AwaitUserReview` is a real LangGraph `interrupt`. The pause is checkpointed
to SQLite, so the run survives the gap between the tailor request and the
Accept/Reject/Edit request that arrives minutes later — including a restart or
a different worker. `tests/test_ai_layer.py` proves this by dropping the
connection and resuming from a rebuilt graph.

### What keeps the output honest

The rule engine owns every number; the scoring prompt is forbidden from
recomputing them, so the score is reproducible. The critic then runs two hard
checks that apply with or without a key: the `target_ref` must resolve against
the live resume, and `original_text` must match it verbatim. Drafts that
invent a metric, invent a claim, or point at a bullet that does not exist are
dropped before they are persisted. The chat fallback was rewritten to obey the
same rule — it no longer appends an invented "30%".

## Not yet built

The React frontend. The backend contract it consumes is stable: 24 endpoints,
`GET /openapi.json` is the source of truth for client generation.
