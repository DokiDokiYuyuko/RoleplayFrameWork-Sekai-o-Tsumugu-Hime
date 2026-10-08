param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8000
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'project_paths.ps1')
$projectRoot = Get-MrpProjectRoot
$dataRoot = Get-MrpDataRoot
$env:MRP_DATA_ROOT = $dataRoot
Set-Location -LiteralPath $projectRoot
. (Join-Path $PSScriptRoot 'server_processes.ps1')

if ($env:MRP_PORT -and $Port -eq 8000) {
    $parsedPort = 0
    if (-not [int]::TryParse($env:MRP_PORT, [ref]$parsedPort) -or $parsedPort -lt 1 -or $parsedPort -gt 65535) {
        throw 'MRP_PORT must be a valid TCP port number.'
    }
    $Port = $parsedPort
}

$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw 'Project Python environment is missing. Set up .venv before using this launcher.'
}
$previousServerWasStopped = $false

function Restore-LoopbackMrpServer {
    if (-not $script:previousServerWasStopped) { return }
    Write-Host 'Restoring the ordinary loopback-only service on the same port.'
    foreach ($name in @('MRP_LAN_ACCESS_CODE', 'MRP_LAN_MODE', 'MRP_LAN_BIND_IP', 'MRP_LAN_SUBNET', 'MRP_LAN_INTERFACE_INDEX', 'MRP_LAN_INTERFACE_ALIAS', 'MRP_LAN_FIREWALL_STATUS_FILE', 'MRP_LAN_TLS_STORE_DIR', 'MRP_LAN_TLS_CERT_FILE', 'MRP_LAN_TLS_KEY_FILE', 'MRP_LAN_TLS_ROOT_FILE')) {
        Remove-Item "Env:$name" -ErrorAction SilentlyContinue
    }
    $env:MRP_HOST = '127.0.0.1'
    $env:MRP_PORT = [string]$Port
    $logRoot = Join-Path $env:LOCALAPPDATA 'Sekai o Tsumugu Hime\logs'
    New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    Start-Process -FilePath $pythonPath `
        -ArgumentList @('-m', 'mrp.server.main') `
        -WorkingDirectory $projectRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logRoot "local-restore-$stamp.out.log") `
        -RedirectStandardError (Join-Path $logRoot "local-restore-$stamp.err.log") | Out-Null
}
Remove-Item Env:MRP_LAN_ACCESS_CODE -ErrorAction SilentlyContinue
foreach ($name in @('MRP_LAN_TLS_STORE_DIR', 'MRP_LAN_TLS_CERT_FILE', 'MRP_LAN_TLS_KEY_FILE', 'MRP_LAN_TLS_ROOT_FILE')) {
    Remove-Item "Env:$name" -ErrorAction SilentlyContinue
}
$ruleName = "Sekai o Tsumugu Hime LAN TCP $Port"
$ruleGroup = 'Sekai o Tsumugu Hime LAN access'
$exceptionFile = Join-Path $env:LOCALAPPDATA 'Sekai o Tsumugu Hime\lan-firewall-exceptions.json'
$ignoredExistingRuleName = ''
if (Test-Path -LiteralPath $exceptionFile -PathType Leaf) {
    try {
        $exceptionConfig = Get-Content -LiteralPath $exceptionFile -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
        $ignoredExistingRuleName = [string]$exceptionConfig.ignored_rule_name
        if (-not $ignoredExistingRuleName -or $ignoredExistingRuleName.Length -gt 256) {
            throw 'ignored_rule_name must contain one exact firewall rule name.'
        }
    } catch {
        throw "Invalid local LAN firewall exception file $exceptionFile`: $($_.Exception.Message)"
    }
}

function Test-Rfc1918IPv4 {
    param([Parameter(Mandatory = $true)][string]$Address)
    try { $bytes = [System.Net.IPAddress]::Parse($Address).GetAddressBytes() }
    catch { return $false }
    if ($bytes.Length -ne 4) { return $false }
    return ($bytes[0] -eq 10) -or
        ($bytes[0] -eq 172 -and $bytes[1] -ge 16 -and $bytes[1] -le 31) -or
        ($bytes[0] -eq 192 -and $bytes[1] -eq 168)
}

function Get-IPv4Network {
    param(
        [Parameter(Mandatory = $true)][string]$Address,
        [Parameter(Mandatory = $true)][int]$PrefixLength
    )
    $bytes = [System.Net.IPAddress]::Parse($Address).GetAddressBytes()
    for ($byteIndex = 0; $byteIndex -lt 4; $byteIndex++) {
        $bits = [Math]::Max(0, [Math]::Min(8, $PrefixLength - (8 * $byteIndex)))
        $mask = if ($bits -eq 0) { 0 } else { (255 -shl (8 - $bits)) -band 255 }
        $bytes[$byteIndex] = [byte]($bytes[$byteIndex] -band $mask)
    }
    return "{0}/{1}" -f ([System.Net.IPAddress]::new($bytes).ToString()), $PrefixLength
}

