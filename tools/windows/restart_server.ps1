$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'project_paths.ps1')
$projectRoot = Get-MrpProjectRoot
& (Join-Path $PSScriptRoot 'setup.ps1')
$dataRoot = Get-MrpDataRoot
$env:MRP_DATA_ROOT = $dataRoot
Set-Location -LiteralPath $projectRoot
. (Join-Path $PSScriptRoot 'server_processes.ps1')

$port = 8000
if ($env:MRP_PORT) {
    $parsedPort = 0
    if (-not [int]::TryParse($env:MRP_PORT, [ref]$parsedPort) -or $parsedPort -lt 1 -or $parsedPort -gt 65535) {
        throw 'MRP_PORT must be a valid TCP port number.'
    }
    $port = $parsedPort
}

$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw 'Project Python environment is missing. Set up .venv before using this launcher.'
}

# Rebuild the web client only when source files are newer than the existing bundle.
$webRoot = Join-Path $projectRoot 'src\web'
$webSource = Join-Path $webRoot 'src'
$distIndex = Join-Path $webRoot 'dist\index.html'
$needsBuild = -not (Test-Path -LiteralPath $distIndex -PathType Leaf)
if (-not $needsBuild -and (Test-Path -LiteralPath $webSource -PathType Container)) {
    $newestSource = Get-ChildItem -LiteralPath $webSource -File -Recurse | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
    $distInfo = Get-Item -LiteralPath $distIndex
    $needsBuild = $newestSource -and $newestSource.LastWriteTimeUtc -gt $distInfo.LastWriteTimeUtc
}
if ($needsBuild) {
    $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $npm) { throw 'The frontend needs a build, but npm.cmd is not available.' }
    Write-Host 'Building the web interface...'
    Push-Location $webRoot
    try {
        & $npm.Source run build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
    }
    finally { Pop-Location }
}

# Stop only a listener whose process tree leads to this project's venv launcher.
$listenerPids = @(
    Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
)
$listenerRecords = @(Get-ProjectMrpPortListener -Port $port -ProjectPythonPath $pythonPath)
$unknownPids = @($listenerRecords | Where-Object { -not $_.VerifiedProjectProcessTree } | Select-Object -ExpandProperty ListenerProcessId)
if ($listenerPids.Count -ne $listenerRecords.Count -or $unknownPids.Count -gt 0) {
    throw "Port $port is occupied by a process that cannot be verified as this project's MRP process tree (PID $($listenerPids -join ', ')); nothing was stopped."
}
$rootRecords = @($listenerRecords | Sort-Object RootProcessId -Unique)
$rootPids = @($rootRecords | Select-Object -ExpandProperty RootProcessId -Unique)
if ($rootPids.Count -gt 0) {
    Write-Host "Found this project's MRP process tree on port $port (listener PID $($listenerPids -join ', '); launcher PID $($rootPids -join ', '))."
    $answer = Read-Host 'Stop it and start the updated project now? [Y/N]'
    if ($answer -notmatch '^(?i:y|yes)$') { Write-Host 'Cancelled; the existing server was left running.'; exit 0 }
    foreach ($record in $rootRecords) {
        Stop-ProjectMrpProcessTree `
            -RootProcessId ([int]$record.RootProcessId) `
            -ProjectPythonPath $pythonPath `
            -ExpectedRootCreationDate $record.RootCreationDate
    }
    $stopDeadline = (Get-Date).AddSeconds(15)
    do {
        Start-Sleep -Milliseconds 400
        $stillListening = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
    } while ($stillListening -and (Get-Date) -lt $stopDeadline)
    if ($stillListening) { throw "Port $port is still occupied; the new server was not started." }
}

# The ordinary launcher stays local-only even when its parent shell has stale LAN vars.
$env:MRP_HOST = '127.0.0.1'
$env:MRP_LAN_MODE = '0'
foreach ($name in @('MRP_LAN_ACCESS_CODE', 'MRP_LAN_TLS_STORE_DIR', 'MRP_LAN_TLS_CERT_FILE', 'MRP_LAN_TLS_KEY_FILE', 'MRP_LAN_TLS_ROOT_FILE', 'MRP_LAN_BIND_IP', 'MRP_LAN_SUBNET', 'MRP_LAN_INTERFACE_INDEX', 'MRP_LAN_INTERFACE_ALIAS', 'MRP_LAN_FIREWALL_STATUS_FILE')) {
    Remove-Item "Env:$name" -ErrorAction SilentlyContinue
}
$env:MRP_PORT = [string]$port
$logRoot = Join-Path $env:LOCALAPPDATA 'Sekai o Tsumugu Hime\logs'
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$stdoutLog = Join-Path $logRoot "server-$stamp.out.log"
$stderrLog = Join-Path $logRoot "server-$stamp.err.log"
$server = Start-Process -FilePath $pythonPath `
    -ArgumentList @('-m', 'mrp.server.main') `
    -WorkingDirectory $projectRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog `
    -PassThru

$healthUrl = "http://127.0.0.1:$port/api/v1/health"
$ready = $false
for ($attempt = 0; $attempt -lt 35; $attempt++) {
    if ($server.HasExited) { break }
    try {
        $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2
        if ($health.ok -eq $true) { $ready = $true; break }
    }
    catch { }
    Start-Sleep -Seconds 1
}

if (-not $ready) {
    Write-Host "Server did not become ready. Logs: $stderrLog"
    if (Test-Path -LiteralPath $stderrLog) { Get-Content -LiteralPath $stderrLog -Tail 35 }
    $startedRoot = Get-CimInstance Win32_Process -Filter "ProcessId = $($server.Id)" -ErrorAction SilentlyContinue
    if (Test-ProjectMrpModuleProcess -Process $startedRoot -ProjectPythonPath $pythonPath) {
        Stop-ProjectMrpProcessTree -RootProcessId $server.Id -ProjectPythonPath $pythonPath -ExpectedRootCreationDate $startedRoot.CreationDate
    }
    exit 1
}

$activeListeners = @(Get-ProjectMrpPortListener -Port $port -ProjectPythonPath $pythonPath -ExpectedRootProcessId $server.Id)
$activeOwners = @($activeListeners | Where-Object { $_.VerifiedProjectProcessTree } | Select-Object -ExpandProperty ListenerProcessId -Unique)
if ($activeListeners.Count -ne 1 -or $activeOwners.Count -ne 1) {
    Write-Host 'The health endpoint responded, but its TCP listener could not be tied to the process started by this launcher.'
    $startedRoot = Get-CimInstance Win32_Process -Filter "ProcessId = $($server.Id)" -ErrorAction SilentlyContinue
    if (Test-ProjectMrpModuleProcess -Process $startedRoot -ProjectPythonPath $pythonPath) {
        Stop-ProjectMrpProcessTree -RootProcessId $server.Id -ProjectPythonPath $pythonPath -ExpectedRootCreationDate $startedRoot.CreationDate
    }
    exit 1
}

Write-Host "世界を紡ぐ姫 is ready at http://127.0.0.1:$port/"
Write-Host "Launcher PID: $($server.Id); TCP listener PID: $($activeOwners[0])"
Write-Host "Logs: $logRoot"
