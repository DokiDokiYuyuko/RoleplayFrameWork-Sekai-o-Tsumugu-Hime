#requires -RunAsAdministrator
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Enable', 'Disable')]
    [string]$Action,
    [ValidateRange(1, 65535)]
    [int]$Port = 8000,
    [string]$LocalAddress,
    [string]$InterfaceAlias,
    [string]$RemoteSubnet,
    [string]$IgnoredRuleName = '',
    [string]$DiagnosticFile
)

$ErrorActionPreference = 'Stop'
trap {
    $failureText = [string]$_.Exception.Message
    if ($DiagnosticFile) {
        try {
            [System.IO.File]::WriteAllText($DiagnosticFile, $failureText, [System.Text.UTF8Encoding]::new($false))
        } catch { }
    }
    [Console]::Error.WriteLine($failureText)
    exit 1
}
. (Join-Path $PSScriptRoot 'project_paths.ps1')
$projectRoot = Get-MrpProjectRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
. (Join-Path $PSScriptRoot 'server_processes.ps1')
$ruleName = "Sekai o Tsumugu Hime LAN TCP $Port"
$ruleGroup = 'Sekai o Tsumugu Hime LAN access'

function Test-Rfc1918IPv4 {
    param([Parameter(Mandatory = $true)][string]$Address)
    try { $bytes = [System.Net.IPAddress]::Parse($Address).GetAddressBytes() }
    catch { return $false }
    if ($bytes.Length -ne 4) { return $false }
    return ($bytes[0] -eq 10) -or
        ($bytes[0] -eq 172 -and $bytes[1] -ge 16 -and $bytes[1] -le 31) -or
        ($bytes[0] -eq 192 -and $bytes[1] -eq 168)
}

function Test-AddressInSubnet {
    param(
        [Parameter(Mandatory = $true)][string]$Address,
        [Parameter(Mandatory = $true)][string]$Subnet
    )
    try {
        $parts = $Subnet.Split('/')
        if ($parts.Count -ne 2) { return $false }
        $addressBytes = [System.Net.IPAddress]::Parse($Address).GetAddressBytes()
        $networkBytes = [System.Net.IPAddress]::Parse($parts[0]).GetAddressBytes()
        $prefix = [int]$parts[1]
        if ($addressBytes.Length -ne 4 -or $networkBytes.Length -ne 4 -or $prefix -lt 8 -or $prefix -gt 30) { return $false }
        for ($index = 0; $index -lt 4; $index++) {
            $bits = [Math]::Max(0, [Math]::Min(8, $prefix - 8 * $index))
            $mask = if ($bits -eq 0) { 0 } else { (255 -shl (8 - $bits)) -band 255 }
            if (($addressBytes[$index] -band $mask) -ne ($networkBytes[$index] -band $mask)) { return $false }
        }
        return $true
    } catch { return $false }
}

function Test-RuleMatchesProgramPort {
    param(
        [Parameter(Mandatory = $true)]$Rule,
        [Parameter(Mandatory = $true)][string]$ProgramPath,
        [Parameter(Mandatory = $true)][int]$LocalPort
    )
    try {
        $app = @(Get-NetFirewallApplicationFilter -AssociatedNetFirewallRule $Rule -ErrorAction Stop)
        $ports = @(Get-NetFirewallPortFilter -AssociatedNetFirewallRule $Rule -ErrorAction Stop)
        if ($app.Count -ne 1 -or $ports.Count -ne 1) { return $false }
        $program = [string]$app[0].Program
        $expectedProgram = [System.IO.Path]::GetFullPath($ProgramPath)
        if (-not $program -or -not [System.StringComparer]::OrdinalIgnoreCase.Equals([System.IO.Path]::GetFullPath($program), $expectedProgram)) { return $false }
        $protocol = [string]$ports[0].Protocol
        if ($protocol -notin @('TCP', '6', 'Any', '256')) { return $false }
        $localPorts = @($ports[0].LocalPort | ForEach-Object { [string]$_ })
        return ($localPorts -contains 'Any') -or ($localPorts -contains [string]$LocalPort)
    } catch { return $false }
}

