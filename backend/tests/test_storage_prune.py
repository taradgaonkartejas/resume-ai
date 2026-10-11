"""`storage prune` must never delete a file a resume row points at.

Written because a blanket `rm -rf data/` removed 110 MB that nobody had
asked to lose. The directory holds three different things -- live uploads
backing real rows, regenerable exports, and test detritus -- and the only
safe tool is one that tells them apart and defaults to doing nothing.
"""

from __future__ import annotations

import argparse

import pytest

from app import cli
from app.db import DATA_DIR


@pytest.fixture
def planted(client, priya):
    """A live upload plus one fixture, one orphan and one export."""
    up = DATA_DIR / "resumeai-uploads"
    ex = DATA_DIR / "resumeai-exports"

    res = client.post(
        "/api/resumes/upload",
        headers={"X-User-Id": priya},
        files={"file": ("real.txt", b"Jane Doe\njane@example.com\n", "text/plain")},
    )
    assert res.status_code == 201, res.text

    from sqlalchemy import select

    from app.models import Resume

    db = client._session_factory()
    try:
        live_key = db.execute(
            select(Resume.storage_key).where(Resume.storage_key != "")
        ).scalars().first()
    finally:
        db.close()
    assert live_key

    fixture = up / "dead-0000" / "aaaa-0000" / "cv.txt"
    orphan = up / "dead-1111" / "bbbb-0000" / "someones_cv.pdf"
    export = ex / "zz" / "out.pdf"
    for path, body in ((fixture, b"x"), (orphan, b"y"), (export, b"z")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)

    return {
        "live": up / live_key,
        "fixture": fixture,
        "orphan": orphan,
        "export": export,
    }


def _args(**kw):
    defaults = {"force": False, "exports": False, "keep_orphans": False}
    return argparse.Namespace(**{**defaults, **kw})


def test_status_classifies_without_deleting(planted, capsys):
    assert cli.cmd_storage_status(_args()) == 0
    for path in planted.values():
        assert path.exists(), f"status deleted {path}"


def test_dry_run_is_the_default(planted):
    cli.cmd_storage_prune(_args())
    for name, path in planted.items():
        assert path.exists(), f"dry run deleted {name}"


def test_force_keeps_the_live_upload(planted):
    cli.cmd_storage_prune(_args(force=True))
    assert planted["live"].exists(), "pruned a file a resume row points at"


def test_force_removes_fixtures_and_orphans(planted):
    cli.cmd_storage_prune(_args(force=True))
    assert not planted["fixture"].exists()
    assert not planted["orphan"].exists()


def test_exports_are_kept_unless_asked_for(planted):
    cli.cmd_storage_prune(_args(force=True))
    assert planted["export"].exists(), "exports went without --exports"
    cli.cmd_storage_prune(_args(force=True, exports=True))
    assert not planted["export"].exists()
    assert planted["live"].exists()


def test_keep_orphans_prunes_only_fixtures(planted):
    cli.cmd_storage_prune(_args(force=True, keep_orphans=True))
    assert not planted["fixture"].exists()
    assert planted["orphan"].exists()
    assert planted["live"].exists()


def test_the_live_upload_is_still_readable_after_a_full_prune(planted, client, priya):
    from app.services import storage

    cli.cmd_storage_prune(_args(force=True, exports=True))
    key = str(planted["live"].relative_to(DATA_DIR / "resumeai-uploads"))
    assert storage.get_object("resumeai-uploads", key).startswith(b"Jane Doe")


def test_an_unreachable_database_refuses_rather_than_deleting(planted, monkeypatch):
    """The failure mode that caused the original accident.

    With no database every upload looks unreferenced, so a tool that treats
    "cannot read the DB" as "nothing is live" deletes the lot. It must
    decline instead — and decline *cleanly*: this first surfaced as a raw
    traceback, because importing app.db for a path constant is enough to
    build the engine and raise.
    """
    import app.cli as cli_mod

    monkeypatch.setattr(cli_mod, "_storage_scan", lambda: None)
    assert cli_mod.cmd_storage_prune(_args(force=True)) == 1
    for name, path in planted.items():
        assert path.exists(), f"deleted {name} with no database"


# ------------------------------------------------------------------- verify
# `prune` asks "which files have no row?". `verify` asks the question that
# actually hurts: "which rows have no file?"
def test_verify_is_clean_when_every_file_is_present(planted):
    assert cli.cmd_storage_verify(_args()) == 0


def test_verify_reports_a_missing_file_and_exits_nonzero(planted, capsys):
    planted["live"].unlink()
    assert cli.cmd_storage_verify(_args()) == 1
    out = capsys.readouterr().out
    assert "MISSING" in out


def test_verify_ignores_orphans_and_exports(planted):
    """A file with no row is prune's business, not verify's."""
    planted["fixture"].unlink()
    planted["orphan"].unlink()
    planted["export"].unlink()
    assert cli.cmd_storage_verify(_args()) == 0


def test_verify_never_deletes_anything(planted):
    cli.cmd_storage_verify(_args())
    for name, path in planted.items():
        assert path.exists(), f"verify deleted {name}"
