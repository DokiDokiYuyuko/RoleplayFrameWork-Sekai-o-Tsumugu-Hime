# Compatibility for services started before the directory migration.
param([ValidateRange(1,65535)][int]$Port = 8000)
& (Join-Path $PSScriptRoot 'tools\windows\disable_lan_firewall.ps1') -Port $Port
exit $LASTEXITCODE
