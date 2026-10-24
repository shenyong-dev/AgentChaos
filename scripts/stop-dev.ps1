$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
foreach ($service in @('api', 'frontend')) {
    $pidFile = Join-Path $projectRoot ".local\$service.pid"
    if (-not (Test-Path -LiteralPath $pidFile)) { continue }
    $serviceProcessId = [int](Get-Content -LiteralPath $pidFile)
    $processRecord = Get-CimInstance Win32_Process -Filter "ProcessId=$serviceProcessId" -ErrorAction SilentlyContinue
    if ($processRecord -and $processRecord.CommandLine.Contains($projectRoot)) {
        Stop-Process -Id $serviceProcessId
        Write-Output "Stopped project $service process."
    }
}
