"""`Makefile` and `make.ps1` must offer the same targets.

The user runs Windows PowerShell, so `make.ps1` is the file that actually
matters to them -- but every habit and every piece of muscle memory says
"add it to the Makefile". The two have silently drifted at least twice:

  * `writer-report` and `writer-report-llm` were added to the Makefile and
    went missing from `make.ps1` for an entire session;
  * the whole `ats-report*` family did the same, and the user found it by
    running `.\\make.ps1 ats-report-llm` and getting `Unknown target`.

Both times the symptom landed on the user rather than on CI, which is the
wrong way round. This test makes the drift fail here instead.

It parses rather than executes: `make` is not installed in every dev
environment and PowerShell is not installed in most of them.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MAKEFILE = ROOT / "Makefile"
MAKE_PS1 = ROOT / "make.ps1"

# `help` is a Makefile convention; PowerShell uses a bare invocation and a
# `-?` switch, so it is legitimately absent from the switch block.
_EXEMPT = {"help", ".PHONY"}


def _makefile_targets() -> set[str]:
    out = set()
    for line in MAKEFILE.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([a-z][a-z0-9-]*):", line)
        if m:
            out.add(m.group(1))
    return out - _EXEMPT


def _ps1_targets() -> set[str]:
    """Quoted labels in the `switch ($Target...)` dispatch block."""
    text = MAKE_PS1.read_text(encoding="utf-8")
    body = text[text.index("switch ($Target"):]
    return set(re.findall(r"^\s*'([a-z][a-z0-9-]*)'\s*\{", body, re.M)) - _EXEMPT


def test_both_files_exist():
    assert MAKEFILE.is_file() and MAKE_PS1.is_file()


def test_the_parsers_find_something():
    """Guard the guard: a regex that matches nothing would pass silently."""
    assert len(_makefile_targets()) > 10
    assert len(_ps1_targets()) > 10


def test_no_target_is_missing_from_make_ps1():
    missing = sorted(_makefile_targets() - _ps1_targets())
    assert not missing, (
        f"in Makefile but not make.ps1: {missing}. The user runs PowerShell; "
        "a target that exists only in the Makefile does not exist for them."
    )


def test_no_target_is_missing_from_the_makefile():
    missing = sorted(_ps1_targets() - _makefile_targets())
    assert not missing, f"in make.ps1 but not Makefile: {missing}"


@pytest.mark.parametrize("target", sorted(_makefile_targets()))
def test_every_target_is_documented_in_both_help_texts(target):
    """A target nobody can discover is a target nobody runs."""
    if target in {"install", "fmt"}:   # trivially known, kept terse on purpose
        pytest.skip("intentionally undocumented")
    assert re.search(rf"\b{re.escape(target)}\b", MAKEFILE.read_text(encoding="utf-8"))
    assert re.search(rf"\b{re.escape(target)}\b", MAKE_PS1.read_text(encoding="utf-8"))
