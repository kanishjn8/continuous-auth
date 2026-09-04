# Windows deployment and verification

This baseline is Windows-primary. It uses a low-level keyboard/mouse hook in the
collector, a local named pipe, a loopback-only FastAPI service, and a co-located static
dashboard. The dashboard placement is a known threat-model limitation: an attacker at
the session may see risk changes. It is never part of the enforcement path.

## Prerequisites

- 64-bit Windows 10/11
- Python 3.11 or newer (`py` launcher available)
- CMake 3.20+ and Visual Studio C++ build tools
- Node.js 20.19+ (or 22.12+) and npm for package creation only

## Create and install a package

From a PowerShell prompt at the repository root:

```powershell
python protocol/codegen/generate.py --check
python tools/guardrails/check.py
python -m pytest
powershell -ExecutionPolicy Bypass -File deployment/windows/package.ps1 -Version 0.2.0
```

The packaging command emits the archive and a SHA-256 sidecar. Verify the archive
against that sidecar before extraction and retain both with the clean-machine evidence.

Extract the resulting archive on the clean test machine and run:

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1
$env:CA_DASHBOARD_SECRET = Read-Host "Temporary local dashboard secret"
powershell -ExecutionPolicy Bypass -File run-development.ps1 -ParticipantId <pseudonym>
```

Open `http://127.0.0.1:8765`. The default `-StorageProfile` is `storage.pilot.yaml`, so
this records real `PILOT` participant data. Pass `-StorageProfile
storage.development.yaml` to select the synthetic profile and record disposable
`SYNTHETIC` data instead.

## Human acceptance matrix

Record evidence beneath ignored `local-evidence/` for: hook coverage across application
focus changes; capture-time interval fidelity; repeat/button/scroll semantics; prompt
pause/resume; named-pipe reconnect; zero drops at typical human rates; stress drop
policy; clean shutdown; multi-hour memory/buffer/database growth; and a clean-machine
install. OS reauthentication/termination adapters require security review before they
replace the default fail-open adapter.

No Windows result is claimed by the repository until that evidence exists and reviewers
approve it. A successful macOS/Linux compile validates portable core code only.
