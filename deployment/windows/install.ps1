param(
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA "ContinuousAuthentication/Application")
)

$ErrorActionPreference = "Stop"
if (-not $env:LOCALAPPDATA) { throw "LOCALAPPDATA is required" }
if (Test-Path $InstallRoot) { throw "Install destination already exists: $InstallRoot" }

New-Item -ItemType Directory -Path $InstallRoot | Out-Null
try {
    Copy-Item (Join-Path $PSScriptRoot "*") $InstallRoot -Recurse
    & py -m venv (Join-Path $InstallRoot ".venv")
    if ($LASTEXITCODE -ne 0) { throw "Virtual environment creation failed" }
    $Python = Join-Path $InstallRoot ".venv/Scripts/python.exe"
    & $Python -m pip install --no-index --find-links (Join-Path $InstallRoot "wheels") continuous-authentication-workspace
    if ($LASTEXITCODE -ne 0) { throw "Offline Python package installation failed" }

    New-Item -ItemType Directory -Path (Join-Path $InstallRoot "models") | Out-Null
} catch {
    Remove-Item -LiteralPath $InstallRoot -Recurse -Force
    throw
}
Write-Output "Installed at $InstallRoot"
Write-Output "Set CA_DASHBOARD_SECRET, then run run-development.ps1 -SyntheticUser <pseudonym>."
