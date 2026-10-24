$ErrorActionPreference = 'Stop'
$runtimeRoot = 'D:\RedisSearch'
$executable = Join-Path $runtimeRoot 'memurai.exe'
$configuration = Join-Path $runtimeRoot 'memurai.conf'
if (-not (Test-Path -LiteralPath $executable) -or -not (Test-Path -LiteralPath $configuration)) {
    throw 'Native Windows runtime is not configured. See docs/environment.md.'
}
$listener = Get-NetTCPConnection -State Listen -LocalPort 16380 -ErrorAction SilentlyContinue
if ($listener) {
    Write-Output 'Port 16380 is already listening; verify its identity through the API health check.'
    exit 0
}
$runtimeProcess = Start-Process -FilePath $executable -ArgumentList $configuration `
    -WorkingDirectory $runtimeRoot -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $runtimeRoot 'runtime-stdout.log') `
    -RedirectStandardError (Join-Path $runtimeRoot 'runtime-stderr.log')
$runtimeProcess.Id | Set-Content -LiteralPath (Join-Path $runtimeRoot 'runtime.pid')
Write-Output 'Started native Windows JSON/Search runtime on port 16380. Verify /api/v1/health.'
