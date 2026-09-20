"""Generate requirements.txt from environment.yaml.

environment.yaml is the source of truth for dependencies. This exists so the
venv path stays usable without anyone hand-maintaining a second pin list that
silently drifts out of date.

    python tools/sync_requirements.py           # write backend/requirements.txt
    python tools/sync_requirements.py --check   # fail if it is stale (CI)
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ENV_YAML = BACKEND.parent / "environment.yaml"
REQS = BACKEND / "requirements.txt"

HEADER = """# GENERATED — do not edit.
# Source of truth: environment.yaml
# Regenerate: python tools/sync_requirements.py
#
# conda users: `conda env create -f ../environment.yaml` instead.
# This file exists for the plain-venv path only.
"""


def parse_env(path: Path) -> tuple[list[str], str | None]:
    """Extract the pip block and any conda numpy pin.

    Deliberately a small parser rather than a pyyaml dependency: this script
    has to run before the environment it describes is installed.
    """
    pips: list[str] = []
    numpy_pin: str | None = None
    in_pip = False

    for raw in path.read_text().splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        stripped = line.strip()

        if re.match(r"^-\s*pip:\s*$", stripped):
            in_pip = True
            continue

        if in_pip:
            # The pip block is indented deeper than the "- pip:" key itself.
            if stripped.startswith("- ") and (len(line) - len(line.lstrip())) >= 6:
                pips.append(stripped[2:].strip())
                continue
            in_pip = False

        m = re.match(r"^-\s*numpy\s*=\s*([\d.]+)\s*$", stripped)
        if m:
            numpy_pin = m.group(1)

    return pips, numpy_pin


def render(pips: list[str], numpy_pin: str | None) -> str:
    lines = [HEADER]
    if numpy_pin:
        lines.append(
            "# numpy comes from conda-forge in environment.yaml; pinned here\n"
            "# so the venv path matches exactly.\n"
            f"numpy=={numpy_pin}\n"
        )
    lines.append("\n".join(pips) + "\n")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="exit 1 if stale")
    args = ap.parse_args()

    if not ENV_YAML.exists():
        print(f"missing {ENV_YAML}", file=sys.stderr)
        return 1

    pips, numpy_pin = parse_env(ENV_YAML)
    if not pips:
        print("no pip block found in environment.yaml", file=sys.stderr)
        return 1

    content = render(pips, numpy_pin)

    if args.check:
        current = REQS.read_text() if REQS.exists() else ""
        if current != content:
            print("requirements.txt is stale — run tools/sync_requirements.py")
            return 1
        print("requirements.txt is up to date.")
        return 0

    REQS.write_text(content)
    print(f"wrote {REQS.relative_to(BACKEND)} ({len(pips)} pins from environment.yaml)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
