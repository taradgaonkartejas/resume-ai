"""ResumeAI schema CLI — the Prisma workflow, driven by the SQLAlchemy models.

`app/models.py` is the schema (Prisma's `schema.prisma`). These commands read
it and reconcile the database against it.

    python -m app.cli db push          # create database + tables to match models
    python -m app.cli db status        # drift: models vs live database
    python -m app.cli db seed          # run the seeder
    python -m app.cli db reset         # drop, recreate, push, seed
    python -m app.cli db create|drop   # database only, no tables
    python -m app.cli db sql           # print/write the DDL, no database needed
    python -m app.cli db inspect       # tables, columns, row counts

    python -m app.cli migrate dev -m "add x"   # autogenerate + apply a revision
    python -m app.cli migrate deploy           # apply pending revisions
    python -m app.cli migrate status           # current revision + pending

Prisma equivalents: `db push`, `migrate dev`, `migrate deploy`, `db seed`,
`migrate reset`, `migrate status`.

Nothing here imports app.db at module scope. app.db opens a connection when it
is imported, which would fail before the database exists — `db create` has to
run first, so every import is deferred into the command that needs it.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
ALEMBIC_DIR = BACKEND_DIR / "migrations"
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"

GREEN, RED, YELLOW, DIM, RESET = (
    "\033[32m",
    "\033[31m",
    "\033[33m",
    "\033[2m",
    "\033[0m",
)


def ok(msg: str) -> None:
    print(f"{GREEN}✔{RESET} {msg}")


def warn(msg: str) -> None:
    print(f"{YELLOW}!{RESET} {msg}")


def fail(msg: str) -> None:
    print(f"{RED}✘{RESET} {msg}")


def dim(msg: str) -> None:
    print(f"{DIM}{msg}{RESET}")


# --------------------------------------------------------------------- URLs
def _url(override: str | None = None):
    """The configured database URL, without importing app.db.

    Reads the environment first: main() puts --url there before any import, and
    that is the value app.db will actually use.
    """
    from sqlalchemy.engine import make_url

    from app.config import settings

    return make_url(override or os.environ.get("DATABASE_URL") or settings.database_url)


def _safe(url) -> str:
    return url.render_as_string(hide_password=True)


def _admin_url(url):
    """Same server, but the maintenance database.

    You cannot CREATE DATABASE while connected to the database you are
    creating, so DDL at that level runs against `postgres`.
    """
    return url.set(database="postgres")


def _is_postgres(url) -> bool:
    return url.get_backend_name() == "postgresql"


# ----------------------------------------------------------- database DDL
def _database_exists(url) -> bool:
    import psycopg2

    admin = _admin_url(url)
    conn = psycopg2.connect(
        host=admin.host,
        port=admin.port,
        user=admin.username,
        password=admin.password,
        dbname=admin.database,
        connect_timeout=5,
    )
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (url.database,))
        return cur.fetchone() is not None
    finally:
        conn.close()


def _run_admin_sql(url, sql: str, params: tuple = ()) -> None:
    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    admin = _admin_url(url)
    conn = psycopg2.connect(
        host=admin.host,
        port=admin.port,
        user=admin.username,
        password=admin.password,
        dbname=admin.database,
        connect_timeout=5,
    )
    try:
        # CREATE/DROP DATABASE cannot run inside a transaction block.
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        conn.cursor().execute(sql, params)
    finally:
        conn.close()


def cmd_create(args) -> int:
    url = _url(args.url)
    if not _is_postgres(url):
        warn(f"{url.get_backend_name()} needs no CREATE DATABASE — skipping.")
        return 0
    try:
        if _database_exists(url):
            ok(f"Database {url.database!r} already exists.")
            return 0
    except Exception as exc:
        fail(f"Cannot reach the server at {url.host}:{url.port} — {exc}")
        dim("Is Postgres running?  ./START-POSTGRES.sh   or   docker compose up -d")
        return 1

    # The database name is an identifier, so it cannot be a bound parameter.
    # quote_ident round-trips it through the server's own quoting rules.
    _run_admin_sql(url, f'CREATE DATABASE "{url.database}"')
    ok(f"Created database {url.database!r}.")
    return 0


def cmd_drop(args) -> int:
    url = _url(args.url)
    if not _is_postgres(url):
        warn("Not Postgres — nothing to drop.")
        return 0
    if not _database_exists(url):
        ok(f"Database {url.database!r} does not exist.")
        return 0
    if not args.force and not _confirm(f"DROP DATABASE {url.database!r}. Sure?"):
        return 1
    # Terminate other sessions, or the DROP blocks forever.
    _run_admin_sql(
        url,
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        "WHERE datname = %s AND pid <> pg_backend_pid()",
        (url.database,),
    )
    _run_admin_sql(url, f'DROP DATABASE IF EXISTS "{url.database}"')
    ok(f"Dropped database {url.database!r}.")
    return 0


def _confirm(question: str) -> bool:
    if not sys.stdin.isatty():
        fail(f"{question} Refusing in a non-interactive shell — pass --force.")
        return False
    return input(f"{YELLOW}?{RESET} {question} [y/N] ").strip().lower() in {"y", "yes"}


# --------------------------------------------------------------- db push
def cmd_push(args) -> int:
    """Create the database if missing, then make tables match the models."""
    url = _url(args.url)
    if _is_postgres(url) and cmd_create(args) != 0:
        return 1

    from app.db import DB_BACKEND, engine, has_pgvector, init_db

    print(f"  datasource : {_safe(_url(args.url))}")
    print(f"  backend    : {DB_BACKEND}")

    before = _table_names(engine)
    init_db()
    after = _table_names(engine)

    if has_pgvector():
        ok("Extension 'vector' ready.")
    elif DB_BACKEND == "postgresql":
        warn("Extension 'vector' unavailable — search falls back to Python cosine.")

    new = sorted(set(after) - set(before))
    if new:
        ok(f"Created {len(new)} table(s): {', '.join(new)}")
    else:
        ok(f"{len(after)} table(s) already in sync.")

    drift = _drift(engine)
    if drift:
        warn(f"{len(drift)} change(s) in the models are NOT in the database:")
        for line in drift:
            print(f"    - {line}")
        dim("create_all only adds missing tables; it never alters existing ones.")
        dim('Use: python -m app.cli migrate dev -m "describe the change"')
        return 1

    ok("Database is in sync with app/models.py.")
    if args.seed:
        return cmd_seed(args)
    return 0


def _table_names(engine) -> list[str]:
    from sqlalchemy import inspect

    return sorted(inspect(engine).get_table_names())


def _drift(engine) -> list[str]:
    """Differences between the models and the live schema.

    This is Alembic's autogenerate comparison used directly — the same engine
    behind `prisma migrate diff`, so it reports column/index/constraint changes
    that create_all would silently ignore.
    """
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from app import models  # noqa: F401  register tables
    from app.db import Base

    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        diffs = compare_metadata(ctx, Base.metadata)

    out: list[str] = []
    for d in diffs:
        if _touches_external(d):
            continue
        out.append(_describe(d))
    return out


# Tables LangGraph's checkpointer and Alembic create and own. Our models say
# nothing about them, so autogenerate sees them as "extra" and wants to DROP
# them. Filtering here is what stops `db status` from reporting permanent,
# unfixable drift.
EXTERNAL_TABLES = {
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
    "checkpoint_migrations",
    "alembic_version",
}


def _table_of(diff) -> str:
    """Best-effort table name for any autogenerate diff tuple."""
    if isinstance(diff, list):
        return _table_of(diff[0]) if diff else ""
    kind = diff[0]
    if kind in {"add_table", "remove_table"}:
        return getattr(diff[1], "name", "")
    if kind in {"add_column", "remove_column"}:
        return diff[2] or ""
    if kind in {"add_index", "remove_index", "add_constraint", "remove_constraint"}:
        obj = diff[1]
        table = getattr(obj, "table", None)
        return getattr(table, "name", "") or ""
    if kind.startswith("modify_"):
        return diff[2] or ""
    return ""


def _touches_external(diff) -> bool:
    if isinstance(diff, list):
        return any(_touches_external(d) for d in diff)
    return _table_of(diff) in EXTERNAL_TABLES


def _describe(diff) -> str:
    if isinstance(diff, list):
        return "; ".join(_describe(d) for d in diff)
    kind = diff[0]
    if kind in {"add_table", "remove_table"}:
        return f"{kind}: {diff[1].name}"
    if kind in {"add_column", "remove_column"}:
        return f"{kind}: {diff[2]}.{diff[3].name}"
    if kind == "modify_nullable":
        return f"modify_nullable: {diff[2]}.{diff[3]} -> {diff[6]}"
    if kind == "modify_type":
        return f"modify_type: {diff[2]}.{diff[3]}: {diff[5]} -> {diff[6]}"
    if kind in {"add_index", "remove_index", "add_constraint", "remove_constraint"}:
        return f"{kind}: {getattr(diff[1], 'name', diff[1])}"
    return str(kind)


# --------------------------------------------------------------- db status
def cmd_status(args) -> int:
    url = _url(args.url)
    try:
        if _is_postgres(url) and not _database_exists(url):
            fail(f"Database {url.database!r} does not exist.")
            dim("Run: python -m app.cli db push")
            return 1
    except Exception as exc:
        fail(f"Cannot reach the server — {exc}")
        return 1

    from app.db import DB_BACKEND, engine, has_pgvector

    print(f"  datasource : {_safe(url)}")
    print(f"  backend    : {DB_BACKEND}")
    print(f"  pgvector   : {has_pgvector()}")

    from app.dbinit import EXPECTED_TABLES

    present = _table_names(engine)
    missing = [t for t in EXPECTED_TABLES if t not in present]
    owned = len(EXPECTED_TABLES) - len(missing)
    external = sorted(set(present) & EXTERNAL_TABLES)
    print(f"  tables     : {owned}/{len(EXPECTED_TABLES)} from app/models.py")
    if external:
        print(f"  external   : {len(external)} ({', '.join(external)})")
    if missing:
        fail(f"missing: {', '.join(missing)}")

    drift = _drift(engine)
    if drift:
        warn(f"{len(drift)} drift item(s):")
        for line in drift:
            print(f"    - {line}")
    if missing or drift:
        dim("Run: python -m app.cli db push")
        return 1
    ok("In sync with app/models.py.")
    return 0


# ----------------------------------------------------------------- db seed
def cmd_seed(args) -> int:
    from app.db import SessionLocal
    from app.services.seed import seed

    db = SessionLocal()
    try:
        counts = seed(db)
    finally:
        db.close()
    for key, value in counts.items():
        print(f"  {key:16s} +{value}")
    ok("Seed complete.")
    return 0


# ---------------------------------------------------------------- db reset
def cmd_reset(args) -> int:
    url = _url(args.url)
    if not args.force and not _confirm(
        f"This DELETES ALL DATA in {url.database!r}. Continue?"
    ):
        return 1
    args.force = True
    if _is_postgres(url):
        if cmd_drop(args) != 0:
            return 1
        if cmd_create(args) != 0:
            return 1
    else:
        from app import models  # noqa: F401
        from app.db import Base, engine

        Base.metadata.drop_all(engine)
        ok("Dropped all tables.")

    args.seed = True
    return cmd_push(args)


# ------------------------------------------------------------------ db sql
def cmd_sql(args) -> int:
    from app.dbinit import write_sql

    if args.out:
        return write_sql(args.out)
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateIndex, CreateTable

    from app import models  # noqa: F401
    from app.db import Base

    pg = postgresql.dialect()
    print("CREATE EXTENSION IF NOT EXISTS vector;\n")
    for table in Base.metadata.sorted_tables:
        print(str(CreateTable(table).compile(dialect=pg)).strip() + ";")
        for idx in sorted(table.indexes, key=lambda i: i.name or ""):
            print(str(CreateIndex(idx).compile(dialect=pg)).strip() + ";")
        print()
    return 0


# -------------------------------------------------------------- db inspect
def cmd_inspect(args) -> int:
    from sqlalchemy import func, inspect, select

    from app import models  # noqa: F401
    from app.db import Base, SessionLocal, engine

    insp = inspect(engine)
    tables = _table_names(engine)
    by_name = {t.name: t for t in Base.metadata.sorted_tables}

    db = SessionLocal()
    try:
        print(f"{'table':24s} {'cols':>4s} {'idx':>4s} {'fks':>4s} {'rows':>8s}")
        print("-" * 48)
        for name in tables:
            cols = len(insp.get_columns(name))
            idxs = len(insp.get_indexes(name))
            fks = len(insp.get_foreign_keys(name))
            rows = "-"
            table = by_name.get(name)
            if table is not None:
                rows = str(db.scalar(select(func.count()).select_from(table)))
            owned = "" if table is not None else f"  {DIM}(external){RESET}"
            print(f"{name:24s} {cols:4d} {idxs:4d} {fks:4d} {rows:>8s}{owned}")
    finally:
        db.close()
    return 0


# ------------------------------------------------------------------ alembic
def _ensure_alembic() -> None:
    """Scaffold migrations/ on first use, wired to our metadata and URL."""
    if ALEMBIC_INI.exists() and (ALEMBIC_DIR / "env.py").exists():
        return

    (ALEMBIC_DIR / "versions").mkdir(parents=True, exist_ok=True)

    ALEMBIC_INI.write_text(
        "# Generated by app/cli.py. The URL is resolved in migrations/env.py\n"
        "# from app.config so there is exactly one source of truth.\n"
        "[alembic]\n"
        "script_location = migrations\n"
        "prepend_sys_path = .\n"
        "\n[loggers]\nkeys = root\n"
        "\n[handlers]\nkeys = console\n"
        "\n[formatters]\nkeys = generic\n"
        "\n[logger_root]\nlevel = WARNING\nhandlers = console\nqualname =\n"
        "\n[handler_console]\nclass = StreamHandler\nargs = (sys.stderr,)\n"
        "level = NOTSET\nformatter = generic\n"
        "\n[formatter_generic]\nformat = %(levelname)-5.5s [%(name)s] %(message)s\n"
    )

    (ALEMBIC_DIR / "env.py").write_text('''"""Alembic environment — generated by app/cli.py.

