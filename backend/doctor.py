"""Environment diagnostics — run this when something fails before the app starts.

    python doctor.py

Standard library only, and it imports nothing from `app`. That is deliberate:
this has to keep working when pydantic, psycopg2 or the config module are the
things that are broken.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import re
import socket
import sys
import traceback

OK = "  [ok]   "
BAD = "  [FAIL] "
WARN = "  [warn] "


def header(text: str) -> None:
    print(f"\n{text}\n" + "-" * len(text))


def check_interpreter() -> bool:
    """Report the interpreter, and cross-check it against the activated env.

    Printing `sys.executable` and `CONDA_DEFAULT_ENV` side by side is not
    enough: an activated prompt says `(resume-ai)` while `python` can still
    resolve to base Anaconda, and then every package you installed into the
    env is invisible. The symptom is a bare ModuleNotFoundError for something
    `conda list` clearly shows as installed. Comparing the two prefixes is the
    only way to see it.
    """
    header("Interpreter")
    print(f"{OK}python   {sys.version.split()[0]}")
    print(f"{OK}exe      {sys.executable}")

    env = os.environ.get("CONDA_DEFAULT_ENV")
    prefix = os.environ.get("CONDA_PREFIX")
    if env:
        print(f"{OK}conda    {env}")
    else:
        print(f"{WARN}conda    no CONDA_DEFAULT_ENV — env may not be activated")

    if not prefix:
        return True

    activated = pathlib.Path(prefix).resolve()
    running = pathlib.Path(sys.prefix).resolve()
    if activated == running:
        print(f"{OK}prefix   {running}")
        return True

    print(f"{BAD}WRONG INTERPRETER")
    print(f"         activated env : {activated}")
    print(f"         actually using: {running}")
    print("")
    print("         The shell prompt says the env is active, but `python` is")
    print("         resolving somewhere else — so packages installed into the")
    print("         env are invisible and you get ModuleNotFoundError for")
    print("         things `conda list` shows as present.")
    print("")
    print("         Run the CLI through the env explicitly:")
    print(f"           conda run -n {env or 'resume-ai'} --no-capture-output python -m app.cli db push --seed")
    print("         or call the env's interpreter directly:")
    exe = activated / ("python.exe" if os.name == "nt" else "bin/python")
    print(f"           \"{exe}\" -m uvicorn app.main:app --reload")
    print("")
    print("         To fix it permanently, re-initialise the conda shell hook:")
    print("           conda init powershell     # then open a NEW terminal")
    return False


def check_binary_package(name: str, ext_stem: str, fix: str) -> bool:
    """Verify a package whose real work lives in a compiled extension.

    Import alone is not enough: the pure-Python half can be present while the
    .pyd/.so is missing, which is the failure this whole script exists for.
    """
    spec = importlib.util.find_spec(name)
    if spec is None:
        print(f"{BAD}{name}: not installed")
        print(f"         {fix}")
        return False

    locations = list(spec.submodule_search_locations or [])
    if locations:
        pkg_dir = pathlib.Path(locations[0])
        found = sorted(p.name for p in pkg_dir.glob(f"{ext_stem}*"))
        if not found:
            print(f"{BAD}{name}: compiled extension '{ext_stem}' MISSING")
            print(f"         in {pkg_dir}")
            print(f"         {fix}")
            return False

    try:
        module = importlib.import_module(name)
    except Exception as exc:  # noqa: BLE001
        print(f"{BAD}{name}: import failed — {type(exc).__name__}: {exc}")
        print(f"         {fix}")
        return False

    version = getattr(module, "__version__", "?")
    print(f"{OK}{name:16} {version}")
    return True


def read_env_file(path: pathlib.Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip().strip('"').strip("'")
    return values


def check_dsn(dsn: str) -> tuple[str, int] | None:
    """Parse the DSN the way SQLAlchemy does and flag the usual mistakes."""
    header("DATABASE_URL")

    if not dsn:
        print(f"{BAD}not set in backend/.env or the environment")
        return None

    scheme, _, rest = dsn.partition("://")
    print(f"{OK}driver   {scheme}")

    # SQLAlchemy splits credentials from the host on the LAST '@'. An
    # unencoded '@' inside the password therefore lands in the hostname.
    creds, _, hostpart = rest.rpartition("@")
    user, _, password = creds.partition(":")
    hostport, _, database = hostpart.partition("/")
    host, _, port_s = hostport.partition(":")

    print(f"{OK}user     {user}")
    print(f"{OK}host     {host}")
    print(f"{OK}port     {port_s or '(default)'}")
    print(f"{OK}database {database}")

    if "@" in password:
        fixed = password.replace("@", "%40")
        print(f"{BAD}password contains an unencoded '@'")
        print("         SQLAlchemy splits on the LAST '@', so this DSN is ambiguous.")
        print(f"         percent-encode it as %40:")
        print(f"           {scheme}://{user}:{fixed}@{hostport}/{database}")
    if not password:
        print(f"{WARN}no password in the DSN")
    if "%40" in password:
        print(f"{OK}password percent-encoded '@' correctly")

    try:
        return host, int(port_s)
    except ValueError:
        print(f"{WARN}port {port_s!r} is not a number — cannot test the socket")
        return None


def check_socket(host: str, port: int) -> None:
    header(f"TCP {host}:{port}")
    try:
        with socket.create_connection((host, port), timeout=3):
            print(f"{OK}something is listening")
    except OSError as exc:
        print(f"{BAD}nothing listening — {exc}")
        print("         docker compose up -d db --wait   (from the repo root)")


def main() -> int:
    here = pathlib.Path(__file__).resolve().parent

    interpreter_ok = check_interpreter()

    header("Compiled dependencies")
    results = [
        interpreter_ok,
        check_binary_package(
            "psycopg2",
            "_psycopg",
            "pip install --force-reinstall --no-cache-dir psycopg2-binary==2.9.13",
        ),
        check_binary_package(
            "pydantic_core",
            "_pydantic_core",
            "pip install --force-reinstall --no-cache-dir pydantic==2.13.5 pydantic-core==2.46.5",
        ),
    ]

    header("Pure-Python dependencies")
    for name in ("fastapi", "sqlalchemy", "alembic", "pgvector", "boto3", "langgraph"):
        try:
            module = importlib.import_module(name)
            print(f"{OK}{name:16} {getattr(module, '__version__', '?')}")
        except Exception as exc:  # noqa: BLE001
            print(f"{BAD}{name}: {type(exc).__name__}: {exc}")
            results.append(False)

    # .env lives in backend/ (this file's directory), not the repo root.
    env = read_env_file(here / ".env")
    dsn = os.environ.get("DATABASE_URL") or env.get("DATABASE_URL", "")
    target = check_dsn(dsn)
    if target:
        check_socket(*target)

    header("Result")
    if all(results):
        print(f"{OK}dependencies are importable")
        print("         if db-push still fails, the problem is the server or the DSN above")
        return 0
    if not interpreter_ok:
        print(f"{BAD}fix the interpreter FIRST — every other failure above is")
        print("         a symptom of running the wrong Python, not a real")
        print("         missing dependency. Do not reinstall anything yet.")
        return 1
    print(f"{BAD}fix the failures above, then re-run: python doctor.py")
    print("         if more than one is broken, rebuild the env instead:")
    print("         conda env remove -n resume-ai")
    print("         conda env create -f environment.yaml")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        sys.exit(2)
