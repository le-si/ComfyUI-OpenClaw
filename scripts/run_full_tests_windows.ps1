Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path | Split-Path -Parent
Set-Location $root

Write-Host "[tests] repo: $root"

# Cache isolation for pre-commit + black
$env:PRE_COMMIT_HOME = if ($env:PRE_COMMIT_HOME) { $env:PRE_COMMIT_HOME } else { "$root\.tmp\pre-commit-win" }
$env:BLACK_CACHE_DIR = if ($env:BLACK_CACHE_DIR) { $env:BLACK_CACHE_DIR } else { "$root\.tmp\black-cache" }
New-Item -ItemType Directory -Force $env:PRE_COMMIT_HOME | Out-Null
New-Item -ItemType Directory -Force $env:BLACK_CACHE_DIR | Out-Null

function Require-Cmd($cmd) {
  if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
    throw "[tests] ERROR: missing command: $cmd"
  }
}

Require-Cmd node
Require-Cmd npm

# Prefer project-local virtualenv to avoid global PATH / cache conflicts on Windows.
$venvPython = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
  Write-Host "[tests] Creating project venv at $root\.venv ..."
  if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 -m venv .venv
  } elseif (Get-Command python -ErrorAction SilentlyContinue) {
    & python -m venv .venv
  } else {
    throw "[tests] ERROR: no bootstrap Python found (need py or python)"
  }
}

$hasPreCommit = $true
try {
  & $venvPython -m pre_commit --version | Out-Null
} catch {
  $hasPreCommit = $false
}
if (-not $hasPreCommit) {
  Write-Host "[tests] Installing pre-commit into project venv ..."
  & $venvPython -m pip install -U pip pre-commit
}

$hasAiohttp = $true
try {
  & $venvPython -c "import aiohttp" | Out-Null
} catch {
  $hasAiohttp = $false
}
if (-not $hasAiohttp) {
  Write-Host "[tests] Installing aiohttp into project venv ..."
  & $venvPython -m pip install aiohttp
}

# Ensure Node >= 18
$nodeMajor = [int]((& node -p "process.versions.node.split('.')[0]").Trim())
if ($nodeMajor -lt 18) {
  Write-Host "[tests] WARN: Node < 18 detected. Trying nvm use 18..."
  if (Get-Command nvm -ErrorAction SilentlyContinue) {
    nvm use 18 | Out-Null
    $nodeMajor = [int]((& node -p "process.versions.node.split('.')[0]").Trim())
  }
}
if ($nodeMajor -lt 18) {
  throw "[tests] ERROR: Node >=18 required, current=$(node -v)"
}

Write-Host "[tests] Node version: $(node -v)"

Write-Host "[tests] 1/4 detect-secrets"
& $venvPython -m pre_commit run detect-secrets --all-files

Write-Host "[tests] 2/4 pre-commit all hooks"
& $venvPython -m pre_commit run --all-files --show-diff-on-failure

Write-Host "[tests] 3/4 backend unit tests"
$env:MOLTBOT_STATE_DIR = "$root\moltbot_state\_local_unit"
& $venvPython scripts\run_unittests.py --start-dir tests --pattern "test_*.py"

Write-Host "[tests] 4/4 frontend E2E"
npm test

Write-Host "[tests] PASS"
