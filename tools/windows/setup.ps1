$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'project_paths.ps1')
$projectRoot = Get-MrpProjectRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$pythonMarker = Join-Path $projectRoot '.venv\.sekai-dependencies-complete'
$pythonFingerprint = (@('uv.lock', 'requirements-lock.txt', 'pyproject.toml') | ForEach-Object { (Get-FileHash -LiteralPath (Join-Path $projectRoot $_) -Algorithm SHA256).Hash }) -join ':'
Push-Location $projectRoot
try {
    $pythonReady = (Test-Path -LiteralPath $pythonPath -PathType Leaf) -and (Test-Path -LiteralPath $pythonMarker -PathType Leaf) -and ((Get-Content -LiteralPath $pythonMarker -Raw).Trim() -eq $pythonFingerprint)
    if (-not $pythonReady) {
        Remove-Item -LiteralPath $pythonMarker -Force -ErrorAction SilentlyContinue
        $uv = Get-Command uv.exe -ErrorAction SilentlyContinue
        if ($uv) {
            & $uv.Source sync --frozen --no-dev
            if ($LASTEXITCODE -ne 0) { throw 'Locked Python dependency installation failed.' }
        } else {
            $python = Get-Command python.exe -ErrorAction SilentlyContinue
            $py = Get-Command py.exe -ErrorAction SilentlyContinue
            if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
                if ($py) {
                    & $py.Source -3 -c 'import sys; assert sys.version_info >= (3,11)'
                    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ is required.' }
                    & $py.Source -3 -m venv .venv
                } elseif ($python) {
                    & $python.Source -c 'import sys; assert sys.version_info >= (3,11)'
                    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ is required.' }
                    & $python.Source -m venv .venv
                } else { throw 'Install Python 3.11+ or uv, and Node.js 20+ before starting.' }
                if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $pythonPath)) { throw 'Could not create the Python environment.' }
            }
            & $pythonPath -m ensurepip --upgrade
            if ($LASTEXITCODE -ne 0) { throw 'Could not install pip in the Python environment.' }
            & $pythonPath -m pip install --require-hashes -r requirements-lock.txt
            if ($LASTEXITCODE -ne 0) { throw 'Locked Python dependency installation failed.' }
            & $pythonPath -m pip install --no-deps -e .
            if ($LASTEXITCODE -ne 0) { throw 'Application installation failed.' }
        }
        Set-Content -LiteralPath $pythonMarker -Value $pythonFingerprint -Encoding ASCII
    }
    $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $npm) { throw 'Install Node.js 20+ (including npm) before starting.' }
    & node.exe -e 'if (parseInt(process.versions.node) < 20) { process.exit(1); }'
    if ($LASTEXITCODE -ne 0) { throw 'Node.js 20+ is required.' }
    Push-Location (Join-Path $projectRoot 'src\web')
    try {
        $npmMarker = Join-Path (Get-Location).Path 'node_modules\.sekai-dependencies-complete'
        $npmFingerprint = (Get-FileHash -LiteralPath 'package-lock.json' -Algorithm SHA256).Hash
        $npmReady = (Test-Path -LiteralPath $npmMarker -PathType Leaf) -and ((Get-Content -LiteralPath $npmMarker -Raw).Trim() -eq $npmFingerprint)
        if (-not $npmReady) {
            Remove-Item -LiteralPath $npmMarker -Force -ErrorAction SilentlyContinue
            & $npm.Source ci
            if ($LASTEXITCODE -ne 0) { throw 'Locked frontend dependency installation failed.' }
            Set-Content -LiteralPath $npmMarker -Value $npmFingerprint -Encoding ASCII
        }
        if (-not (Test-Path -LiteralPath 'dist\index.html' -PathType Leaf)) {
            & $npm.Source run build
            if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
        }
    } finally { Pop-Location }
} finally { Pop-Location }
