<#
.SYNOPSIS
    Task runner for ResumeAI backend — the Windows equivalent of the Makefile.

.DESCRIPTION
    Same target names as `make`, so the README works on both platforms:

        .\make.ps1 db-push-seed
        .\make.ps1 db-status
        .\make.ps1 migrate -m "add users.linkedin_url"

    Every target is a thin wrapper over `python -m app.cli`. If you prefer,
    skip this script entirely and call the CLI directly — it is the same thing:

        python -m app.cli db push --seed

    Configuration is read from backend\.env (this script's own directory),
    not the repo root. `.\make.ps1 env-file` creates it from .env.example.

    If PowerShell refuses to run this file ("not digitally signed"), either
    use the CLI directly as above, or:

        powershell -ExecutionPolicy Bypass -File .\make.ps1 db-push-seed

.EXAMPLE
    .\make.ps1                      # list targets
.EXAMPLE
    .\make.ps1 db-push-seed         # create tables + seed
.EXAMPLE
    .\make.ps1 migrate -m "add column"
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Target = 'help',

    # Migration message, for: .\make.ps1 migrate -m "..."
    [Alias('m')]
    [string]$Message,

    # Override the database for one command.
    [string]$Url,

    # Pass through to destructive targets without prompting.
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

# --- locate the interpreter -------------------------------------------------
# Priority order:
#   1. a local .venv, if you use one
#   2. $env:CONDA_PREFIX — the ACTIVATED env, which is authoritative
#   3. plain `python` from PATH
#
# Step 2 is the important one. "python on PATH" and "the activated conda env"
# are NOT the same thing: Anaconda installs that add base to the system PATH
# can leave the prompt reading (resume-ai) while `python` still resolves to
# C:\ProgramData\anaconda3. Every package installed into the env is then
# invisible, and you get a ModuleNotFoundError for something `conda list`
# plainly shows as installed. CONDA_PREFIX is set by `conda activate` itself,
# so it cannot disagree with the prompt.
function Resolve-Python {
    $candidates = @(
        (Join-Path $PSScriptRoot '.venv\Scripts\python.exe'),
        (Join-Path $PSScriptRoot '.venv/bin/python')
    )
    foreach ($c in $candidates) {
        if (Test-Path -LiteralPath $c) { return $c }
    }

    if ($env:CONDA_PREFIX) {
        $condaPy = @(
            (Join-Path $env:CONDA_PREFIX 'python.exe'),
            (Join-Path $env:CONDA_PREFIX 'bin/python')
        )
        foreach ($c in $condaPy) {
            if (Test-Path -LiteralPath $c) { return $c }
        }
    }

    $onPath = Get-Command python -ErrorAction SilentlyContinue
    if ($onPath) { return $onPath.Source }
    throw "No Python found. Activate the conda env (conda activate resume-ai) or create a venv."
}

$PY = Resolve-Python

# If an env is activated but we ended up on a different interpreter anyway,
# say so loudly rather than failing later with a confusing import error.
if ($env:CONDA_PREFIX) {
    $running = (& $PY -c "import sys; print(sys.prefix)" 2>$null)
    if ($running -and ($running.Trim() -ne $env:CONDA_PREFIX.TrimEnd('\', '/'))) {
        Write-Host "warning: activated env is $($env:CONDA_PREFIX)" -ForegroundColor Yellow
        Write-Host "         but the interpreter reports $running" -ForegroundColor Yellow
        Write-Host "         run '.\make.ps1 which-python' to inspect" -ForegroundColor Yellow
    }
}

function Invoke-Py {
    param([string[]]$Arguments)
    & $PY @Arguments
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

# NOTE: do not rename this back to `Cli`. `cli` is a built-in PowerShell alias
# for Clear-Item, and aliases are resolved before functions, so every target
# would call Clear-Item instead of this. Use an approved-verb name.
#
# `--url` must precede the subcommand: app.cli applies it before importing
# app.db, which builds its engine at import time.
function Invoke-Cli {
    param([string[]]$Arguments)
    $pre = @('-m', 'app.cli')
    if ($Url) { $pre += @('--url', $Url) }
    Invoke-Py ($pre + $Arguments)
}

function Show-Help {
    Write-Host ""
    Write-Host "ResumeAI backend tasks" -ForegroundColor Cyan
    Write-Host '  usage: .\make.ps1 <target> [-m "msg"] [-Url ...] [-Force]'
    Write-Host ""
    Write-Host "Services (docker compose, run from the repo root)" -ForegroundColor Yellow
    Write-Host "  up              start Postgres + MinIO + Redis, wait for the db"
    Write-Host "  ps              service status"
    Write-Host "  logs            follow logs"
    Write-Host "  down            stop (volumes survive)"
    Write-Host ""
    Write-Host "Environment (environment.yaml = packages, .env = settings)" -ForegroundColor Yellow
    Write-Host "  env-file        create backend\.env from .env.example"
    Write-Host "  which-python    show which interpreter these targets use"
    Write-Host "  doctor          diagnose a broken environment"
    Write-Host "  env             conda env create -f environment.yaml"
    Write-Host "  env-update      conda env update --prune"
    Write-Host ""
    Write-Host "  backend\.env is THIS directory's config and holds real secrets." -ForegroundColor DarkGray
    Write-Host "  The browser app is configured separately in frontend\.env.local." -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "Schema (app/models.py is the source of truth)" -ForegroundColor Yellow
    Write-Host "  db-push         create database + tables to match the models"
    Write-Host "  db-push-seed    ... and seed"
    Write-Host "  db-status       drift between models and database"
    Write-Host "  db-inspect      tables, columns, row counts"
    Write-Host "  db-seed         run the seeder"
    Write-Host "  db-reset        drop, recreate, push, seed (destructive)"
    Write-Host "  db-sql          write ..\schema.sql"
    Write-Host ""
    Write-Host "Migrations" -ForegroundColor Yellow
    Write-Host '  migrate -m "msg"  autogenerate a revision and apply it'
    Write-Host "  migrate-deploy    apply pending revisions"
    Write-Host "  migrate-status    current revision and history"
    Write-Host "  migrate-down      roll back one revision"
    Write-Host ""
    Write-Host "Develop" -ForegroundColor Yellow
    Write-Host "  test            pytest -q"
    Write-Host "  lint            ruff check app tests"
    Write-Host "  run             uvicorn on 0.0.0.0:8000"
    Write-Host ""
    Write-Host "interpreter: $PY" -ForegroundColor DarkGray
    Write-Host ""
}

switch ($Target.ToLowerInvariant()) {
    'help'           { Show-Help }

    'up' {
        Push-Location ..
        try {
            docker compose up -d
            # Wait on db only: a bare --wait fails when minio-init exits 0.
            docker compose up -d db --wait
            docker compose ps
        } finally { Pop-Location }
    }
    'ps'             { Push-Location ..; try { docker compose ps } finally { Pop-Location } }
    'logs'           { Push-Location ..; try { docker compose logs -f } finally { Pop-Location } }
    'down'           { Push-Location ..; try { docker compose down } finally { Pop-Location } }

    'env-file' {
        # Never clobber an existing .env: it holds the only copy of the API
        # key, and a task runner that silently overwrites secrets is one
        # nobody can trust. Re-running this is always safe.
        $envPath = Join-Path $PSScriptRoot '.env'
        if (Test-Path -LiteralPath $envPath) {
            Write-Host "backend\.env already exists - leaving it alone." -ForegroundColor Yellow
        } else {
            Copy-Item -LiteralPath (Join-Path $PSScriptRoot '.env.example') -Destination $envPath
            Write-Host "created backend\.env - add UNOROUTER_API_KEY (optional)." -ForegroundColor Green
        }
    }

    'which-python' {
        # First thing to check when an import fails for a package that
        # `conda list` says is installed.
        #
        # This delegates to doctor.py rather than passing `python -c "..."`
        # inline: PowerShell re-quotes arguments for native executables, and
        # embedded double quotes terminate that wrapping early, so the snippet
        # arrives at python truncated. A plain script path has nothing to quote.
        Invoke-Py @('doctor.py', '--which')
    }

    'doctor'         { Invoke-Py @('doctor.py') }
    'env'            { conda env create -f environment.yaml }
    'env-update'     { conda env update -f environment.yaml --prune }

    'db-push'        { Invoke-Cli @('db', 'push') }
    'db-push-seed'   { Invoke-Cli @('db', 'push', '--seed') }
    'db-create'      { Invoke-Cli @('db', 'create') }
    'db-drop'        { if ($Force) { Invoke-Cli @('db','drop','--force') } else { Invoke-Cli @('db','drop') } }
    'db-status'      { Invoke-Cli @('db', 'status') }
    'db-inspect'     { Invoke-Cli @('db', 'inspect') }
    'db-seed'        { Invoke-Cli @('db', 'seed') }
    'db-reset'       { if ($Force) { Invoke-Cli @('db','reset','--force') } else { Invoke-Cli @('db','reset') } }
    'db-sql'         { Invoke-Cli @('db', 'sql', '--out', '..\schema.sql') }

    'migrate' {
        if (-not $Message) {
            Write-Host 'usage: .\make.ps1 migrate -m "your message"' -ForegroundColor Red
            exit 1
        }
        Invoke-Cli @('migrate', 'dev', '-m', $Message)
    }
    'migrate-deploy' { Invoke-Cli @('migrate', 'deploy') }
    'migrate-status' { Invoke-Cli @('migrate', 'status') }
    'migrate-down'   { Invoke-Cli @('migrate', 'down') }

    'test'           { Invoke-Py @('-m', 'pytest', '-q') }
    'lint'           { Invoke-Py @('-m', 'ruff', 'check', 'app', 'tests') }
    'run'            { Invoke-Py @('-m', 'uvicorn', 'app.main:app', '--reload', '--host', '0.0.0.0', '--port', '8000') }

    default {
        Write-Host "Unknown target: $Target" -ForegroundColor Red
        Show-Help
        exit 1
    }
}
