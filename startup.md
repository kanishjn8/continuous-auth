# Application startup

This repository has two supported startup modes:

1. **Docker Compose control plane** starts the React dashboard and the authenticated
   Python backend. The ML feature/model packages are installed and validated in the
   backend container because the ML engine is an in-process backend component, not a
   separate network service.
2. **Full desktop application** starts the dashboard, backend, in-process ML engine,
   and the native Windows or macOS event collector as one working event pipeline.

The native collector must run on the monitored desktop. It selects Windows named pipes
and low-level hooks on Windows, or a private Unix socket and listen-only event tap on
macOS. Compose does not start the collector and must not be treated as a
participant-collection deployment.

## 1. Prerequisites

For the Docker control plane:

- Docker Desktop with Docker Compose v2.
- Free loopback port `8080`, or set `CA_DASHBOARD_PORT` to another port.

For the complete application on Windows:

- Windows 10 or 11.
- Python 3.11 or newer.
- Node.js 22 and npm.
- CMake 3.20 or newer.
- Visual Studio Build Tools with the C++ desktop workload.
- Permission to install global keyboard and mouse hooks on the machine.

For the complete application on macOS:

- macOS with Xcode command-line tools.
- Python 3.11 or newer, Node.js 22, npm, and CMake 3.20 or newer.
- Input Monitoring permission for the terminal or stable collector executable.

Run all commands from the repository root.

## 2. Start the Docker Compose control plane

### Step 1: provide a temporary dashboard secret

PowerShell:

```powershell
$env:CA_DASHBOARD_SECRET = Read-Host "Temporary local dashboard secret"
```

Bash or zsh:

```bash
read -s CA_DASHBOARD_SECRET
export CA_DASHBOARD_SECRET
```

The secret is passed to the backend process at startup. Do not put it in the Compose
file, commit it, or add it to a tracked environment file.

### Step 2: validate and start the stack

```bash
docker compose config
docker compose up --build -d
docker compose ps
```

Compose builds the dashboard, installs the backend and ML dependencies, validates the
development ML configuration, runs the authenticated API, and waits for backend health
before starting the dashboard proxy.

### Step 3: open and verify the dashboard

Open <http://127.0.0.1:8080> and sign in with the secret from Step 1. If a different
port is needed, set it before startup:

```bash
export CA_DASHBOARD_PORT=8081
docker compose up --build -d
```

The dashboard will correctly show protection as unavailable because no native collector
is attached in this mode. This is a web/API development mode, not the complete
authentication pipeline.

Useful checks:

```bash
docker compose ps
docker compose logs -f backend dashboard
curl http://127.0.0.1:8080/healthz
```

### Step 4: stop the Compose stack

```bash
docker compose down
```

The named `runtime-data` volume retains disposable `SYNTHETIC` state written under the
container's development storage profile (`config/storage.development.yaml`); this
Compose path never records `PILOT` participant data. To delete that state as well, run
`docker compose down --volumes`; this is destructive and cannot be undone.

## 3. Start the complete application on Windows

The complete path is:

```text
Windows input -> native collector -> named pipe -> backend ingestion
              -> shared ML features/models -> risk engine -> API/WebSocket -> dashboard
```

Use separate PowerShell terminals for the backend and collector. Start the backend
first because it creates the named-pipe server; the collector reconnects with bounded
backoff if the pipe is not ready.

### Step 1: create the Python environment

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[backend]"
```

For development and test commands, install `.[backend,dev]` instead.

### Step 2: build the dashboard

```powershell
Set-Location dashboard
npm ci
npm run typecheck
npm test
npm run build
Set-Location ..
```

The integrated backend serves `dashboard/dist` directly, so no separate dashboard
server is needed for the full native run.

### Step 3: build and test the native collector

```powershell
cmake -S collector -B build/collector -DBUILD_TESTING=ON
cmake --build build/collector --config Release
ctest --test-dir build/collector -C Release --output-on-failure
```

The collector executable is normally written to
`build/collector/Release/continuous_auth_collector.exe` by the Visual Studio generator.

### Step 4: create local runtime directories and set the secret

```powershell
$runtimeRoot = Join-Path $env:LOCALAPPDATA "ContinuousAuthentication\Development"
$artifactRoot = Join-Path $runtimeRoot "models"
New-Item -ItemType Directory -Force $artifactRoot | Out-Null
$env:CA_DASHBOARD_SECRET = Read-Host "Temporary local dashboard secret"
```

Runtime databases, audit records, and model artifacts stay outside the repository and
must never be committed. By default this run records real `PILOT` participant data
through `config/storage.pilot.yaml`; pass `--storage-config
config/storage.development.yaml` to record disposable `SYNTHETIC` data instead.

### Step 5: start the integrated backend and ML engine

In the first PowerShell terminal, with the virtual environment active:

```powershell
python -m backend.app.runtime.cli `
  --participant-id <pseudonym> `
  --artifact-root $artifactRoot `
  --dashboard-directory dashboard/dist
```

This command validates configuration, opens the SQLite/WAL store, starts a `PILOT`
collection session against the default `config/storage.pilot.yaml` profile (pass
`--storage-config config/storage.development.yaml` for a disposable `SYNTHETIC`
session instead), loads any active schema-compatible model profile, starts ingestion
and risk processing, creates the Windows named pipe, and serves the API, WebSocket, and
built dashboard at `127.0.0.1:8765`.

