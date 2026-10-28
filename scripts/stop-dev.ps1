$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
foreach ($service in @('api', 'frontend')) {
    $pidFile = Join-Path $projectRoot ".local\$service.pid"
    if (-not (Test-Path -LiteralPath $pidFile)) { continue }
    $serviceProcessId = [int](Get-Content -LiteralPath $pidFile)
    $processRecord = Get-CimInstance Win32_Process -Filter "ProcessId=$serviceProcessId" -ErrorAction SilentlyContinue
    if ($processRecord -and $processRecord.CommandLine.Contains($projectRoot)) {
        # A Windows venv launcher owns the actual Python server child process.
        # Stop only this verified project process tree, including that child.
        & taskkill.exe /PID $serviceProcessId /T /F | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Could not stop project $service process tree." }
        Remove-Item -LiteralPath $pidFile
        Write-Output "Stopped project $service process."
    }
}
