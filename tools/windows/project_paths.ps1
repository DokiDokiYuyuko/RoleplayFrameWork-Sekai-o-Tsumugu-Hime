# Shared path resolution; launchers may run from any working directory.
function Get-MrpProjectRoot {
    $root = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
    if (-not (Test-Path -LiteralPath (Join-Path $root 'pyproject.toml') -PathType Leaf) -or
        -not (Test-Path -LiteralPath (Join-Path $root 'src\mrp') -PathType Container)) {
        throw 'Cannot locate this MRP project. Keep tools/windows inside the project.'
    }
    return $root
}

function Get-MrpDataRoot {
    $projectRoot = Get-MrpProjectRoot
    $configured = [Environment]::GetEnvironmentVariable('MRP_DATA_ROOT', 'Process')
    if ($configured) {
        $dataRoot = [System.IO.Path]::GetFullPath($configured)
    } else {
        $motherRoot = Split-Path -Parent $projectRoot
        $dataRoot = [System.IO.Path]::GetFullPath((Join-Path $motherRoot 'data'))
    }

    $codePrefix = $projectRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
    if ($dataRoot.Equals($projectRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
        $dataRoot.StartsWith($codePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'Private data must be stored outside the code repository. Choose the sibling data directory or set MRP_DATA_ROOT to another external location.'
    }
    New-Item -ItemType Directory -Path $dataRoot -Force | Out-Null
    return $dataRoot
}