An absent model remains loud and fail-open. The user may remain `ENROLLING` or the
system may report model unavailability until a reviewed model artifact is trained and
activated; startup must not invent or auto-promote training data.

### Step 6: start the event collector

In a second PowerShell terminal from the repository root:

```powershell
.\build\collector\Release\continuous_auth_collector.exe `
  --config config/collector.development.yaml `
  --categories config/app_categories.yaml
```

The collector installs Windows-wide keyboard and mouse hooks, assigns monotonic capture
timestamps inside the callbacks, immediately converts each platform key identifier to
a content-free key class, and sends framed events over the local named pipe. It never
persists raw input events.

If Windows security software or policy blocks hook installation, the collector exits
with `collector hook startup failed`. Permission and real-hardware validation require a
human and cannot be bypassed with Docker.

### Step 7: verify the running pipeline

First verify process liveness:

```powershell
Invoke-RestMethod http://127.0.0.1:8765/healthz
```

Then open <http://127.0.0.1:8765>, sign in with `CA_DASHBOARD_SECRET`, and check:

1. System Health receives collector heartbeats.
2. Dropped events remain zero during normal input.
3. Keyboard and mouse activity produces feature-window activity after the configured
   window closes.
4. Availability alerts are visibly different from behavioral alerts.

Do not expect a risk score from idle or sparse activity: `INSUFFICIENT_DATA` holds the
current state and does not create an anomaly.

### Step 8: shut down cleanly

1. Press `Ctrl+C` in the collector terminal so the global hooks are removed.
2. Press `Ctrl+C` in the backend terminal so the runtime, pipe, database, and audit
   resources close cleanly.
3. Clear the session secret if the terminal will remain open:

```powershell
Remove-Item Env:CA_DASHBOARD_SECRET
```

## 4. Start the complete application on macOS

The ordering is the same as Windows; the runtime selects macOS implementations from
`sys.platform`, while the collector selects them from `__APPLE__` at build time.

### Step 1: build the application

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[backend]"

cd dashboard
npm ci
npm run typecheck
npm test
npm run build
cd ..

cmake -S collector -B build/collector -DBUILD_TESTING=ON
cmake --build build/collector
ctest --test-dir build/collector --output-on-failure
```

### Step 2: grant native input permission

In **System Settings → Privacy & Security → Input Monitoring**, enable the terminal used
to start the collector, then restart that terminal. A denied permission causes a loud
startup failure; the collector never silently runs with zero capture confidence.

### Step 3: start the backend and collector

In the backend terminal:

```bash
runtime_root="$HOME/Library/Application Support/ContinuousAuthentication"
artifact_root="$runtime_root/models"
mkdir -p "$artifact_root"
chmod 700 "$runtime_root" "$artifact_root"
read -s CA_DASHBOARD_SECRET
export CA_DASHBOARD_SECRET

python -m backend.app.runtime.cli \
  --participant-id <pseudonym> \
  --artifact-root "$artifact_root" \
  --dashboard-directory dashboard/dist
```

macOS automatically selects `config/storage.macos.pilot.yaml`. For a disposable
synthetic run, explicitly pass `--storage-config
config/storage.macos.development.yaml`.

In the collector terminal:

```bash
./build/collector/continuous_auth_collector \
  --config config/collector.development.yaml \
  --categories config/app_categories.yaml
```

The backend owns a mode-`0600` Unix socket beneath a mode-`0700` per-user runtime
directory. Start either process first: the collector uses the same bounded reconnect
logic as Windows. Participant pause/resume also selects the macOS signal automatically:

```bash
python -m tools.collection --config config/collection.pilot.yaml pause \
  --event-name continuous-auth-pause-v1
python -m tools.collection --config config/collection.pilot.yaml resume \
  --event-name continuous-auth-pause-v1
```

Press `Ctrl+C` in both terminals for clean shutdown.

## 5. Troubleshooting

### Dashboard is offline or login fails

- Confirm the backend terminal is still running.
- Confirm the same secret entered at login was set before backend startup.
- Rebuild `dashboard/dist` and ensure `--dashboard-directory dashboard/dist` is present.
- Check that port `8765` is not already in use.

### Collector keeps reconnecting

- Start the integrated backend before the collector.
- Confirm both processes use `continuous-auth-v1` from
  `config/collector.development.yaml`.
- Confirm both processes run in the same Windows user session.

### Backend reports that the collector transport is unsupported

The full integrated runtime was started on Linux, WSL, or another unsupported host.
Run the complete pipeline directly on Windows or macOS. Use Compose only for
dashboard/API development.

### Model is missing or rejected

This is intentional fail-open behavior when there is no active profile, the artifact is
corrupt, or its feature-schema version differs from the running extractor. Do not copy
an arbitrary model into the artifact directory. Train and activate it only through the
reviewed enrollment and Model Update Manager workflow.

### Local development data needs resetting

Stop the application first. Preserve any state needed for diagnosis before removing
it. Development storage lives under
`%LOCALAPPDATA%\ContinuousAuthentication\Development`; participant data must never be
used with the supplied synthetic-only configuration.

## 6. Pre-handoff verification

Before handing off a startup or deployment change, run:

```powershell
python protocol/codegen/generate.py --check
python tools/guardrails/check.py
python -m pytest
python -m mypy backend ml tools
```

Also run the collector and dashboard build/test commands from Sections 3.2 and 3.3 on
their supported toolchains. Skipped required tests do not count as passing.
