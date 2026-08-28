param(
    [Parameter(Mandatory = $true)]
    [string]$Version
)

$ErrorActionPreference = "Stop"
$Repository = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$Stage = Join-Path $Repository "out/package-$Version"
$Archive = Join-Path $Repository "out/continuous-authentication-$Version.zip"
$Checksum = "$Archive.sha256"

if (Test-Path $Stage) { throw "Package staging directory already exists: $Stage" }
if (Test-Path $Archive) { throw "Package archive already exists: $Archive" }
if (Test-Path $Checksum) { throw "Package checksum already exists: $Checksum" }

New-Item -ItemType Directory -Path $Stage | Out-Null
New-Item -ItemType Directory -Path (Join-Path $Stage "wheels") | Out-Null
New-Item -ItemType Directory -Path (Join-Path $Stage "config") | Out-Null
New-Item -ItemType Directory -Path (Join-Path $Stage "dashboard") | Out-Null

& py -m pip wheel "${Repository}[backend]" --wheel-dir (Join-Path $Stage "wheels")
if ($LASTEXITCODE -ne 0) { throw "Python wheel build failed" }

& cmake -S (Join-Path $Repository "collector") -B (Join-Path $Repository "build/collector-package") -DBUILD_TESTING=OFF
if ($LASTEXITCODE -ne 0) { throw "Collector configuration failed" }
& cmake --build (Join-Path $Repository "build/collector-package") --config Release
if ($LASTEXITCODE -ne 0) { throw "Collector build failed" }

Push-Location (Join-Path $Repository "dashboard")
try {
    & npm ci
    if ($LASTEXITCODE -ne 0) { throw "Dashboard dependency installation failed" }
    & npm run build
    if ($LASTEXITCODE -ne 0) { throw "Dashboard build failed" }
} finally {
    Pop-Location
}

Copy-Item (Join-Path $Repository "build/collector-package/Release/continuous_auth_collector.exe") $Stage
Copy-Item (Join-Path $Repository "config/*.development.yaml") (Join-Path $Stage "config")
Copy-Item (Join-Path $Repository "config/app_categories.yaml") (Join-Path $Stage "config")
Copy-Item (Join-Path $Repository "dashboard/dist/*") (Join-Path $Stage "dashboard") -Recurse
Copy-Item (Join-Path $PSScriptRoot "install.ps1") $Stage
Copy-Item (Join-Path $PSScriptRoot "run-development.ps1") $Stage
Copy-Item (Join-Path $Repository "docs/deployment/windows.md") $Stage

Compress-Archive -Path (Join-Path $Stage "*") -DestinationPath $Archive
$ArchiveHash = (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath $Checksum -Value "$ArchiveHash  $(Split-Path -Leaf $Archive)" -Encoding ascii
Write-Output $Archive
Write-Output $Checksum
