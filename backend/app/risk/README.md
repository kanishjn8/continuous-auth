# T-013 risk engine

The engine consumes authoritative, risk-oriented C3 scores and emits C4 decisions. It
renormalizes weights over available modalities, applies context confidence, EWMA and
K-of-N evidence, then uses hysteresis and the graded action ladder. Insufficient-data
windows do not alter smoothing, breach history, or the held internal risk state.

Enforcement is possible only in `ACTIVE`. `CALIBRATING` produces a complete shadow
trace, while component/model failures transition to `DEGRADED`, emit a distinct HIGH
availability alert, and produce a fail-open `NONE` action. Heartbeat loss is separately
typed as `TAMPER` and also fails open; it never terminates a session by exception.

`config/risk.development.yaml` is deliberately synthetic/development-only. The O3/O7/
O8/O9 values remain open and require the experiments and human approval named in
`PLAN.md`; this implementation does not close those decisions.

Run focused checks from the repository root:

```powershell
python -m pytest backend/tests/test_risk_engine.py -q
```
