# Continuous Authentication Using Behavioral Biometrics

Local-first, Windows-primary continuous verification using content-free keyboard and
mouse dynamics. Shared contracts live only in `protocol/`, feature computation lives
only in `ml/features/`, and every operational/security tunable lives in `config/`.

## Implemented software baseline

- Generated JSON Schema/Pydantic/C++ C1–C9 contracts and privacy guardrails.
- Deterministic synthetic fixtures; bounded native Windows hooks, QPC timestamps,
  content-free key classification, context/device metadata, pause, heartbeat, and named
  pipe transport.
- Ordered ingestion, shared feature windows, independent per-user models, calibration,
  context confidence, risk/state policy, fail-open enforcement adapters, SQLite/audit,
  retention, and model-update promotion/rollback.
- Authenticated loopback REST/WebSocket API with bounded replay/snapshot resync and a
  six-view React dashboard.
- Consent-aware collection health, immutable day-disjoint freeze, full robustness
  matrix ledger, threshold/config freeze, runtime benchmark helpers, E1/E2 calculations,
  and Windows packaging scripts.

## Local verification

Python 3.11+ is required; Node and a C++17 compiler are needed for their components.

```powershell
python -m pip install -e ".[backend,dev]"
python protocol/codegen/generate.py --check
python tools/guardrails/check.py
python -m pytest
python -m mypy backend/app ml tools
cmake -S collector -B build/collector -DBUILD_TESTING=ON
cmake --build build/collector --config Release
ctest --test-dir build/collector -C Release --output-on-failure
cd dashboard
npm ci
npm run typecheck
npm test
npm run build
```

See `docs/architecture.md` for component/privacy boundaries,
`docs/deployment/windows.md` for packaging, `docs/pilot/` for collection operations,
`docs/update-manager.md` for G1–G6, and `docs/evaluation.md` for evidence rules.

## Evidence still requiring humans

The repository cannot manufacture consent, participant collection days, real Windows
hook/timing evidence, security approval for OS enforcement actions, informed mimicry,
live takeover, clean-machine/soak evidence, or academic review. Development thresholds
are explicitly unreviewed and no accuracy is claimed. Participant data, databases,
credentials, logs, evidence, and sensitive model artifacts remain ignored by Git.

