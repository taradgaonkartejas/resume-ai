"""The suite must never write into the real `data/` directory.

This is the defect that caused the worst incident in this project's history.
Tests shared `DATA_DIR` with real uploads, so fixture files (`cv.txt`,
`jane.txt`, `broken.pdf`) accumulated alongside a candidate's actual resume
until "which of these is junk?" had no answer -- and the eventual cleanup was
a blanket delete of the whole directory.

It then recurred twice more *during the cleanup itself*: a restored upload was
overwritten by a `pytest` run while that restore was being verified.

`conftest._isolated_data_dir` redirects it. These tests exist because that
fixture is only as good as its list of modules to patch: `DATA_DIR` is bound
by `from app.db import DATA_DIR`, so each importer holds its own reference,
and a new importer added next year would silently escape the sandbox.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import app.db as db_mod
from app.ai import graphs as graphs_mod
from app.config import ROOT_DIR
from app.services import storage as storage_mod

REAL_DATA_DIR = ROOT_DIR / "data"
BACKEND = Path(__file__).resolve().parents[1]

# Every module the autouse fixture rebinds. Kept here so the test below can
# compare it against what the source actually imports.
PATCHED = {"app.db", "app.services.storage", "app.ai.graphs"}

# cli.py resolves `ROOT_DIR / "data"` at CALL time (it imports ROOT_DIR inside
# each function), so patching app.config.ROOT_DIR covers it without the
# fixture needing to touch the module.
COVERED_BY_ROOT_DIR_PATCH = {"app.cli"}


@pytest.mark.parametrize(
    "module", [db_mod, storage_mod, graphs_mod],
    ids=lambda m: m.__name__,
)
def test_every_holder_points_at_the_sandbox(module, _isolated_data_dir):
    assert module.DATA_DIR == _isolated_data_dir
    assert module.DATA_DIR != REAL_DATA_DIR


def test_writing_a_file_lands_in_the_sandbox(_isolated_data_dir):
    from app.services import storage

    key = storage.put_object("resumeai-uploads", "t/u/probe.txt", b"hello")
    found = list(_isolated_data_dir.rglob("probe.txt"))
    assert found, f"put_object wrote outside the sandbox (key={key})"
    assert not (REAL_DATA_DIR / "resumeai-uploads" / "t").exists()


def test_the_real_directory_is_untouched_by_this_test_run():
    """The blunt, direct assertion -- no fixture indirection."""
    probes = ["cv.txt", "jane.txt", "broken.pdf", "up.txt", "r.pdf", "real.txt"]
    if not REAL_DATA_DIR.exists():
        return
    stale = [p for p in REAL_DATA_DIR.rglob("*") if p.name in probes]
    assert not stale, (
        f"test fixtures are in the REAL data dir: {[str(p) for p in stale]}. "
        "Something is bypassing conftest._isolated_data_dir."
    )


def _modules_reaching_the_data_dir() -> set[str]:
    """Every app module that can resolve the data directory, by EITHER route.

    Two exist, and the first version of this guard only knew about one:

      1. `from app.db import DATA_DIR`        -- storage.py, graphs.py
      2. `ROOT_DIR / "data"`                  -- cli.py, three times

    Route 2 is deliberate: importing app.db builds the engine at module
    scope, which the storage commands must survive, so cli.py derives the
    path instead. Checking only route 1 is what let cli.py write into the
    real directory while every test looked green.
    """
    found: set[str] = set()
    for path in (BACKEND / "app").rglob("*.py"):
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src, str(path))
        hit = False
        for node in ast.walk(tree):
            # route 1
            if isinstance(node, ast.ImportFrom) and node.module == "app.db":
                hit |= any(a.name == "DATA_DIR" for a in node.names)
            # route 2:  ROOT_DIR / "data"
            if (
                isinstance(node, ast.BinOp)
                and isinstance(node.op, ast.Div)
                and isinstance(node.left, ast.Name)
                and node.left.id == "ROOT_DIR"
                and isinstance(node.right, ast.Constant)
                and node.right.value == "data"
            ):
                hit = True
        if hit:
            rel = path.relative_to(BACKEND).with_suffix("")
            found.add(str(rel).replace("/", "."))
    return found


def test_no_unpatched_module_can_reach_the_real_data_dir():
    """Guard the guard: a new route to the directory must be covered.

    Without this, someone adds `from app.db import DATA_DIR` -- or another
    `ROOT_DIR / "data"` -- to a new service next year, every test stays
    green, and the suite quietly starts writing into real data again.
    """
    reaching = _modules_reaching_the_data_dir()
    missing = reaching - PATCHED - COVERED_BY_ROOT_DIR_PATCH
    assert not missing, (
        f"these modules can resolve the real data directory but "
        f"conftest._isolated_data_dir does not cover them: {sorted(missing)}. "
        "Add them to the fixture, or the suite will write into real data."
    )


def test_the_route_scanner_finds_both_routes():
    """A scanner that matched nothing would make the test above vacuous."""
    reaching = _modules_reaching_the_data_dir()
    assert "app.services.storage" in reaching, "route 1 (import) not detected"
    assert "app.cli" in reaching, "route 2 (ROOT_DIR / 'data') not detected"
