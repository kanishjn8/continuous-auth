# Demo runbook

1. Start the packaged runtime and collector against the demo's `SYNTHETIC` storage
   profile (`config/storage.development.yaml`); the demo intentionally does not use
   the `PILOT` default.
2. Sign in to the loopback dashboard.
3. Show Overview, Live, Alerts, History, System Health, and Profiles.
4. Stop the collector and show the tamper/availability state after the configured
   heartbeat timeout; restart it and show reconnect.
5. Disconnect the dashboard and demonstrate that runtime decisions continue, then show
   snapshot resynchronization.
6. Use the clearly labelled demo replay only as a presentation fallback.

Never imply that replay data is live, that `DEGRADED` is protected, or that development
thresholds are approved. The fallback is a recorded synthetic trace, not participant
data. Keep a Windows acceptance report and fault-matrix report in ignored evidence
storage for the live demonstration.
