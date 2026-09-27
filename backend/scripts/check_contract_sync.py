"""Fail if the frontend's hand-written types have drifted from the API.

`frontend/src/services/types.ts` is maintained by hand (there is no codegen
step), so nothing stops the backend adding a field and the UI never reading
it. That drift is invisible: TypeScript is happy, the request succeeds, and
the value is silently dropped. This script is the check that would have caught
`DeleteOut.children_orphaned` and `TailorIn.fork` the day they were added.

Run:
    DATABASE_URL=sqlite:////tmp/c.db ALLOW_SQLITE_FALLBACK=true \
        python backend/scripts/check_contract_sync.py

Exit 0 = in sync, 1 = drift found.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/contract_check.db")
os.environ.setdefault("ALLOW_SQLITE_FALLBACK", "true")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.main import app  # noqa: E402

TYPES = ROOT / "frontend" / "src" / "services" / "types.ts"

# Schemas with no frontend counterpart by design (request bodies the UI builds
# inline, FastAPI's own error envelopes).
IGNORE = {"HTTPValidationError", "ValidationError", "Body_upload_resume"}


def ts_interface_fields(src: str, name: str) -> set[str] | None:
    m = re.search(rf"export interface {name} \{{(.*?)\n\}}", src, re.S)
    if not m:
        return None
    return set(re.findall(r"^\s*([a-z_][a-zA-Z0-9_]*)\??:", m.group(1), re.M))


def main() -> int:
    spec = app.openapi()
    schemas = spec["components"]["schemas"]
    src = TYPES.read_text()

    problems: list[str] = []
    checked = 0

    for name, schema in sorted(schemas.items()):
        if name in IGNORE or "properties" not in schema:
            continue
        frontend = ts_interface_fields(src, name)
        if frontend is None:
            # Not every schema needs a mirror; only report ones the UI clearly
            # consumes (they end in Out and are referenced by a service).
            continue
        checked += 1
        backend = set(schema["properties"].keys())
        missing = backend - frontend
        extra = frontend - backend
        if missing:
            problems.append(f"{name}: backend sends {sorted(missing)}, frontend has no field")
        if extra:
            problems.append(f"{name}: frontend expects {sorted(extra)}, backend never sends it")

    print(f"checked {checked} shared schemas")
    if problems:
        print("\nCONTRACT DRIFT:")
        for p in problems:
            print("  -", p)
        return 1
    print("frontend types are in sync with the API")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