$physicalAdapters = @(Get-NetAdapter -Physical -ErrorAction SilentlyContinue | Where-Object Status -eq 'Up')
$physicalIndices = @($physicalAdapters | ForEach-Object { [int]$_.ifIndex })
$choices = @(
    Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object {
            $physicalIndices -contains [int]$_.InterfaceIndex -and
            (Test-Rfc1918IPv4 -Address ([string]$_.IPAddress)) -and
            [int]$_.PrefixLength -ge 8 -and [int]$_.PrefixLength -le 30
        } |
        ForEach-Object {
            $adapter = $physicalAdapters | Where-Object { [int]$_.ifIndex -eq [int]$_.InterfaceIndex } | Select-Object -First 1
            $profile = Get-NetConnectionProfile -InterfaceIndex ([int]$_.InterfaceIndex) -ErrorAction SilentlyContinue | Select-Object -First 1
            [pscustomobject]@{
                InterfaceIndex = [int]$_.InterfaceIndex
                InterfaceAlias = [string]$adapter.Name
                NetworkName = if ($profile) { [string]$profile.Name } else { [string]$adapter.Name }
                NetworkCategory = if ($profile) { [string]$profile.NetworkCategory } else { 'Unknown' }
                IPAddress = [string]$_.IPAddress
                PrefixLength = [int]$_.PrefixLength
                Subnet = Get-IPv4Network -Address ([string]$_.IPAddress) -PrefixLength ([int]$_.PrefixLength)
            }
        }
)
if ($choices.Count -eq 0) {
    throw 'No active physical adapter has an RFC 1918 IPv4 address. Connect to a private Wi-Fi/hotspot, then retry.'
}
Write-Host ''
Write-Host '请选择本次手机访问使用的物理网络。服务和防火墙只绑定到所选网卡与 IPv4 子网。'
for ($index = 0; $index -lt $choices.Count; $index++) {
    $item = $choices[$index]
    Write-Host ("[{0}] {1} · {2} · {3}/{4} · {5}" -f ($index + 1), $item.NetworkName, $item.InterfaceAlias, $item.IPAddress, $item.PrefixLength, $item.NetworkCategory)
}
if ($choices.Count -eq 1) {
    $choiceText = Read-Host '确认使用此网络? [Y/N]'
    if ($choiceText -notmatch '^(?i:y|yes)$') { Write-Host 'Cancelled; no service was changed.'; exit 0 }
    $selected = $choices[0]
} else {
    $selectedIndex = 0
    do {
        $choiceText = Read-Host "输入网络编号 1-$($choices.Count)（输入 Q 取消）"
        if ($choiceText -match '^(?i:q|quit|cancel)$') { Write-Host 'Cancelled; no service was changed.'; exit 0 }
        $validChoice = [int]::TryParse($choiceText, [ref]$selectedIndex) -and $selectedIndex -ge 1 -and $selectedIndex -le $choices.Count
        if (-not $validChoice) { Write-Host '请输入列表中的有效编号。' }
    } until ($validChoice)
    $selected = $choices[$selectedIndex - 1]
}
$selectedLocalAddress = $selected.IPAddress
$selectedSubnet = $selected.Subnet
$selectedInterfaceAlias = $selected.InterfaceAlias
$selectedInterfaceIndex = $selected.InterfaceIndex
Write-Host ("所选网络：{0} ({1})，本机 IPv4 {2}/{3}，允许客户端网段 {4}" -f $selected.NetworkName, $selectedInterfaceAlias, $selectedLocalAddress, $selected.PrefixLength, $selectedSubnet)

# Prepare and validate the persistent private CA and selected-IP certificate
# before the restart prompt can stop an existing local service.
$tlsPreparationOutput = & $pythonPath -m mrp.server.lan_tls prepare --ip $selectedLocalAddress 2>&1
if ($LASTEXITCODE -ne 0) {
    $tlsDiagnostic = ($tlsPreparationOutput | Out-String).Trim()
    if (-not $tlsDiagnostic) { $tlsDiagnostic = 'No diagnostic was returned.' }
    throw "Could not prepare the LAN HTTPS certificate. The existing service was left running. $tlsDiagnostic"
}
try {
    $tlsMaterial = ($tlsPreparationOutput | Out-String).Trim() | ConvertFrom-Json -ErrorAction Stop
    if (-not $tlsMaterial.store_path -or -not $tlsMaterial.certificate_path -or
        -not $tlsMaterial.private_key_path -or -not $tlsMaterial.root_certificate_path -or
        [string]$tlsMaterial.root_fingerprint_sha256 -notmatch '^[A-Fa-f0-9]{64}$' -or
        -not (Test-Path -LiteralPath ([string]$tlsMaterial.certificate_path) -PathType Leaf) -or
        -not (Test-Path -LiteralPath ([string]$tlsMaterial.private_key_path) -PathType Leaf) -or
        -not (Test-Path -LiteralPath ([string]$tlsMaterial.root_certificate_path) -PathType Leaf)) {
        throw 'The TLS preparation result is incomplete.'
    }
} catch {
    throw "Could not validate the LAN HTTPS preparation result. The existing service was left running. $($_.Exception.Message)"
}
Write-Host ("LAN HTTPS certificate prepared; root fingerprint SHA-256: {0}" -f [string]$tlsMaterial.root_fingerprint_sha256)

