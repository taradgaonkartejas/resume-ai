"""Database bootstrap: create the schema, seed it, and report what exists.

    python -m app.dbinit            # create tables + seed + verify
    python -m app.dbinit --verify   # report only, change nothing
    python -m app.dbinit --reset    # DROP everything, then recreate and seed
    python -m app.dbinit --sql      # write schema.sql (Postgres DDL), no DB needed

Creating the schema is idempotent: create_all only adds what is missing, and
the seed skips rows that already exist.
"""

from __future__ import annotations

import argparse
import sys

EXPECTED_TABLES = [
    "users",
    "templates",
    "resumes",
    "resume_versions",
    "analysis_reports",
    "job_descriptions",
    "tailoring_sessions",
    "suggestions",
    "chat_messages",
    "vector_docs",
    "agent_runs",
]


def _inspect() -> tuple[list[str], list[str]]:
    from sqlalchemy import inspect

    from app.db import engine

    found = sorted(inspect(engine).get_table_names())
    missing = [t for t in EXPECTED_TABLES if t not in found]
    return found, missing


def _banner() -> None:
    from app.db import DB_BACKEND, HAS_PGVECTOR, engine

    url = engine.url
    shown = url.render_as_string(hide_password=True)
    print(f"backend   : {DB_BACKEND}")
    print(f"url       : {shown}")
    print(f"pgvector  : {HAS_PGVECTOR}")
    if DB_BACKEND == "sqlite":
        print(
            "note      : Postgres was unreachable, so the SQLite fallback is in "
            "use.\n            Start docker compose for the real database."
        )


def verify() -> int:
    _banner()
    found, missing = _inspect()
    print(f"\ntables    : {len(found)}/{len(EXPECTED_TABLES)}")
    for t in EXPECTED_TABLES:
        print(f"  {'OK ' if t in found else 'MISSING'}  {t}")
    extra = [t for t in found if t not in EXPECTED_TABLES]
    if extra:
        print(f"  (also present: {', '.join(extra)})")

    if missing:
        print(f"\nMISSING {len(missing)} table(s). Run: python -m app.dbinit")
        return 1

    from sqlalchemy import func, select

    from app.db import SessionLocal
    from app.models import Resume, Template, User, VectorDoc

    db = SessionLocal()
    try:
        print("\nrows")
        print(f"  users       : {db.scalar(select(func.count()).select_from(User))}")
        print(f"  templates   : {db.scalar(select(func.count()).select_from(Template))}")
        print(f"  resumes     : {db.scalar(select(func.count()).select_from(Resume))}")
        total = db.scalar(select(func.count()).select_from(VectorDoc))
        print(f"  vector_docs : {total}")
        if total:
            by_corpus = db.execute(
                select(VectorDoc.corpus, func.count()).group_by(VectorDoc.corpus)
            ).all()
            for corpus, n in sorted(by_corpus):
                print(f"      {corpus:16s} {n}")
    finally:
        db.close()
    print("\nSchema OK.")
    return 0


def create(reset: bool = False) -> int:
    from app.db import Base, engine, init_db

    _banner()
    if reset:
        from app import models  # noqa: F401  register tables before dropping

        print("\ndropping all tables...")
        Base.metadata.drop_all(engine)

    print("\ncreating schema...")
    init_db()
    found, missing = _inspect()
    print(f"created   : {len(found)} tables")
    if missing:
        print(f"ERROR: still missing {missing}")
        return 1

    print("\nseeding...")
    from app.db import SessionLocal
    from app.services.seed import seed

    db = SessionLocal()
    try:
        counts = seed(db)
    finally:
        db.close()
    for key, value in counts.items():
        print(f"  {key:16s} +{value}")
    print()
    return verify()


def write_sql(path: str = "schema.sql") -> int:
    """Emit the Postgres DDL without needing a live database."""
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateIndex, CreateTable

    from app import models  # noqa: F401
    from app.db import Base

    pg = postgresql.dialect()
    out = [
        "-- ResumeAI schema (PostgreSQL 16 + pgvector)",
        "-- Generated from app/models.py by `python -m app.dbinit --sql`.",
        "-- The ORM is the source of truth; init_db() applies this automatically.",
        "",
        "CREATE EXTENSION IF NOT EXISTS vector;",
        "",
    ]
    for table in Base.metadata.sorted_tables:
        out.append(str(CreateTable(table).compile(dialect=pg)).strip() + ";")
        for idx in sorted(table.indexes, key=lambda i: i.name or ""):
            out.append(str(CreateIndex(idx).compile(dialect=pg)).strip() + ";")
        out.append("")

    with open(path, "w") as fh:
        fh.write("\n".join(out))
    print(f"wrote {path} ({len(Base.metadata.sorted_tables)} tables)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Create and verify the ResumeAI schema.")
    ap.add_argument("--verify", action="store_true", help="report only")
    ap.add_argument("--reset", action="store_true", help="DROP then recreate")
    ap.add_argument("--sql", nargs="?", const="schema.sql", help="write Postgres DDL")
    args = ap.parse_args()

    if args.sql:
        return write_sql(args.sql)
    if args.verify:
        return verify()
    return create(reset=args.reset)


if __name__ == "__main__":
    sys.exit(main())
