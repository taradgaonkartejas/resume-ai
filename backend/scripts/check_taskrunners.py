#!/usr/bin/env python3
"""Static checks for Makefile / make.ps1, which CI cannot execute on Linux.

`make.ps1` only ever runs on a Windows machine, so nothing in this repo's test
suite exercises it. These checks are the substitute. They exist because two
real bugs shipped:

1. A `which-python` target built from `python -c "import sys; print("...")"`.
   PowerShell re-quotes arguments when invoking a native executable, and the
   embedded double quotes terminated that wrapping early — python received a
   truncated snippet and died with `SyntaxError: '(' was never closed`.

2. The two runners drifting apart, breaking the README's promise that every
   `make X` has a `.\\make.ps1 X`.

Run: python scripts/check_taskrunners.py
"""

from __future__ import annotations

import pathlib
import re
import sys

BACKEND = pathlib.Path(__file__).resolve().parents[1]
MAKEFILE = BACKEND / "Makefile"
PS1 = BACKEND / "make.ps1"

failures: list[str] = []
checks = 0


def check(condition: bool, label: str, detail: str = "") -> None:
    global checks
    checks += 1
    if condition:
        print(f"  OK   {label}" + (f" -- {detail}" if detail else ""))
    else:
        print(f"  FAIL {label}" + (f" -- {detail}" if detail else ""))
        failures.append(label)


def main() -> int:
    mk = MAKEFILE.read_text(encoding="utf-8")
    ps = PS1.read_text(encoding="utf-8")

    print("=== target parity ===")
    mk_targets = {
        t for t in re.findall(r"^([a-z][a-z0-9-]*):", mk, re.M)
    } - {"help"}
    ps_targets = {
        t for t in re.findall(r"^\s{4}'([a-z0-9-]+)'", ps, re.M)
    } - {"help"}
    check(
        mk_targets == ps_targets,
        "every make target has a make.ps1 target",
        f"{len(mk_targets)} vs {len(ps_targets)}",
    )
    if mk_targets - ps_targets:
        print(f"       missing from make.ps1: {sorted(mk_targets - ps_targets)}")
    if ps_targets - mk_targets:
        print(f"       missing from Makefile: {sorted(ps_targets - mk_targets)}")

    print("\n=== PowerShell argument quoting ===")
    # PowerShell mangles embedded double quotes when it re-quotes arguments for
    # a native .exe. Any -c payload containing one arrives truncated.
    bad = [
        (n, line.strip())
        for n, line in enumerate(ps.splitlines(), 1)
        if "'-c'" in line and '"' in line.split("'-c'", 1)[1]
    ]
    check(
        not bad,
        "no `-c` payload contains an embedded double quote",
        f"{len(bad)} offender(s)",
    )
    for n, line in bad:
        print(f"       line {n}: {line}")
        print("       -> move the logic into a .py file and pass a script path")

    # Same trap, different spelling: & $PY -c "...\"...\"..."
    inline = [
        (n, line.strip())
        for n, line in enumerate(ps.splitlines(), 1)
        if re.search(r'&\s+\$PY\s+-c\s+"[^"]*\\"', line)
    ]
    check(not inline, "no escaped double quotes in inline `& $PY -c` calls")
    for n, line in inline:
        print(f"       line {n}: {line}")

    print("\n=== structure ===")
    check(ps.count("{") == ps.count("}"), "make.ps1 braces balanced",
          f"{ps.count('{')}/{ps.count('}')}")
    check(ps.count("(") == ps.count(")"), "make.ps1 parens balanced",
          f"{ps.count('(')}/{ps.count(')')}")
    check("default {" in ps, "make.ps1 switch has a default arm")

    print("\n=== interpreter resolution ===")
    # "python on PATH" and "the activated conda env" are not the same thing on
    # Anaconda-on-Windows. Both runners must consult CONDA_PREFIX.
    check("CONDA_PREFIX" in ps, "make.ps1 prefers $env:CONDA_PREFIX over PATH")
    check("CONDA_PREFIX" in mk, "Makefile prefers $CONDA_PREFIX over PATH")

    print("\n=== Makefile tabs ===")
    # A recipe indented with spaces fails with "missing separator".
    #
    # Only RECIPE lines need a tab. A space-indented continuation of a
    # non-recipe line — `.PHONY: a b \` wrapped over several lines — is
    # perfectly legal, so the previous line ending in a backslash rules a
    # line out. (The first version of this check flagged exactly that and
    # was wrong.)
    lines = mk.splitlines()
    offenders = []
    for i, line in enumerate(lines):
        if not line.startswith("    ") or line.lstrip().startswith("#"):
            continue
        prev = lines[i - 1] if i else ""
        if prev.rstrip().endswith("\\"):
            continue        # continuation of the line above, not a recipe
        offenders.append(i + 1)
    check(not offenders, "recipes are tab-indented, not space-indented",
          f"lines {offenders[:5]}" if offenders else "")

    print(
        f"\n>>> all {checks} checks passed"
        if not failures
        else f"\n>>> {len(failures)} FAILED, {checks - len(failures)} passed"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