function Test-ExactLanFirewallRule {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$ProgramPath,
        [Parameter(Mandatory = $true)][int]$LocalPort,
        [Parameter(Mandatory = $true)][string]$LocalAddress,
        [Parameter(Mandatory = $true)][string]$InterfaceAlias,
        [Parameter(Mandatory = $true)][string]$RemoteSubnet
    )

    try {
        $expectedProgram = [System.IO.Path]::GetFullPath($ProgramPath)
        $rules = @(Get-NetFirewallRule -PolicyStore ActiveStore -DisplayName $Name -ErrorAction SilentlyContinue)
        # Multiple rules with the managed display name can combine their access.
        # Recreate them through the admin helper so only one precise rule remains.
        if ($rules.Count -ne 1) { return $false }
        foreach ($rule in $rules) {
            if ([string]$rule.Enabled -ne 'True' -or [string]$rule.Direction -ne 'Inbound' -or [string]$rule.Action -ne 'Allow') {
                continue
            }
            $profiles = ([string]$rule.Profile -split '[,\s]+') | Where-Object { $_ }
            if ($profiles -notcontains 'Any' -and $profiles -notcontains 'Public') { continue }

            $portFilters = @(Get-NetFirewallPortFilter -AssociatedNetFirewallRule $rule -ErrorAction Stop)
            $applicationFilters = @(Get-NetFirewallApplicationFilter -AssociatedNetFirewallRule $rule -ErrorAction Stop)
            $addressFilters = @(Get-NetFirewallAddressFilter -AssociatedNetFirewallRule $rule -ErrorAction Stop)
            $interfaceFilters = @(Get-NetFirewallInterfaceFilter -AssociatedNetFirewallRule $rule -ErrorAction Stop)
            if ($portFilters.Count -ne 1 -or $applicationFilters.Count -ne 1 -or $addressFilters.Count -ne 1 -or $interfaceFilters.Count -ne 1) { continue }

            $protocol = [string]$portFilters[0].Protocol
            $ports = @($portFilters[0].LocalPort | ForEach-Object { [string]$_ })
            $program = [string]$applicationFilters[0].Program
            $localAddresses = @($addressFilters[0].LocalAddress | ForEach-Object { [string]$_ })
            $remoteAddresses = @($addressFilters[0].RemoteAddress | ForEach-Object { [string]$_ })
            $interfaces = @($interfaceFilters[0].InterfaceAlias | ForEach-Object { [string]$_ })
            if ([string]$rule.EdgeTraversalPolicy -ne 'Block') { continue }
            if ($protocol -notin @('TCP', '6') -or $ports.Count -ne 1 -or $ports[0] -ne [string]$LocalPort) { continue }
            if ($localAddresses.Count -ne 1 -or $localAddresses[0] -ne $LocalAddress) { continue }
            if ($remoteAddresses.Count -ne 1 -or $remoteAddresses[0] -ne $RemoteSubnet) { continue }
            if ($interfaces.Count -ne 1 -or $interfaces[0] -ne $InterfaceAlias) { continue }
            if ([System.StringComparer]::OrdinalIgnoreCase.Equals([System.IO.Path]::GetFullPath($program), $expectedProgram)) {
                return $true
            }
        }
    }
    catch {
        return $false
    }
    return $false
}

function Get-BroaderLanFirewallRules {
    param(
        [Parameter(Mandatory = $true)][string]$ProgramPath,
        [Parameter(Mandatory = $true)][int]$LocalPort,
        [Parameter(Mandatory = $true)][string]$LocalAddress,
        [Parameter(Mandatory = $true)][string]$InterfaceAlias,
        [Parameter(Mandatory = $true)][string]$RemoteSubnet,
        [string]$IgnoredRuleName = ''
    )
    $conflicts = [System.Collections.Generic.List[string]]::new()
    $expectedProgram = [System.IO.Path]::GetFullPath($ProgramPath)
    try {
        $programFilters = @(Get-NetFirewallApplicationFilter -PolicyStore ActiveStore -ErrorAction Stop |
            Where-Object {
                $candidateProgram = [string]$_.Program
                if (-not $candidateProgram) { return $false }
                try {
                    [System.StringComparer]::OrdinalIgnoreCase.Equals(
                        [System.IO.Path]::GetFullPath($candidateProgram), $expectedProgram)
                } catch { $false }
            })
        $rules = @(foreach ($filter in $programFilters) {
            Get-NetFirewallRule -PolicyStore ActiveStore -AssociatedNetFirewallApplicationFilter $filter -ErrorAction Stop |
                Where-Object { [string]$_.Direction -eq 'Inbound' -and [string]$_.Action -eq 'Allow' -and [string]$_.Enabled -eq 'True' }
        })
    } catch {
        return @('Windows firewall policy could not be inspected')
    }
    foreach ($rule in $rules) {
        if ([string]$rule.Group -eq $ruleGroup -and [string]$rule.DisplayName -eq $ruleName) { continue }
        try {
            $applicationFilters = @(Get-NetFirewallApplicationFilter -AssociatedNetFirewallRule $rule -ErrorAction Stop)
            $portFilters = @(Get-NetFirewallPortFilter -AssociatedNetFirewallRule $rule -ErrorAction Stop)
            if ($applicationFilters.Count -ne 1 -or $portFilters.Count -ne 1) { continue }
            $program = [string]$applicationFilters[0].Program
            if (-not $program -or -not [System.StringComparer]::OrdinalIgnoreCase.Equals([System.IO.Path]::GetFullPath($program), $expectedProgram)) { continue }
            $protocol = [string]$portFilters[0].Protocol
            $ports = @($portFilters[0].LocalPort | ForEach-Object { [string]$_ })
            if ($protocol -notin @('TCP', '6', 'Any', '256') -or (($ports -notcontains 'Any') -and ($ports -notcontains [string]$LocalPort))) { continue }
            if ($IgnoredRuleName -and [System.StringComparer]::OrdinalIgnoreCase.Equals([string]$rule.Name, $IgnoredRuleName)) { continue }

            $addressFilters = @(Get-NetFirewallAddressFilter -AssociatedNetFirewallRule $rule -ErrorAction Stop)
            $interfaceFilters = @(Get-NetFirewallInterfaceFilter -AssociatedNetFirewallRule $rule -ErrorAction Stop)
            if ($addressFilters.Count -ne 1 -or $interfaceFilters.Count -ne 1) {
                $conflicts.Add([string]$rule.DisplayName); continue
            }
            $localAddresses = @($addressFilters[0].LocalAddress | ForEach-Object { [string]$_ })
            $remoteAddresses = @($addressFilters[0].RemoteAddress | ForEach-Object { [string]$_ })
            $interfaces = @($interfaceFilters[0].InterfaceAlias | ForEach-Object { [string]$_ })
            $isNarrow = $localAddresses.Count -eq 1 -and $localAddresses[0] -eq $LocalAddress -and
                $remoteAddresses.Count -eq 1 -and $remoteAddresses[0] -eq $RemoteSubnet -and
                $interfaces.Count -eq 1 -and $interfaces[0] -eq $InterfaceAlias -and
                [string]$rule.EdgeTraversalPolicy -eq 'Block'
            if (-not $isNarrow) { $conflicts.Add([string]$rule.DisplayName) }
        } catch {
            $conflicts.Add([string]$rule.DisplayName)
        }
    }
    return @($conflicts | Sort-Object -Unique)
}