The URL comes from app.config, and target_metadata from app.models, so
migrations and the running app can never disagree about either.
"""

from alembic import context
from sqlalchemy import engine_from_config, pool

from app import models  # noqa: F401  register tables
from app.config import settings
from app.db import Base

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)
target_metadata = Base.metadata

# Tables owned by LangGraph's checkpointer, not by our models. Without this,
# autogenerate would emit a DROP for each one on every revision.
EXTERNAL = {
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
    "checkpoint_migrations",
}


def include_object(obj, name, type_, reflected, compare_to):
    if type_ == "table" and name in EXTERNAL:
        return False
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        include_object=include_object,
        compare_type=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
''')

    (ALEMBIC_DIR / "script.py.mako").write_text(
        '"""${message}\n\n'
        "Revision ID: ${up_revision}\n"
        "Revises: ${down_revision | comma,n}\n"
        "Create Date: ${create_date}\n"
        '"""\n\n'
        "from alembic import op\n"
        "import sqlalchemy as sa\n"
        "${imports if imports else ''}\n\n"
        "revision = ${repr(up_revision)}\n"
        "down_revision = ${repr(down_revision)}\n"
        "branch_labels = ${repr(branch_labels)}\n"
        "depends_on = ${repr(depends_on)}\n\n\n"
        "def upgrade() -> None:\n    ${upgrades if upgrades else 'pass'}\n\n\n"
        "def downgrade() -> None:\n    ${downgrades if downgrades else 'pass'}\n"
    )
    ok("Scaffolded migrations/ (alembic.ini, env.py, versions/).")


