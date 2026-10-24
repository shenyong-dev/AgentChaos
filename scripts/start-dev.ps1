$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$localRoot = Join-Path $projectRoot '.local'
$temporaryRoot = Join-Path $projectRoot '.cache\tmp'
New-Item -ItemType Directory -Path $localRoot, $temporaryRoot -Force | Out-Null
$env:TEMP = $temporaryRoot
$env:TMP = $temporaryRoot
$env:UV_CACHE_DIR = Join-Path $projectRoot '.cache\uv'
$env:npm_config_cache = Join-Path $projectRoot '.cache\npm'
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $projectRoot '.cache\playwright'
$pythonPath = Join-Path $projectRoot 'backend\.venv\Scripts\python.exe'
$vitePath = Join-Path $projectRoot 'frontend\node_modules\vite\bin\vite.js'
if (-not (Test-Path -LiteralPath $pythonPath) -or -not (Test-Path -LiteralPath $vitePath)) {
    throw 'Install locked dependencies first; see README.md.'
}
foreach ($port in @(8000, 5173)) {
    if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) {
        throw "Port $port is already occupied; no existing process was changed."
    }
}
$apiProcess = Start-Process -FilePath $pythonPath `
    -ArgumentList '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8000' `
    -WorkingDirectory (Join-Path $projectRoot 'backend') -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $localRoot 'api.stdout.log') `
    -RedirectStandardError (Join-Path $localRoot 'api.stderr.log')
$apiProcess.Id | Set-Content -LiteralPath (Join-Path $localRoot 'api.pid')
$webProcess = Start-Process -FilePath (Get-Command node).Source `
    -ArgumentList $vitePath, '--host', '127.0.0.1', '--port', '5173', '--strictPort' `
    -WorkingDirectory (Join-Path $projectRoot 'frontend') -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $localRoot 'frontend.stdout.log') `
    -RedirectStandardError (Join-Path $localRoot 'frontend.stderr.log')
$webProcess.Id | Set-Content -LiteralPath (Join-Path $localRoot 'frontend.pid')
Write-Output 'Started API http://127.0.0.1:8000 and frontend http://127.0.0.1:5173.'