function Invoke-ProjectFirewallAction {
    param([Parameter(Mandatory = $true)][ValidateSet('Enable', 'Disable')][string]$Action)
    $firewallScript = Join-Path $PSScriptRoot 'manage_lan_firewall.ps1'
    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [System.Security.Principal.WindowsPrincipal]::new($identity)
    if ($principal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)) {
        try {
            if ($Action -eq 'Enable') {
                & $firewallScript -Action Enable -Port $Port -LocalAddress $selectedLocalAddress `
                    -InterfaceAlias $selectedInterfaceAlias -RemoteSubnet $selectedSubnet `
                    -IgnoredRuleName $ignoredExistingRuleName
            } else {
                & $firewallScript -Action Disable -Port $Port
            }
        } catch {
            throw "Firewall $Action helper failed: $($_.Exception.Message)"
        }
        if (-not $?) { throw "Firewall $Action helper returned an unsuccessful status." }
        return
    }

    $windowsPowerShell = Get-Command powershell.exe -ErrorAction Stop
    $diagnosticRoot = Join-Path $env:LOCALAPPDATA 'Sekai o Tsumugu Hime\logs'
    New-Item -ItemType Directory -Path $diagnosticRoot -Force | Out-Null
    $diagnosticId = [guid]::NewGuid().ToString('N')
    $diagnosticPath = Join-Path $diagnosticRoot "firewall-$Action-$diagnosticId.log"
    [System.IO.File]::WriteAllText($diagnosticPath, 'Waiting for the elevated PowerShell wrapper to start.', [System.Text.UTF8Encoding]::new($false))
    $scriptLiteral = "'" + $firewallScript.Replace("'", "''") + "'"
    $diagnosticLiteral = "'" + $diagnosticPath.Replace("'", "''") + "'"
    $helperArguments = if ($Action -eq 'Enable') {
        "-Action Enable -Port $Port -LocalAddress '$($selectedLocalAddress.Replace("'", "''"))' -InterfaceAlias '$($selectedInterfaceAlias.Replace("'", "''"))' -RemoteSubnet '$($selectedSubnet.Replace("'", "''"))' -IgnoredRuleName '$($ignoredExistingRuleName.Replace("'", "''"))'"
    } else { "-Action Disable -Port $Port" }
    $wrapperCommand = @"