def _alembic(*argv: str) -> int:
    _ensure_alembic()
    return subprocess.call(
        [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_INI), *argv],
        cwd=str(BACKEND_DIR),
    )


def cmd_migrate_dev(args) -> int:
    """Autogenerate a revision from model changes, then apply it."""
    url = _url(args.url)
    if _is_postgres(url) and cmd_create(args) != 0:
        return 1

    before = _revision_files()
    rc = _alembic("revision", "--autogenerate", "-m", args.message)
    if rc != 0:
        return rc

    new = sorted(set(_revision_files()) - set(before))
    if new:
        ok(f"Generated {new[0].name}")

    rc = _alembic("upgrade", "head")
    if rc != 0:
        fail("The revision was generated but could not be applied.")
        if new:
            _explain_upgrade_failure(new[0])
            dim(f"Edit {new[0].relative_to(BACKEND_DIR)}, then: "
                f"python -m app.cli migrate deploy")
            dim(f"Or discard it:  rm {new[0].relative_to(BACKEND_DIR)}")
        return rc

    ok("Migration applied.")
    return 0


def _revision_files() -> list[Path]:
    versions = ALEMBIC_DIR / "versions"
    return sorted(versions.glob("*.py")) if versions.exists() else []


def _explain_upgrade_failure(revision: Path) -> None:
    """Turn the two failures autogenerate reliably causes into instructions."""
    body = revision.read_text()
    if "nullable=False" in body and "server_default" not in body:
        warn(
            "A NOT NULL column cannot be added to a table that already has "
            "rows — existing rows would have no value."
        )
        dim("Fix: give the column a server_default in the revision, e.g.")
        dim("     sa.Column('x', sa.String(), nullable=False,")
        dim("               server_default='')")
        dim("(a server_default backfills existing rows; the model keeps its")
        dim(" Python-side default for new ones)")
    elif "drop_column" in body or "drop_table" in body:
        warn("This revision drops something. Check it is intentional.")


