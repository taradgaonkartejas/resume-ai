"""`make fetch-models` / `.\\make.ps1 fetch-models`.

This is the script you run when your environment is wrong, so the thing worth
testing is not the happy path -- it is that every failure prints advice
rather than a traceback.

The first version failed that completely. `fetch_models` imported
`app.ai.embeddings`, which imported `app.ai.llm`, which imported the whole
provider stack and `pydantic_settings`, all to read one string constant. With
the env half-built the user got:

    ModuleNotFoundError: No module named 'pydantic_settings'

...and the carefully written "fastembed is not installed, run conda env
update" message was unreachable, because the import chain exploded before
main() ran. Every import now happens inside main(), ordered by how likely it
is to be the broken thing.
"""

from __future__ import annotations

import ast
import pathlib
import re
import subprocess
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------
# the import discipline that makes the error messages reachable
# --------------------------------------------------------------------------

class TestImportDiscipline:
    def _module_level_imports(self) -> set[str]:
        tree = ast.parse((BACKEND / "app" / "fetch_models.py").read_text())
        names: set[str] = set()
        for node in tree.body:  # module level ONLY, not nested
            if isinstance(node, ast.Import):
                names.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
        return names

    def test_it_imports_nothing_from_the_app_at_module_level(self):
        """An app import at module scope makes every message unreachable."""
        offenders = {n for n in self._module_level_imports()
                     if n.split(".")[0] == "app"}
        assert not offenders, (
            f"module-level app imports: {sorted(offenders)}. These run before "
            "main() and turn a helpful message into a traceback."
        )

    def test_it_does_not_import_heavy_third_party_at_module_level(self):
        heavy = {"fastembed", "pydantic", "pydantic_settings", "sqlalchemy",
                 "openai", "langchain_openai", "langgraph"}
        assert not (self._module_level_imports() & heavy)

    def test_embeddings_does_not_import_the_llm_gateway_at_module_level(self):
        """The root cause: embeddings dragged in the whole provider stack.

        The remote path is off by default, so importing the gateway to read a
        constant cost every caller -- including a setup script -- the entire
        dependency tree.
        """
        tree = ast.parse((BACKEND / "app" / "ai" / "embeddings.py").read_text())
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module == "app.ai":
                assert "llm" not in {a.name for a in node.names}, (
                    "app.ai.embeddings imports llm at module level again"
                )


# --------------------------------------------------------------------------
# the constant is duplicated on purpose -- prove it cannot drift
# --------------------------------------------------------------------------

def test_the_model_name_matches_the_embeddings_module():
    from app.ai.embeddings import MINILM_MODEL as canonical
    from app.fetch_models import MINILM_MODEL as duplicated

    assert duplicated == canonical, (
        "fetch_models duplicates this constant so it can import nothing to "
        "find it; the two have drifted apart"
    )


# --------------------------------------------------------------------------
# behaviour
# --------------------------------------------------------------------------

def _run(env_extra: dict[str, str], python: str = sys.executable):
    import os

    env = {**os.environ, "PYTHONPATH": str(BACKEND), **env_extra}
    return subprocess.run([python, "-m", "app.fetch_models"],
                          capture_output=True, text=True, cwd=BACKEND, env=env)


class TestBehaviour:
    def test_hashed_backend_short_circuits_without_fetching(self):
        r = _run({"EMBEDDING_BACKEND": "hashed",
                  "DATABASE_URL": "sqlite:///:memory:",
                  "ALLOW_SQLITE_FALLBACK": "true"})
        assert r.returncode == 0
        assert "nothing to fetch" in r.stdout

    def test_success_reports_the_dimension_and_the_reset_reminder(self):
        pytest.importorskip("fastembed")
        r = _run({"EMBEDDING_BACKEND": "minilm",
                  "DATABASE_URL": "sqlite:///:memory:",
                  "ALLOW_SQLITE_FALLBACK": "true"})
        assert r.returncode == 0, r.stderr
        assert "384-dimensional" in r.stdout
        assert "db reset" in r.stdout, (
            "switching backend without re-embedding silently corrupts search"
        )

    def test_a_missing_dependency_gives_advice_not_a_traceback(self, tmp_path):
        """The regression this file exists for."""
        bare = tmp_path / "venv"
        subprocess.run([sys.executable, "-m", "venv", str(bare)], check=True,
                       capture_output=True)
        python = bare / "bin" / "python"
        if not python.exists():  # pragma: no cover - windows layout
            python = bare / "Scripts" / "python.exe"

        r = _run({}, python=str(python))
        assert r.returncode == 1
        assert "Traceback" not in r.stderr, (
            f"user sees a traceback, not advice:\n{r.stderr}"
        )
        assert "fastembed is not installed" in r.stderr
        assert "conda env update" in r.stderr


# --------------------------------------------------------------------------
# both runners expose it
# --------------------------------------------------------------------------

class TestRunners:
    def test_the_makefile_has_the_target(self):
        body = (BACKEND / "Makefile").read_text()
        assert re.search(r"^fetch-models:\s*$", body, re.M)
        assert "app.fetch_models" in body

    def test_make_ps1_dispatches_it(self):
        body = (BACKEND / "make.ps1").read_text()
        assert "'fetch-models'" in body
        assert "app.fetch_models" in body

    def test_make_ps1_help_mentions_it(self):
        """An undiscoverable setup step does not get run."""
        body = (BACKEND / "make.ps1").read_text()
        help_section = body.split("function Show-Help")[1]
        assert "fetch-models" in help_section