`$diagnosticFile = $diagnosticLiteral
try {
    [System.IO.File]::WriteAllText(`$diagnosticFile, 'Elevated firewall wrapper started.', [System.Text.UTF8Encoding]::new(`$false))
    & $scriptLiteral $helperArguments -DiagnosticFile `$diagnosticFile
    exit 0
} catch {
    `$failureText = [string]`$_.Exception.Message
    try { [System.IO.File]::WriteAllText(`$diagnosticFile, `$failureText, [System.Text.UTF8Encoding]::new(`$false)) } catch { }
    [Console]::Error.WriteLine(`$failureText)
    exit 1
}
"@
    $encodedCommand = [Convert]::ToBase64String([System.Text.Encoding]::Unicode.GetBytes($wrapperCommand))
    $arguments = @('-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', $encodedCommand)
    try {
        $elevated = Start-Process -FilePath $windowsPowerShell.Source -ArgumentList $arguments `
            -Verb RunAs -Wait -PassThru -WindowStyle Hidden
    } catch {
        throw "Firewall $Action helper could not be elevated: $($_.Exception.Message)"
    }
    $childOutput = if (Test-Path -LiteralPath $diagnosticPath -PathType Leaf) {
        (Get-Content -LiteralPath $diagnosticPath -Raw -ErrorAction SilentlyContinue).Trim()
    } else { '' }
    if ($elevated.ExitCode -ne 0) {
        $details = [string]$childOutput
        if (-not $details) { $details = 'The elevated helper did not write diagnostic output.' }
        $details += " Diagnostic file: $diagnosticPath"
        throw "Firewall $Action helper exited with code $($elevated.ExitCode): $details"
    }
    Remove-Item -LiteralPath $diagnosticPath -Force -ErrorAction SilentlyContinue
}

# Build and validate the bundle before asking to stop the existing service.
$webRoot = Join-Path $projectRoot 'src\web'
$webSource = Join-Path $webRoot 'src'
$distIndex = Join-Path $webRoot 'dist\index.html'
$distRoot = Join-Path $webRoot 'dist'
$needsBuild = -not (Test-Path -LiteralPath $distIndex -PathType Leaf)
if (-not $needsBuild -and (Test-Path -LiteralPath $webSource -PathType Container)) {
    $newestSource = Get-ChildItem -LiteralPath $webSource -File -Recurse | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
    $distInfo = Get-Item -LiteralPath $distIndex
    $needsBuild = $newestSource -and $newestSource.LastWriteTimeUtc -gt $distInfo.LastWriteTimeUtc
}
if ($needsBuild) {
    $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $npm) { throw 'The frontend needs a build, but npm.cmd is not available.' }
    $buildId = [guid]::NewGuid().ToString('N')
    $stagingDist = Join-Path $webRoot ".lan-dist-stage-$buildId"
    Write-Host 'Building the web interface...'
    Push-Location $webRoot
    try {
        & $npm.Source run build -- --outDir $stagingDist
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
    }
    finally { Pop-Location }
}

$listeners = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
if ($listeners.Count -gt 0) {
    $owners = @($listeners | Select-Object -ExpandProperty OwningProcess -Unique)
    $listenerRecords = @(Get-ProjectMrpPortListener -Port $Port -ProjectPythonPath $pythonPath)
    $unknownOwners = @($listenerRecords | Where-Object { -not $_.VerifiedProjectProcessTree })
    if ($listenerRecords.Count -ne $owners.Count -or $unknownOwners.Count -gt 0) {
        throw "Port $Port is occupied by a process that cannot be verified as this project's MRP process tree (PID $($owners -join ', ')); nothing was stopped. Choose an unused port with -Port."
    }
    $rootRecords = @($listenerRecords | Sort-Object RootProcessId -Unique)
    $rootPids = @($rootRecords | Select-Object -ExpandProperty RootProcessId -Unique)
    if ($rootPids.Count -gt 0) {
        Write-Host "Found this project's MRP process tree on port $Port (listener PID $($owners -join ', '); launcher PID $($rootPids -join ', '))."
        Write-Host 'The verified old process tree will be force-stopped. Finish or cancel active generations and save first; project data and settings are not deleted.'
        do {
            $answer = Read-Host 'Press Enter/Y to stop the old server and continue; press N to leave it running'
            $validAnswer = $answer -match '^(?i:y|yes|n|no)?$'
            if (-not $validAnswer) { Write-Host 'Enter Y, N, or press Enter to continue with the LAN server.' }
        } until ($validAnswer)
        if ($answer -match '^(?i:n|no)$') {
            Write-Host 'Cancelled; the existing server was left running.'
            exit 0
        }
        Write-Host 'Stopping the verified project service process tree...'
        foreach ($record in $rootRecords) {
            Stop-ProjectMrpProcessTree `
                -RootProcessId ([int]$record.RootProcessId) `
                -ProjectPythonPath $pythonPath `
                -ExpectedRootCreationDate $record.RootCreationDate
        }
        $previousServerWasStopped = $true
        $stopDeadline = (Get-Date).AddSeconds(15)
        do {
            Start-Sleep -Milliseconds 400
            $stillListening = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
        } while ($stillListening -and (Get-Date) -lt $stopDeadline)
        if ($stillListening) { throw "Port $Port is still occupied; the LAN server was not started." }
    }
}

# Publish a verified staged bundle only after any existing MRP server has been
# stopped through the restart prompt. Keep the previous bundle for recovery.
$previousDist = $null
if ($stagingDist -and (Test-Path -LiteralPath $stagingDist -PathType Container)) {
    $previousDist = Join-Path $webRoot ".lan-dist-previous-$buildId"
    if (Test-Path -LiteralPath $distRoot) {
        Move-Item -LiteralPath $distRoot -Destination $previousDist
    }
    try {
        Move-Item -LiteralPath $stagingDist -Destination $distRoot
    }
    catch {
        if (-not (Test-Path -LiteralPath $distRoot) -and (Test-Path -LiteralPath $previousDist)) {
            Move-Item -LiteralPath $previousDist -Destination $distRoot
        }
        throw
    }
}

$staleManagedRules = @(Get-NetFirewallRule -PolicyStore PersistentStore -ErrorAction Stop |
    Where-Object { [string]$_.Group -eq $ruleGroup -and [string]$_.DisplayName -eq $ruleName })
if ($staleManagedRules.Count -gt 0) {
    try {
        Invoke-ProjectFirewallAction -Action Disable
        $stillManaged = @(Get-NetFirewallRule -PolicyStore PersistentStore -ErrorAction Stop |
            Where-Object { [string]$_.Group -eq $ruleGroup -and [string]$_.DisplayName -eq $ruleName })
        if ($stillManaged.Count -gt 0) {
            $remainingNames = @($stillManaged | ForEach-Object { [string]$_.Name }) -join ', '
            throw "Owned firewall rule(s) remain after cleanup: $remainingNames."
        }
    } catch {
        Write-Warning "不能确认旧的本项目防火墙规则已清理；拒绝启动 LAN 服务。原因：$($_.Exception.Message)"
        Restore-LoopbackMrpServer
        exit 1
    }
}

