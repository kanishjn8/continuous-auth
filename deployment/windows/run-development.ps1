param(
    [Parameter(Mandatory = $true)]
    [string]$SyntheticUser,
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA "ContinuousAuthentication/Application")
)

$ErrorActionPreference = "Stop"
if (-not $env:CA_DASHBOARD_SECRET) { throw "CA_DASHBOARD_SECRET must be set" }
$Python = Join-Path $InstallRoot ".venv/Scripts/python.exe"
$Config = Join-Path $InstallRoot "config"

$BackendArguments = @(
    "-m", "backend.app.runtime.cli",
    "--synthetic-user", $SyntheticUser,
    "--artifact-root", (Join-Path $InstallRoot "models"),
    "--dashboard-directory", (Join-Path $InstallRoot "dashboard"),
    "--storage-config", (Join-Path $Config "storage.development.yaml"),
    "--collector-config", (Join-Path $Config "collector.development.yaml"),
    "--ingestion-config", (Join-Path $Config "ingestion.development.yaml"),
    "--ml-config", (Join-Path $Config "ml.development.yaml"),
    "--risk-config", (Join-Path $Config "risk.development.yaml"),
    "--context-config", (Join-Path $Config "context.development.yaml"),
    "--api-config", (Join-Path $Config "api.development.yaml"),
    "--updates-config", (Join-Path $Config "updates.development.yaml"),
    "--orchestration-config", (Join-Path $Config "orchestration.development.yaml")
)
$Backend = Start-Process -FilePath $Python -ArgumentList $BackendArguments -PassThru
$Collector = Start-Process -FilePath (Join-Path $InstallRoot "continuous_auth_collector.exe") -ArgumentList @(
    "--config", (Join-Path $Config "collector.development.yaml"),
    "--categories", (Join-Path $Config "app_categories.yaml")
) -PassThru

try {
    Wait-Process -Id $Backend.Id
} finally {
    if (-not $Collector.HasExited) { Stop-Process -Id $Collector.Id }
    if (-not $Backend.HasExited) { Stop-Process -Id $Backend.Id }
}
