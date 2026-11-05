param(
    [ValidateSet('start', 'stop', 'status', 'logs')][string]$Action = 'status',
    [ValidatePattern('^[a-z][a-z0-9-]{0,39}$')][string]$Profile = 'main'
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot 'backend\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Existing D: project Python environment is required.' }
$oldPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = Join-Path $projectRoot 'backend'
$env:TEMP = Join-Path $projectRoot '.cache\tmp'
$env:TMP = $env:TEMP
try {
    if ($Action -eq 'start') {
        & (Join-Path $PSScriptRoot 'start-linux-engine.ps1')
        if ($LASTEXITCODE -ne 0) { throw 'Existing Docker Linux engine is not ready.' }
    }
    & $python -m operations.workers $Action --profile $Profile
    if ($LASTEXITCODE -ne 0) { throw 'Worker operation could not complete; see the sanitized status above.' }
} finally { $env:PYTHONPATH = $oldPythonPath }
