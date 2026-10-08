# Shared Windows process checks for the explicit MRP launchers.

function Test-MrpModuleCommandLine {
    param([Parameter(Mandatory = $true)][AllowNull()]$Process)

    if (-not $Process -or -not $Process.CommandLine) { return $false }
    return [string]$Process.CommandLine -match '(?i)^\s*(?:"[^"]+"|\S+)\s+-m\s+mrp\.server\.main(?:\s|$)'
}

function Test-ProjectMrpModuleProcess {
    param(
        [Parameter(Mandatory = $true)][AllowNull()]$Process,
        [Parameter(Mandatory = $true)][string]$ProjectPythonPath
    )

    if (-not $Process -or -not $Process.ExecutablePath -or -not (Test-MrpModuleCommandLine -Process $Process)) { return $false }
    try {
        $expectedPath = [System.IO.Path]::GetFullPath($ProjectPythonPath)
        $actualPath = [System.IO.Path]::GetFullPath([string]$Process.ExecutablePath)
        return [System.StringComparer]::OrdinalIgnoreCase.Equals($actualPath, $expectedPath)
    }
    catch { return $false }
}

function Get-ProjectMrpPortListener {
    param(
        [Parameter(Mandatory = $true)][int]$Port,
        [Parameter(Mandatory = $true)][string]$ProjectPythonPath,
        [int]$ExpectedRootProcessId = 0
    )

    $owners = @(
        Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique
    )
    if ($owners.Count -eq 0) { return }

    $processes = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
    $byId = @{}
    foreach ($process in $processes) { $byId[[int]$process.ProcessId] = $process }
    $expectedPython = [System.IO.Path]::GetFullPath($ProjectPythonPath)
    foreach ($owner in $owners) {
        $listenerId = [int]$owner
        $listener = $byId[$listenerId]
        $root = $null
        $verified = $false
        $programPath = $null

        if ($listener -and $listener.ExecutablePath) {
            try { $programPath = [System.IO.Path]::GetFullPath([string]$listener.ExecutablePath) }
            catch { $programPath = $null }
        }

        if ($listener -and (Test-MrpModuleCommandLine -Process $listener) -and $programPath) {
            $seen = @{}
            $cursor = $listener
            for ($hop = 0; $cursor -and $hop -lt 32; $hop++) {
                $cursorId = [int]$cursor.ProcessId
                if ($seen.ContainsKey($cursorId)) { break }
                $seen[$cursorId] = $true

                $cursorPath = $null
                if ($cursor.ExecutablePath) {
                    try { $cursorPath = [System.IO.Path]::GetFullPath([string]$cursor.ExecutablePath) }
                    catch { $cursorPath = $null }
                }
                if ($cursorPath -and [System.StringComparer]::OrdinalIgnoreCase.Equals($cursorPath, $expectedPython)) {
                    if ((Test-MrpModuleCommandLine -Process $cursor) -and
                        ($ExpectedRootProcessId -eq 0 -or $cursorId -eq $ExpectedRootProcessId)) {
                        $root = $cursor
                        $verified = $true
                    }
                    break
                }

                $parentId = [int]$cursor.ParentProcessId
                if ($parentId -le 0 -or -not $byId.ContainsKey($parentId)) { break }
                $parent = $byId[$parentId]
                try {
                    if ([datetime]$parent.CreationDate -gt [datetime]$cursor.CreationDate) { break }
                }
                catch { break }
                $cursor = $parent
            }
        }

        [pscustomobject]@{
            ListenerProcessId = $listenerId
            ListenerExecutablePath = $programPath
            RootProcessId = if ($root) { [int]$root.ProcessId } else { 0 }
            RootCreationDate = if ($root) { $root.CreationDate } else { $null }
            VerifiedProjectProcessTree = $verified
        }
    }
}

function Stop-ProjectMrpProcessTree {
    param(
        [Parameter(Mandatory = $true)][int]$RootProcessId,
        [Parameter(Mandatory = $true)][string]$ProjectPythonPath,
        $ExpectedRootCreationDate
    )

    $processes = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
    $byId = @{}
    foreach ($process in $processes) { $byId[[int]$process.ProcessId] = $process }
    $root = $byId[$RootProcessId]
    if (-not (Test-ProjectMrpModuleProcess -Process $root -ProjectPythonPath $ProjectPythonPath)) {
        throw "Refusing to stop PID $RootProcessId because it is no longer the project's MRP launcher."
    }
    if ($ExpectedRootCreationDate) {
        $identityMatches = $false
        try {
            $identityMatches = [datetime]$root.CreationDate -eq [datetime]$ExpectedRootCreationDate
        } catch { throw "Refusing to stop PID $RootProcessId because its process identity could not be verified." }
        if (-not $identityMatches) { throw "Refusing to stop PID $RootProcessId because its process identity changed." }
    }

    $depthById = @{$RootProcessId = 0}
    $changed = $true
    while ($changed) {
        $changed = $false
        foreach ($process in $processes) {
            $processId = [int]$process.ProcessId
            $parentId = [int]$process.ParentProcessId
            if ($depthById.ContainsKey($processId) -or -not $depthById.ContainsKey($parentId)) { continue }
            try {
                if ([datetime]$process.CreationDate -lt [datetime]$byId[$parentId].CreationDate) { continue }
            }
            catch { continue }
            $depthById[$processId] = $depthById[$parentId] + 1
            $changed = $true
        }
    }

    $targets = foreach ($processId in @($depthById.Keys | Where-Object { [int]$_ -ne $RootProcessId })) {
        [pscustomobject]@{ Process = $byId[[int]$processId]; Depth = [int]$depthById[$processId] }
    }
    foreach ($target in @($targets | Sort-Object Depth -Descending)) {
        $expected = $target.Process
        $current = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$expected.ProcessId)" -ErrorAction SilentlyContinue
        if (-not $current) { continue }
        try {
            if ([datetime]$current.CreationDate -ne [datetime]$expected.CreationDate) { continue }
        }
        catch { continue }
        Stop-Process -Id ([int]$current.ProcessId) -Force -ErrorAction SilentlyContinue
    }

    $currentRoot = Get-CimInstance Win32_Process -Filter "ProcessId = $RootProcessId" -ErrorAction SilentlyContinue
    if ($currentRoot) {
        if (-not (Test-ProjectMrpModuleProcess -Process $currentRoot -ProjectPythonPath $ProjectPythonPath)) {
            throw "Refusing to stop PID $RootProcessId because it changed identity during shutdown."
        }
        if ($ExpectedRootCreationDate) {
            $identityMatches = $false
            try {
                $identityMatches = [datetime]$currentRoot.CreationDate -eq [datetime]$ExpectedRootCreationDate
            } catch { throw "Refusing to stop PID $RootProcessId because its process identity could not be verified." }
            if (-not $identityMatches) { throw "Refusing to stop PID $RootProcessId because its process identity changed." }
        }
        Stop-Process -Id $RootProcessId -Force -ErrorAction SilentlyContinue
    }
}