$randomBytes = [byte[]]::new(8)
$randomGenerator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
try { $randomGenerator.GetBytes($randomBytes) }
finally { $randomGenerator.Dispose() }
$alphabet = 'ABCDEFGHJKMNPQRSTUVWXYZ023456789' # excludes I, L, O, and 1 for easier phone entry
$bitString = -join @($randomBytes | ForEach-Object { [Convert]::ToString([int]$_, 2).PadLeft(8, '0') })
$bitString = $bitString.Substring(0, 60) # 60 random bits encoded in 12 base-32 symbols
$codeCharacters = for ($offset = 0; $offset -lt $bitString.Length; $offset += 5) {
    $index = [Convert]::ToInt32($bitString.Substring($offset, 5), 2)
    [string]$alphabet[$index]
}
$codeText = -join $codeCharacters
$accessCode = [regex]::Replace($codeText, '(.{4})(?=.)', '$1-')

# Only this explicit launcher enables LAN mode. Uvicorn binds the selected private
# address, and the server checks the selected destination and client subnet.
$firewallStatusRoot = Join-Path $env:LOCALAPPDATA 'Sekai o Tsumugu Hime\lan-status'
New-Item -ItemType Directory -Path $firewallStatusRoot -Force | Out-Null
$firewallStatusFile = Join-Path $firewallStatusRoot "lan-firewall-$Port.status"
Remove-Item -LiteralPath $firewallStatusFile -Force -ErrorAction SilentlyContinue
Remove-Item Env:MRP_LAN_FIREWALL_STATUS_FILE -ErrorAction SilentlyContinue
$env:MRP_LAN_BIND_IP = $selectedLocalAddress
$env:MRP_LAN_SUBNET = $selectedSubnet
$env:MRP_LAN_INTERFACE_INDEX = [string]$selectedInterfaceIndex
$env:MRP_LAN_INTERFACE_ALIAS = $selectedInterfaceAlias
$env:MRP_LAN_FIREWALL_STATUS_FILE = $firewallStatusFile
$env:MRP_LAN_TLS_STORE_DIR = [string]$tlsMaterial.store_path
$env:MRP_LAN_TLS_CERT_FILE = [string]$tlsMaterial.certificate_path
$env:MRP_LAN_TLS_KEY_FILE = [string]$tlsMaterial.private_key_path
$env:MRP_LAN_TLS_ROOT_FILE = [string]$tlsMaterial.root_certificate_path
$env:MRP_HOST = $selectedLocalAddress
$env:MRP_PORT = [string]$Port
$env:MRP_LAN_MODE = '1'
$env:MRP_LAN_ACCESS_CODE = $accessCode

$logRoot = Join-Path $env:LOCALAPPDATA 'Sekai o Tsumugu Hime\logs'
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$stdoutLog = Join-Path $logRoot "lan-server-$stamp.out.log"
$stderrLog = Join-Path $logRoot "lan-server-$stamp.err.log"
$server = Start-Process -FilePath $pythonPath `
    -ArgumentList @('-m', 'mrp.server.main') `
    -WorkingDirectory $projectRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog `
    -PassThru
Remove-Item Env:MRP_LAN_ACCESS_CODE -ErrorAction SilentlyContinue

$statusUrl = "http://127.0.0.1:$Port/api/v1/lan/status"
$status = $null
for ($attempt = 0; $attempt -lt 40; $attempt++) {
    if ($server.HasExited) { break }
    try {
        $status = Invoke-RestMethod -Uri $statusUrl -TimeoutSec 2
        if ($status.enabled -eq $true) { break }
    }
    catch { }
    Start-Sleep -Milliseconds 750
}

if (-not $status -or $status.enabled -ne $true) {
    Write-Host "LAN server did not become ready. Logs: $stderrLog"
    if (Test-Path -LiteralPath $stderrLog) { Get-Content -LiteralPath $stderrLog -Tail 35 }
    $startedRoot = Get-CimInstance Win32_Process -Filter "ProcessId = $($server.Id)" -ErrorAction SilentlyContinue
    if (Test-ProjectMrpModuleProcess -Process $startedRoot -ProjectPythonPath $pythonPath) {
        Stop-ProjectMrpProcessTree -RootProcessId $server.Id -ProjectPythonPath $pythonPath -ExpectedRootCreationDate $startedRoot.CreationDate
    }
    if ($previousDist -and (Test-Path -LiteralPath $previousDist -PathType Container)) {
        $failedDist = Join-Path $webRoot ".lan-dist-failed-$buildId"
        if (Test-Path -LiteralPath $distRoot) { Move-Item -LiteralPath $distRoot -Destination $failedDist }
        Move-Item -LiteralPath $previousDist -Destination $distRoot
        Write-Host "Restored the previous web bundle. Failed LAN bundle saved to $failedDist"
    }
    Restore-LoopbackMrpServer
    exit 1
}

# Verify the actual selected-address endpoint with the prepared local CA. This
# performs normal certificate-chain and IP SAN validation; it has no insecure
# fallback and runs before firewall access is enabled or the URL is shared.
$tlsVerificationOutput = & $pythonPath -m mrp.server.lan_tls verify `
    --ip $selectedLocalAddress --port $Port --ca-file ([string]$tlsMaterial.root_certificate_path) 2>&1
