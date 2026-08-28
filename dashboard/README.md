# Authenticated dashboard

The React/Vite dashboard provides Overview, Live, Alerts, History, System Health, and
Profiles views. It authenticates through an HTTP-only local session cookie, consumes
snapshot/replay C8 envelopes with monotonic cursors, reconnects after gaps, and marks
offline/stale states as unavailable rather than normal. Behavioral and availability
alerts have distinct visual treatments; demo replay is unmistakably labelled synthetic.

```powershell
npm install
npm run typecheck
npm test
npm run build
```

The backend can serve `dist/` at `/`. Stopping this UI cannot change a decision or action.
Co-location is a known limitation: a session occupant can observe risk. A production
operator console should be separated from the monitored endpoint.