def cmd_migrate_deploy(args) -> int:
    rc = _alembic("upgrade", "head")
    if rc == 0:
        ok("Database at head.")
    return rc


def cmd_migrate_status(args) -> int:
    _ensure_alembic()
    print("current:")
    _alembic("current", "--verbose")
    print("\nhistory:")
    return _alembic("history", "--indicate-current")


def cmd_migrate_down(args) -> int:
    return _alembic("downgrade", args.to)


# --------------------------------------------------------------------- main
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m app.cli",
        description="Schema management for ResumeAI (app/models.py is the schema).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--url", help="override DATABASE_URL for this command")
    sub = p.add_subparsers(dest="group", required=True)

    db = sub.add_parser("db", help="database and table management").add_subparsers(
        dest="cmd", required=True
    )

    sp = db.add_parser("push", help="create database + tables to match the models")
    sp.add_argument("--seed", action="store_true", help="seed after pushing")
    sp.set_defaults(func=cmd_push)

    db.add_parser("create", help="create the database only").set_defaults(
        func=cmd_create
    )

    sp = db.add_parser("drop", help="drop the database")
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(func=cmd_drop)

    sp = db.add_parser("reset", help="drop, recreate, push, seed")
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(func=cmd_reset)

    db.add_parser("seed", help="run the seeder").set_defaults(func=cmd_seed)
    db.add_parser("status", help="report drift between models and database").set_defaults(
        func=cmd_status
    )
    db.add_parser("inspect", help="tables, columns, row counts").set_defaults(
        func=cmd_inspect
    )

    sp = db.add_parser("sql", help="print the DDL")
    sp.add_argument("--out", nargs="?", const="schema.sql", help="write to a file")
    sp.set_defaults(func=cmd_sql)

    mig = sub.add_parser("migrate", help="versioned migrations").add_subparsers(
        dest="cmd", required=True
    )
    sp = mig.add_parser("dev", help="autogenerate a revision and apply it")
    sp.add_argument("-m", "--message", required=True)
    sp.set_defaults(func=cmd_migrate_dev)
    mig.add_parser("deploy", help="apply pending revisions").set_defaults(
        func=cmd_migrate_deploy
    )
    mig.add_parser("status", help="current revision and history").set_defaults(
        func=cmd_migrate_status
    )
    sp = mig.add_parser("down", help="downgrade")
    sp.add_argument("to", nargs="?", default="-1")
    sp.set_defaults(func=cmd_migrate_down)

    return p


def main() -> int:
    args = build_parser().parse_args()
    if not hasattr(args, "seed"):
        args.seed = False
    if not hasattr(args, "force"):
        args.force = False

    # app.db builds its engine from settings at import time, so --url has to be
    # in the environment BEFORE anything imports it. Setting it afterwards
    # leaves the CLI reporting one database while operating on another.
    if args.url:
        if "app.db" in sys.modules:  # pragma: no cover - defensive
            fail("app.db was imported before --url was applied.")
            return 1
        os.environ["DATABASE_URL"] = args.url

    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