if ($LASTEXITCODE -ne 0) {
    $tlsDiagnostic = ($tlsVerificationOutput | Out-String).Trim()
    Write-Warning ("经本地 CA 验证的 LAN HTTPS 检查失败；拒绝启用远程入口。{0}" -f $tlsDiagnostic)
    $startedRoot = Get-CimInstance Win32_Process -Filter "ProcessId = $($server.Id)" -ErrorAction SilentlyContinue
    if (Test-ProjectMrpModuleProcess -Process $startedRoot -ProjectPythonPath $pythonPath) {
        Stop-ProjectMrpProcessTree -RootProcessId $server.Id -ProjectPythonPath $pythonPath -ExpectedRootCreationDate $startedRoot.CreationDate
    }
    Restore-LoopbackMrpServer
    exit 1
}
try {
    $verifiedTlsStatus = ($tlsVerificationOutput | Out-String).Trim() | ConvertFrom-Json -ErrorAction Stop
} catch {
    Write-Warning '经本地 CA 验证的 LAN HTTPS 状态无效；拒绝启用远程入口。'
    $startedRoot = Get-CimInstance Win32_Process -Filter "ProcessId = $($server.Id)" -ErrorAction SilentlyContinue
    if (Test-ProjectMrpModuleProcess -Process $startedRoot -ProjectPythonPath $pythonPath) {
        Stop-ProjectMrpProcessTree -RootProcessId $server.Id -ProjectPythonPath $pythonPath -ExpectedRootCreationDate $startedRoot.CreationDate
    }
    Restore-LoopbackMrpServer
    exit 1
}
if ($verifiedTlsStatus.transport -ne 'https' -or $verifiedTlsStatus.https_available -ne $true -or
    [string]$verifiedTlsStatus.root_ca_fingerprint_sha256 -ne [string]$tlsMaterial.root_fingerprint_sha256 -or
    [string]$verifiedTlsStatus.bind_address -ne $selectedLocalAddress) {
    Write-Warning 'LAN HTTPS 核验状态与所选地址或根证书指纹不符；拒绝启用远程入口。'
    $startedRoot = Get-CimInstance Win32_Process -Filter "ProcessId = $($server.Id)" -ErrorAction SilentlyContinue
    if (Test-ProjectMrpModuleProcess -Process $startedRoot -ProjectPythonPath $pythonPath) {
        Stop-ProjectMrpProcessTree -RootProcessId $server.Id -ProjectPythonPath $pythonPath -ExpectedRootCreationDate $startedRoot.CreationDate
    }
    Restore-LoopbackMrpServer
    exit 1
}

if ([string]$status.bind_address -ne $selectedLocalAddress -or $status.interfaces.Count -ne 1 -or
    [string]$status.interfaces[0].name -ne $selectedInterfaceAlias -or
    [string]$status.interfaces[0].subnet -ne $selectedSubnet) {
    Write-Warning 'LAN 服务报告的监听地址或网卡与所选网络不一致；启动已回滚。'
    $startedRoot = Get-CimInstance Win32_Process -Filter "ProcessId = $($server.Id)" -ErrorAction SilentlyContinue
    if (Test-ProjectMrpModuleProcess -Process $startedRoot -ProjectPythonPath $pythonPath) {
        Stop-ProjectMrpProcessTree -RootProcessId $server.Id -ProjectPythonPath $pythonPath -ExpectedRootCreationDate $startedRoot.CreationDate
    }
    Restore-LoopbackMrpServer
    exit 1
}

