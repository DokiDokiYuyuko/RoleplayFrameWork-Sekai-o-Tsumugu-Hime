param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8000
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'project_paths.ps1')
$projectRoot = Get-MrpProjectRoot
$firewallScript = Join-Path $PSScriptRoot 'manage_lan_firewall.ps1'

try {
    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [System.Security.Principal.WindowsPrincipal]::new($identity)
    if ($principal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)) {
        & $firewallScript -Action Disable -Port $Port
        if (-not $?) { exit 1 }
        exit 0
    }

    $windowsPowerShell = Get-Command powershell.exe -ErrorAction Stop
    $arguments = @(
        '-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass',
        '-File', ('"' + $firewallScript + '"'),
        '-Action', 'Disable', '-Port', [string]$Port
    )
    $elevated = Start-Process -FilePath $windowsPowerShell.Source `
        -ArgumentList $arguments `
        -Verb RunAs -Wait -PassThru -WindowStyle Hidden
    exit [int]$elevated.ExitCode
}
catch {
    Write-Error 'Windows did not authorize LAN firewall cleanup.'
    exit 1
}
