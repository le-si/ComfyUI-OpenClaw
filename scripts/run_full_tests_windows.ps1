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

function Invoke-Checked {
  param(
    [Parameter(Mandatory = $true)][string]$Label,
    [Parameter(Mandatory = $true)][scriptblock]$Command
  )
  & $Command
  if ($LASTEXITCODE -ne 0) {
    throw "[tests] ERROR: $Label failed with exit code $LASTEXITCODE"
  }
}

Require-Cmd node
Require-Cmd npm

# Prefer project-local virtualenv to avoid global PATH / cache conflicts on Windows.
$venvPython = Join-Path $root ".venv\Scripts\python.exe"
function New-ProjectVenv {
  Write-Host "[tests] Creating project venv at $root\.venv ..."
  if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 -m venv .venv
  }
  elseif (Get-Command python -ErrorAction SilentlyContinue) {
    & python -m venv .venv
  }
  else {
    throw "[tests] ERROR: no bootstrap Python found (need py or python)"
  }
}

function Test-VenvPython {
  param([string]$PythonExe)
  if (-not (Test-Path $PythonExe)) {
    return $false
  }
  try {
    & $PythonExe -c "import sys; print(sys.executable)" | Out-Null
    return $true
  }
  catch {
    return $false
  }
}

function Test-VenvCfgWindowsCompatible {
  $cfg = Join-Path $root ".venv\pyvenv.cfg"
  if (-not (Test-Path $cfg)) {
    return $false
  }
  try {
    $content = Get-Content $cfg -Raw
    # WSL/Linux-built venvs typically contain POSIX home paths (e.g. /usr/bin)
    if ($content -match "home\s*=\s*/") {
      return $false
    }
    return $true
  }
  catch {
    return $false
  }
}

if (-not (Test-Path $venvPython)) {
  New-ProjectVenv
}
elseif (-not (Test-VenvCfgWindowsCompatible)) {
  Write-Host "[tests] WARN: existing .venv was created from non-Windows interpreter; recreating ..."
  Remove-Item -Recurse -Force ".venv"
  New-ProjectVenv
}
elseif (-not (Test-VenvPython -PythonExe $venvPython)) {
  Write-Host "[tests] WARN: existing .venv is invalid for current OS/interpreter; recreating ..."
  Remove-Item -Recurse -Force ".venv"
  New-ProjectVenv
}

if (-not (Test-VenvPython -PythonExe $venvPython)) {
  throw "[tests] ERROR: project venv python is not runnable: $venvPython"
}

$hasPreCommit = $true
& $venvPython -m pre_commit --version | Out-Null
if ($LASTEXITCODE -ne 0) {
  $hasPreCommit = $false
}
if (-not $hasPreCommit) {
  Write-Host "[tests] Installing pre-commit into project venv ..."
  Invoke-Checked "pip install pre-commit" { & $venvPython -m pip install -U pip pre-commit }
}

$hasAiohttp = $true
& $venvPython -c "import aiohttp" | Out-Null
if ($LASTEXITCODE -ne 0) {
  $hasAiohttp = $false
}
if (-not $hasAiohttp) {
  Write-Host "[tests] Installing aiohttp into project venv ..."
  Invoke-Checked "pip install aiohttp" { & $venvPython -m pip install aiohttp }
}

$hasCrypto = $true
& $venvPython -c "import Cryptodome" | Out-Null
if ($LASTEXITCODE -ne 0) {
  $hasCrypto = $false
}
if (-not $hasCrypto) {
  Write-Host "[tests] Installing pycryptodomex into project venv (R82 AES) ..."
  Invoke-Checked "pip install pycryptodomex" { & $venvPython -m pip install pycryptodomex }
}

$hasCryptography = $true
& $venvPython -c "import cryptography" | Out-Null
if ($LASTEXITCODE -ne 0) {
  $hasCryptography = $false
}
if (-not $hasCryptography) {
  Write-Host "[tests] Installing cryptography into project venv (S57 Fernet AEAD) ..."
  Invoke-Checked "pip install cryptography" { & $venvPython -m pip install cryptography }
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

Write-Host "[tests] 0/4 R120 dependency preflight"
Invoke-Checked "preflight_check" { & $venvPython scripts\preflight_check.py --strict }

Write-Host "[tests] 1/4 detect-secrets"
Invoke-Checked "detect-secrets" { & $venvPython -m pre_commit run detect-secrets --all-files }

Write-Host "[tests] 2/4 pre-commit all hooks (pass 1: autofix)"
& $venvPython -m pre_commit run --all-files --show-diff-on-failure
if ($LASTEXITCODE -ne 0) {
  Write-Host "[tests] INFO: pre-commit reported changes/issues; running pass 2 verification..."
  Invoke-Checked "pre-commit all hooks (pass 2 verify)" { & $venvPython -m pre_commit run --all-files --show-diff-on-failure }
}

Write-Host "[tests] 3/4 backend unit tests"
$env:MOLTBOT_STATE_DIR = "$root\moltbot_state\_local_unit"
Invoke-Checked "backend unit tests" {
  & $venvPython scripts\run_unittests.py --start-dir tests --pattern "test_*.py" --enforce-skip-policy tests\skip_policy.json
}

if ($env:OPENCLAW_IMPL_RECORD_PATH) {
  Write-Host "[tests] 3.5/4 implementation record lint (strict)"
  # IMPORTANT: strict mode is opt-in via OPENCLAW_IMPL_RECORD_PATH to avoid retroactive legacy record failures.
  Invoke-Checked "implementation record lint" {
    & $venvPython scripts\lint_implementation_record.py --path $env:OPENCLAW_IMPL_RECORD_PATH --strict
  }
}

Write-Host "[tests] 4/4 frontend E2E"
Invoke-Checked "frontend E2E" { npm test }

Write-Host "[tests] PASS"