$activeListeners = @(Get-ProjectMrpPortListener -Port $Port -ProjectPythonPath $pythonPath -ExpectedRootProcessId $server.Id)
$activeOwnerIds = @($activeListeners | Select-Object -ExpandProperty ListenerProcessId -Unique)
$verifiedListeners = @($activeListeners | Where-Object { $_.VerifiedProjectProcessTree })
$verifiedOwnerIds = @($verifiedListeners | Select-Object -ExpandProperty ListenerProcessId -Unique)
$allPortBindings = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
$activeBindings = @($allPortBindings | Where-Object { $verifiedOwnerIds -contains [int]$_.OwningProcess })
$activeBindingAddresses = @($activeBindings | ForEach-Object { [string]$_.LocalAddress } | Sort-Object -Unique)
$expectedBindingAddresses = @($selectedLocalAddress, '127.0.0.1')
$unexpectedBindings = @($allPortBindings | Where-Object {
    ($verifiedOwnerIds -notcontains [int]$_.OwningProcess) -or
    [string]$_.LocalAddress -notin $expectedBindingAddresses
})
$missingBindings = @($expectedBindingAddresses | Where-Object { $activeBindingAddresses -notcontains $_ })
$programPaths = @($verifiedListeners | Select-Object -ExpandProperty ListenerExecutablePath -Unique)
if ($activeListeners.Count -ne 1 -or $activeOwnerIds.Count -ne 1 -or $verifiedListeners.Count -ne 1 -or
    $verifiedOwnerIds.Count -ne 1 -or $programPaths.Count -ne 1 -or $activeBindings.Count -lt 1 -or
    $missingBindings.Count -gt 0 -or $unexpectedBindings.Count -gt 0) {
    Write-Warning 'LAN startup reached the status endpoint, but its selected and loopback listeners could not be tied exclusively to this launcher; firewall setup was skipped.'
    $startedRoot = Get-CimInstance Win32_Process -Filter "ProcessId = $($server.Id)" -ErrorAction SilentlyContinue
    if (Test-ProjectMrpModuleProcess -Process $startedRoot -ProjectPythonPath $pythonPath) {
        Stop-ProjectMrpProcessTree -RootProcessId $server.Id -ProjectPythonPath $pythonPath -ExpectedRootCreationDate $startedRoot.CreationDate
    }
    Restore-LoopbackMrpServer
    exit 1
}
$listenerProgramPath = $programPaths[0]
$firewallReady = Test-ExactLanFirewallRule -Name $ruleName -ProgramPath $listenerProgramPath -LocalPort $Port `
    -LocalAddress $selectedLocalAddress -InterfaceAlias $selectedInterfaceAlias -RemoteSubnet $selectedSubnet
$wideRules = @(Get-BroaderLanFirewallRules -ProgramPath $listenerProgramPath -LocalPort $Port `
    -LocalAddress $selectedLocalAddress -InterfaceAlias $selectedInterfaceAlias -RemoteSubnet $selectedSubnet `
    -IgnoredRuleName $ignoredExistingRuleName)
if ($wideRules.Count -gt 0) {
    Write-Warning ("发现可能扩大本机暴露面的防火墙规则：{0}" -f ($wideRules -join ', '))
    $firewallReady = $false
}
if (-not $firewallReady) {
    Write-Host ''
    Write-Host '未发现适用于所选网络、实际 Python、TCP 端口和确切客户端子网的防火墙规则。'
    Write-Host 'Windows 将请求管理员授权添加这条受限规则；拒绝授权会回滚 LAN 服务。'
    try {
        Invoke-ProjectFirewallAction -Action Enable
        # The elevated helper verifies the newly created rule in ActiveStore.
        # A second filter-by-filter readback here can disagree with Windows'
        # normalized address/interface representation and roll back a valid rule.
        $firewallReady = $true
    }
    catch {
        Write-Warning "未能建立防火墙规则。原因：$($_.Exception.Message)"
        $firewallReady = $false
    }
}

if (-not $firewallReady) {
    Write-Host '防火墙规则未能精确建立或核验；正在停止 LAN 服务并清理本项目规则。'
    $startedRoot = Get-CimInstance Win32_Process -Filter "ProcessId = $($server.Id)" -ErrorAction SilentlyContinue
    if (Test-ProjectMrpModuleProcess -Process $startedRoot -ProjectPythonPath $pythonPath) {
        Stop-ProjectMrpProcessTree -RootProcessId $server.Id -ProjectPythonPath $pythonPath -ExpectedRootCreationDate $startedRoot.CreationDate
    }
    $rulesToRemove = @(Get-NetFirewallRule -PolicyStore PersistentStore -ErrorAction SilentlyContinue |
        Where-Object { [string]$_.Group -eq $ruleGroup -and [string]$_.DisplayName -eq $ruleName })
    if ($rulesToRemove.Count -gt 0) {
        try { Invoke-ProjectFirewallAction -Action Disable }
        catch { Write-Warning '无法确认本项目防火墙规则已撤销；监听已停止。请在再次启用 LAN 前以管理员身份检查并清理该规则。' }
    }
    Remove-Item -LiteralPath $firewallStatusFile -Force -ErrorAction SilentlyContinue
    if ($previousDist -and (Test-Path -LiteralPath $previousDist -PathType Container)) {
        if (Test-Path -LiteralPath $distRoot) {
            $failedDist = Join-Path $webRoot ".lan-dist-failed-$buildId"
            Move-Item -LiteralPath $distRoot -Destination $failedDist
        }
        Move-Item -LiteralPath $previousDist -Destination $distRoot
    }
    Restore-LoopbackMrpServer
    exit 1
}
Set-Content -LiteralPath $firewallStatusFile -Value 'verified' -NoNewline -Encoding Ascii

Write-Host ''
Write-Host '局域网服务已启动。仅在可信家庭 Wi-Fi 或个人热点使用。'
Write-Host "端口：$Port / TCP"
Write-Host '手机访问 URL：'
Write-Host "  https://$($selectedLocalAddress):$Port/"
Write-Host ("本机根 CA SHA-256 指纹：{0}（首次使用手机访问前需手动安装此根证书）" -f [string]$tlsMaterial.root_fingerprint_sha256)
Write-Host ("服务器证书有效期至：{0}" -f [string]$tlsMaterial.certificate_expires_at)
Write-Host "访问码（12 位，排除易混字符，仅显示一次）：$accessCode"
Write-Host '二维码：本机浏览器打开“设置 → 手机访问”即可扫码；二维码只含 URL，不含访问码。'
Write-Host "启动状态：仅监听 $($selectedLocalAddress):$Port；客户端限定 $selectedSubnet，其他网卡不接收连接。"
Write-Host "启动器 PID：$($server.Id)；TCP 监听 PID：$($activeOwnerIds[0])"
Write-Host "停止整个服务进程树：.\stop_server.bat -Port $Port"
Write-Host "日志：$logRoot"
if ($previousDist -and (Test-Path -LiteralPath $previousDist -PathType Container)) {
    Write-Host "切换前的前端产物备份：$previousDist"
}

$profiles = @(Get-NetConnectionProfile -ErrorAction SilentlyContinue)
if ($profiles.Count -gt 0) {
    Write-Host ("当前网络：" + (($profiles | ForEach-Object { "$($_.Name) ($($_.NetworkCategory))" }) -join ', '))
}
Write-Host 'Windows 防火墙规则已建立；手机端仍需验证同网连接。路由器映射和公网可达性未验证。'

# Keep the desktop control page on the loopback listener; the QR still points
# at the selected IPv4 listener for the phone.
Start-Process "http://127.0.0.1:$Port/settings/phone"
