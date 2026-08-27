# Backend

The backend validates length-prefixed C1 frames, preserves capture timestamps and event
ordering, assigns sessions/segments, calls the single `ml/features` implementation,
scores the active per-user profile, evaluates context/risk, runs state-gated fail-open
actions, persists approved aggregates/audit, and publishes authenticated C7/C8 state.

The complete Windows synthetic-development process is:

```powershell
$env:CA_DASHBOARD_SECRET = Read-Host "Temporary local dashboard secret"
python -m backend.app.runtime.cli --synthetic-user synthetic-user `
  --artifact-root "$env:LOCALAPPDATA/ContinuousAuthentication/Development/models" `
  --dashboard-directory dashboard/dist
```

It binds to `127.0.0.1`, stores only `SYNTHETIC` provenance with the development storage
profile, and starts the Windows named-pipe server. Participant provenance needs a
separately reviewed storage/collection configuration and a reviewed A1 authentication
integration; the synthetic launcher deliberately cannot enable it.

For API-only diagnostics, `backend.app.main:app` exposes process liveness. API/dashboard
failure is outside the enforcement path. Model absence/corruption and storage/action
failure remain loud and fail-open.