if ($Action -eq 'Disable') {
    $existing = @(Get-NetFirewallRule -PolicyStore PersistentStore -ErrorAction Stop |
        Where-Object { [string]$_.Group -eq $ruleGroup -and [string]$_.DisplayName -eq $ruleName })
    if ($existing.Count -gt 0) {
        $removedNames = @($existing | ForEach-Object { [string]$_.Name })
        foreach ($name in $removedNames) {
            Remove-NetFirewallRule -Name $name -PolicyStore PersistentStore -ErrorAction Stop
        }
        $remaining = @(Get-NetFirewallRule -PolicyStore PersistentStore -ErrorAction Stop |
            Where-Object { [string]$_.Group -eq $ruleGroup -and [string]$_.DisplayName -eq $ruleName })
        if ($remaining.Count -gt 0) {
            $remainingNames = @($remaining | ForEach-Object { [string]$_.Name }) -join ', '
            throw "Could not remove owned persistent firewall rule(s): $remainingNames."
        }
        if ($DiagnosticFile) {
            [System.IO.File]::WriteAllText($DiagnosticFile, "Removed owned persistent rule(s): $($removedNames -join ', ').", [System.Text.UTF8Encoding]::new($false))
        }
        Write-Host "已撤销本项目创建的局域网规则（$($removedNames.Count) 条）。"
    } else {
        if ($DiagnosticFile) {
            [System.IO.File]::WriteAllText($DiagnosticFile, 'No owned persistent firewall rule required removal.', [System.Text.UTF8Encoding]::new($false))
        }
        Write-Host '本项目没有需要撤销的局域网规则。'
    }
    exit 0
}

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw 'Project Python environment is missing.'
}
if (-not $LocalAddress -or -not $InterfaceAlias -or -not $RemoteSubnet -or
    -not (Test-Rfc1918IPv4 -Address $LocalAddress) -or
    -not (Test-AddressInSubnet -Address $LocalAddress -Subnet $RemoteSubnet)) {
    throw 'Enable requires the selected RFC 1918 address, physical adapter name, and containing subnet.'
}

$adapter = Get-NetAdapter -Name $InterfaceAlias -Physical -ErrorAction Stop
if ([string]$adapter.Status -ne 'Up') { throw "Selected physical adapter '$InterfaceAlias' is no longer Up." }
$interfaceIndex = [int]$adapter.ifIndex
$addressPresent = @(Get-NetIPAddress -InterfaceIndex $interfaceIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { [string]$_.IPAddress -eq $LocalAddress })
if ($addressPresent.Count -ne 1) { throw 'Selected IPv4 address is no longer assigned to the selected physical adapter.' }

$bindings = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
if ($bindings.Count -eq 0) {
    throw "No TCP listener was found on port $Port. Start this project's selected-interface MRP service first."
}
$bindingAddresses = @($bindings | ForEach-Object { [string]$_.LocalAddress } | Sort-Object -Unique)
$unexpectedBindings = @($bindingAddresses | Where-Object { $_ -notin @($LocalAddress, '127.0.0.1') })
if ($unexpectedBindings.Count -gt 0 -or $bindingAddresses -notcontains $LocalAddress -or
    $bindingAddresses -notcontains '127.0.0.1') {
    throw "The listener on port $Port must bind only to $LocalAddress and 127.0.0.1; no firewall rule was changed."
}
$listeners = @(Get-ProjectMrpPortListener -Port $Port -ProjectPythonPath $pythonPath)
$unverified = @($listeners | Where-Object { -not $_.VerifiedProjectProcessTree })
$programPaths = @($listeners | Where-Object { $_.VerifiedProjectProcessTree } | Select-Object -ExpandProperty ListenerExecutablePath -Unique)
if ($listeners.Count -ne 1 -or $unverified.Count -gt 0 -or $programPaths.Count -ne 1) {
    throw "The listener on port $Port could not be verified as this project's single MRP process tree; no firewall rule was changed."
}
$programPath = [System.IO.Path]::GetFullPath($programPaths[0])

# Remove only rules this launcher owns, then reject any other enabled inbound
# allow rule that can open this same program/port more broadly.
$managed = @(Get-NetFirewallRule -PolicyStore PersistentStore -ErrorAction Stop |
    Where-Object { [string]$_.Group -eq $ruleGroup -and [string]$_.DisplayName -eq $ruleName })
