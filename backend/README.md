# Backend

The backend validates length-prefixed C1 frames, preserves capture timestamps and event
ordering, assigns sessions/segments, calls the single `ml/features` implementation,
scores the active per-user profile, evaluates context/risk, runs state-gated fail-open
actions, persists approved aggregates/audit, and publishes authenticated C7/C8 state.

The complete Windows collection process is:

```powershell
$env:CA_DASHBOARD_SECRET = Read-Host "Temporary local dashboard secret"
python -m backend.app.runtime.cli --participant-id <pseudonym> `
  --artifact-root "$env:LOCALAPPDATA/ContinuousAuthentication/Pilot/models"
```

It binds to `127.0.0.1` and starts the Windows named-pipe server. By default it uses
`config/storage.pilot.yaml` and records real `PILOT` participant data. Passing
`--storage-config config/storage.development.yaml` instead records disposable
`SYNTHETIC` data under the development storage profile. Provenance is derived solely
from the storage profile in use; there is no other place to set it.

For API-only diagnostics, `backend.app.main:app` exposes process liveness. API/dashboard
failure is outside the enforcement path. Model absence/corruption and storage/action
failure remain loud and fail-open.


