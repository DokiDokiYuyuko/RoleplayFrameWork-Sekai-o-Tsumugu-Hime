param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8000
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'project_paths.ps1')
$projectRoot = Get-MrpProjectRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
. (Join-Path $PSScriptRoot 'server_processes.ps1')
$firewallScript = Join-Path $PSScriptRoot 'manage_lan_firewall.ps1'
$firewallGroup = 'Sekai o Tsumugu Hime LAN access'
$firewallStatusFile = Join-Path $env:LOCALAPPDATA "Sekai o Tsumugu Hime\lan-status\lan-firewall-$Port.status"
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw 'Project Python environment is missing.'
}

function Remove-ProjectLanFirewallRules {
    $rules = @(Get-NetFirewallRule -PolicyStore PersistentStore -Group $firewallGroup -ErrorAction SilentlyContinue |
        Where-Object { [string]$_.DisplayName -like 'Sekai o Tsumugu Hime LAN TCP *' })
    if ($rules.Count -eq 0) { return }

    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [System.Security.Principal.WindowsPrincipal]::new($identity)
    if ($principal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)) {
        & $firewallScript -Action Disable -Port $Port
        if (-not $?) { throw 'Could not remove the project LAN firewall rules.' }
        return
    }

    try {
        $windowsPowerShell = Get-Command powershell.exe -ErrorAction Stop
        $elevated = Start-Process -FilePath $windowsPowerShell.Source `
            -ArgumentList @('-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"' + $firewallScript + '"'), '-Action', 'Disable', '-Port', [string]$Port) `
            -Verb RunAs -Wait -PassThru -WindowStyle Hidden
        if ($elevated.ExitCode -ne 0) { throw 'Elevated firewall cleanup returned an error.' }
    } catch {
        throw 'The listener stopped, but Windows did not authorize removal of the project firewall rule. Its current filters were not re-verified; inspect and remove the rule before enabling LAN again.'
    }
}

$owners = @(
    Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
)
if ($owners.Count -eq 0) {
    Remove-ProjectLanFirewallRules
    Remove-Item -LiteralPath $firewallStatusFile -Force -ErrorAction SilentlyContinue
    Write-Host "No listener was found on port $Port; any stale project LAN firewall rule was removed."
    exit 0
}

$listeners = @(Get-ProjectMrpPortListener -Port $Port -ProjectPythonPath $pythonPath)
$unverified = @($listeners | Where-Object { -not $_.VerifiedProjectProcessTree })
if ($listeners.Count -ne $owners.Count -or $unverified.Count -gt 0) {
    throw "Port $Port is occupied by a process that cannot be verified as this project's MRP process tree; nothing was stopped."
}

$rootRecords = @($listeners | Sort-Object RootProcessId -Unique)
foreach ($record in $rootRecords) {
    Stop-ProjectMrpProcessTree `
        -RootProcessId ([int]$record.RootProcessId) `
        -ProjectPythonPath $pythonPath `
        -ExpectedRootCreationDate $record.RootCreationDate
}

$deadline = (Get-Date).AddSeconds(15)
do {
    Start-Sleep -Milliseconds 400
    $stillListening = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
} while ($stillListening -and (Get-Date) -lt $deadline)
if ($stillListening) { throw "Port $Port is still listening; the process tree may not have stopped completely." }
Remove-ProjectLanFirewallRules
Remove-Item -LiteralPath $firewallStatusFile -Force -ErrorAction SilentlyContinue
Write-Host "Stopped this project's MRP process tree on TCP port $Port."
