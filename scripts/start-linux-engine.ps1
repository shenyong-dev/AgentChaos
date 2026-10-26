# Reuse the installed Linux engine and D: data. No independent WSL installation.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$dockerRoot = 'D:\DockerDesktop'
$dockerCli = Join-Path $dockerRoot 'resources\bin\docker.exe'
$backend = Join-Path $dockerRoot 'resources\com.docker.backend.exe'
$localRoot = Join-Path $projectRoot '.local\t05'
$cliConfig = Join-Path $localRoot 'docker-config'
$profile = Join-Path $localRoot 'docker-profile'
$endpoint = 'npipe:////./pipe/dockerDesktopLinuxEngine'
if (-not (Test-Path -LiteralPath $dockerCli) -or -not (Test-Path -LiteralPath $backend)) {
    throw 'The existing D:\DockerDesktop installation is required; nothing is installed here.'
}
New-Item -ItemType Directory -Force -Path $cliConfig | Out-Null
$configFile = Join-Path $cliConfig 'config.json'
if (-not (Test-Path -LiteralPath $configFile)) {
    [IO.File]::WriteAllText($configFile, '{"auths":{}}', [Text.UTF8Encoding]::new($false))
}
& $dockerCli --config $cliConfig --host $endpoint info --format '{{.OSType}}' 2>$null
if ($LASTEXITCODE -eq 0) { Write-Output 'Existing Linux engine is ready.'; exit 0 }
$existingDisk = 'D:\docker-desktop\data\wsl\disk\docker_data.vhdx'
if (-not (Test-Path -LiteralPath $existingDisk)) { throw 'Existing D: engine data disk is missing.' }
$registered = Get-ChildItem 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss' |
    ForEach-Object { Get-ItemProperty -LiteralPath $_.PSPath } |
    Where-Object { $_.DistributionName -eq 'docker-desktop' }
if (-not $registered -or $registered.BasePath -notlike '*D:\docker-desktop\*') {
    throw 'Expected existing D: docker-desktop registration was not found; no distro is created.'
}
$localProfile = Join-Path $profile 'local'
$roamingProfile = Join-Path $profile 'roaming'
New-Item -ItemType Directory -Force -Path $localProfile,$roamingProfile | Out-Null
$previousLocal = $env:LOCALAPPDATA
$previousRoaming = $env:APPDATA
try {
    # Child process only. The user's global profile/junctions are not modified.
    $env:LOCALAPPDATA = $localProfile
    $env:APPDATA = $roamingProfile
    $process = Start-Process -FilePath $backend -ArgumentList '-with-frontend=false' `
        -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $localRoot 'docker-engine.stdout.log') `
        -RedirectStandardError (Join-Path $localRoot 'docker-engine.stderr.log')
    $process.Id | Set-Content -LiteralPath (Join-Path $localRoot 'docker-engine.pid')
} finally {
    $env:LOCALAPPDATA = $previousLocal
    $env:APPDATA = $previousRoaming
}
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    & $dockerCli --config $cliConfig --host $endpoint info --format '{{.OSType}}' 2>$null
    if ($LASTEXITCODE -eq 0) { Write-Output 'Existing D: Linux engine is ready.'; exit 0 }
    Start-Sleep -Seconds 1
}
throw 'Engine did not become ready. Inspect the ignored .local\t05 logs; no reinstall is attempted.'
