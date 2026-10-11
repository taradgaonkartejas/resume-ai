"""Fail when a third-party import is missing from environment.yaml.

`conda env update --prune` deletes anything the file does not declare. An
undeclared-but-installed package therefore works perfectly until the first
prune, and then disappears -- which is how reportlab vanished and took two
test modules with it as collection errors.

Relying on a transitive dependency has the same shape: it works until the
package that pulled it in changes its own requirements.
"""

from __future__ import annotations

import ast
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

# import name -> distribution name, where they differ
ALIAS = {
    "docx": "python-docx",
    "fpdf": "fpdf2",
    "dotenv": "python-dotenv",
    "yaml": "pyyaml",
    "multipart": "python-multipart",
    "psycopg2": "psycopg2-binary",
    "PIL": "pillow",
}

# Pulled in by a declared package and not imported directly by us.
TRANSITIVE_OK = {"botocore", "langchain-core"}

# Local modules imported via sys.path manipulation, not distributions.
LOCAL = {"app", "tests", "scripts", "writer_report", "check_model_access",
         "critic_report", "doctor"}


def _declared() -> set[str]:
    text = (ROOT / "environment.yaml").read_text(encoding="utf-8")
    out = set()
    for line in text.splitlines():
        m = re.match(r"\s*-\s*([A-Za-z0-9_.\-]+)\s*(?:[=<>!].*)?$", line)
        if m:
            out.add(m.group(1).lower().replace("_", "-"))
    return out


def _imported() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for sub in ("app", "tests", "scripts"):
        for path in (ROOT / sub).rglob("*.py"):
            if "__pycache__" in str(path):
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    names = [node.module.split(".")[0]] if node.module else []
                else:
                    continue
                for n in names:
                    if n in sys.stdlib_module_names or n in LOCAL:
                        continue
                    dist = ALIAS.get(n, n).lower().replace("_", "-")
                    found.setdefault(dist, set()).add(
                        str(path.relative_to(ROOT))
                    )
    return found


def main() -> int:
    declared = _declared()
    missing = {
        dist: where
        for dist, where in _imported().items()
        if dist not in declared and dist not in TRANSITIVE_OK
    }
    if not missing:
        print(">>> every third-party import is declared in environment.yaml")
        return 0
    print("UNDECLARED imports -- `conda env update --prune` will delete these:\n")
    for dist in sorted(missing):
        print(f"  {dist}")
        for w in sorted(missing[dist])[:3]:
            print(f"      {w}")
    print("\nAdd them to backend/environment.yaml with a pinned version.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
