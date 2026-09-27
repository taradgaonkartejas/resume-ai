"""The advice printed on schema drift must actually fix the drift.

A stale `resumes` table missing the `kind` / `parent_id` / `tailored_for`
columns produced a 500 at runtime, and following the tooling's own advice went
in a circle:

    db status  ->  "Run: db push"
    db push    ->  "create_all never alters existing tables; use migrate dev"
    migrate dev -> contradicts this project's decision to reset instead

`db reset`, the one command that resolves it, was never mentioned. These tests
pin the remedy text so that loop cannot come back.
"""

import io
from contextlib import redirect_stdout

from app import cli


def _remedy(drift: list[str]) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        cli._print_drift_remedy(drift)
    return buf.getvalue()


COLUMN_DRIFT = [
    "add_column: resumes.kind",
    "add_column: resumes.tailored_for",
    "add_index: ix_resumes_kind",
]


def test_column_drift_recommends_reset():
    text = _remedy(COLUMN_DRIFT)
    assert "db reset" in text


def test_column_drift_does_not_send_the_user_back_to_db_push():
    """`db push` is create_all; it cannot add a column to an existing table."""
    text = _remedy(COLUMN_DRIFT)
    assert "Run: python -m app.cli db push" not in text


def test_remedy_explains_why_push_cannot_help():
    text = _remedy(COLUMN_DRIFT)
    assert "only adds missing TABLES" in text
    assert "never alters existing ones" in text


def test_destructive_command_is_labelled_destructive():
    """Nobody should run this without knowing it drops their data."""
    text = _remedy(COLUMN_DRIFT)
    line = next(ln for ln in text.splitlines() if "db reset" in ln and "app.cli" in ln)
    assert "DROPS ALL DATA" in line


def test_remedy_offers_the_data_preserving_alternative():
    text = _remedy(COLUMN_DRIFT)
    assert "migrate dev" in text


def test_remedy_mentions_both_task_runners():
    text = _remedy(COLUMN_DRIFT)
    assert "make db-reset" in text
    assert "make.ps1" in text


def test_non_column_drift_still_gets_an_explanation():
    """An index-only diff should not silently print nothing."""
    text = _remedy(["add_index: ix_resumes_kind"])
    assert text.strip()
    assert "create_all" in text