if ($managed.Count -gt 0) { $managed | Remove-NetFirewallRule }

$conflicts = @()
$programFilters = @(Get-NetFirewallApplicationFilter -PolicyStore ActiveStore -ErrorAction Stop |
    Where-Object {
        $candidateProgram = [string]$_.Program
        if (-not $candidateProgram) { return $false }
        try {
            [System.StringComparer]::OrdinalIgnoreCase.Equals(
                [System.IO.Path]::GetFullPath($candidateProgram), $programPath)
        } catch { $false }
    })
$activeAllows = @(foreach ($filter in $programFilters) {
    Get-NetFirewallRule -PolicyStore ActiveStore -AssociatedNetFirewallApplicationFilter $filter -ErrorAction Stop |
        Where-Object { [string]$_.Direction -eq 'Inbound' -and [string]$_.Action -eq 'Allow' -and [string]$_.Enabled -eq 'True' }
})
foreach ($rule in $activeAllows) {
    if ([string]$rule.Group -eq $ruleGroup -or -not (Test-RuleMatchesProgramPort -Rule $rule -ProgramPath $programPath -LocalPort $Port)) { continue }
    if ($IgnoredRuleName -and [System.StringComparer]::OrdinalIgnoreCase.Equals([string]$rule.Name, $IgnoredRuleName)) { continue }
    try {
        $addresses = @(Get-NetFirewallAddressFilter -AssociatedNetFirewallRule $rule -ErrorAction Stop)
        $interfaces = @(Get-NetFirewallInterfaceFilter -AssociatedNetFirewallRule $rule -ErrorAction Stop)
        if ($addresses.Count -ne 1 -or $interfaces.Count -ne 1) { $conflicts += [string]$rule.DisplayName; continue }
        $locals = @($addresses[0].LocalAddress | ForEach-Object { [string]$_ })
        $remotes = @($addresses[0].RemoteAddress | ForEach-Object { [string]$_ })
        $aliases = @($interfaces[0].InterfaceAlias | ForEach-Object { [string]$_ })
        $isNarrow = $locals.Count -eq 1 -and $locals[0] -eq $LocalAddress -and
            $remotes.Count -eq 1 -and $remotes[0] -eq $RemoteSubnet -and
            $aliases.Count -eq 1 -and $aliases[0] -eq $InterfaceAlias -and
            [string]$rule.EdgeTraversalPolicy -eq 'Block'
        if (-not $isNarrow) { $conflicts += [string]$rule.DisplayName }
    } catch { $conflicts += [string]$rule.DisplayName }
}
if ($conflicts.Count -gt 0) {
    throw ("An enabled inbound rule can also allow this Python/port more broadly ({0}); remove or narrow that rule before enabling LAN access." -f (($conflicts | Sort-Object -Unique) -join ', '))
}

try {
    New-NetFirewallRule `
        -DisplayName $ruleName `
        -Group $ruleGroup `
        -Direction Inbound `
        -Action Allow `
        -Enabled True `
        -Profile Any `
        -Program $programPath `
        -Protocol TCP `
        -LocalPort $Port `
        -LocalAddress $LocalAddress `
        -InterfaceAlias $InterfaceAlias `
        -RemoteAddress $RemoteSubnet `
        -EdgeTraversalPolicy Block | Out-Null

    $created = @(Get-NetFirewallRule -PolicyStore ActiveStore -DisplayName $ruleName -ErrorAction SilentlyContinue)
    if ($created.Count -ne 1 -or [string]$created[0].Group -ne $ruleGroup) {
        throw 'The firewall rule could not be uniquely verified after creation.'
    }
    Write-Host "已允许 $programPath TCP/$Port，仅限 $InterfaceAlias 上的 $LocalAddress，客户端网段 $RemoteSubnet，Edge Traversal=Block。"
} catch {
    Get-NetFirewallRule -PolicyStore PersistentStore -ErrorAction SilentlyContinue |
        Where-Object { [string]$_.Group -eq $ruleGroup -and [string]$_.DisplayName -eq $ruleName } | Remove-NetFirewallRule -ErrorAction SilentlyContinue
    throw
}
