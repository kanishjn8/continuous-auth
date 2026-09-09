# Continuous Authentication Using Behavioral Biometrics

Local-first, Windows-primary continuous verification using content-free keyboard and
mouse dynamics. Shared contracts live only in `protocol/`, feature computation lives
only in `ml/features/`, and every operational/security tunable lives in `config/`.

## Study design — single-participant enrollment, unseen live attacker

The delivered study has **one participant** (ADR-014,
`docs/adr/ADR-014-single-participant-enrollment-and-unseen-attacker.md`). The model is
trained, calibrated, and validated **only** on the legitimate user's own data. There is
no second enrolled participant, and none is required in order to activate a profile.

- **FRR is measured** on the participant's own held-out VALIDATION partition, at the
  operating point the live system actually uses (`risk.medium_threshold` on the
  calibrated risk scale).
- **FAR is recorded as UNMEASURED** — `false_acceptance_rate = None` — because a
  false-acceptance rate requires impostor data and a single-participant corpus contains
  none. It is never written as `0.0`, never estimated, and never imputed. FAR is **not**
  an activation gate for a first profile.
- **The impostor evidence is a post-activation live attacker drill.** After the corpus is
  frozen and the profile is `ACTIVE`, a classmate who has never contributed data operates
  the machine. Their session is declared with `--drill-label`, and is scored, risk-assessed,
  enforced, alerted, and audited — but is permanently excluded from every training,
  calibration, validation, enrollment, and update corpus. The drill reports a risk
  trajectory and detection latency, **not** a FAR.
- **Cross-participant FAR/EER evaluation remains implemented and available** as optional
  research functionality. It runs automatically if a cohort ever exists. It is not a
  prerequisite for anything in this pilot.

## Implemented software baseline

- Generated JSON Schema/Pydantic/C++ C1–C9 contracts and privacy guardrails.
- Deterministic synthetic fixtures; bounded native Windows hooks, QPC timestamps,
  content-free key classification, context/device metadata, pause, heartbeat, and named
  pipe transport.
- Ordered ingestion, shared feature windows, independent per-user models, calibration,
  context confidence, risk/state policy, fail-open enforcement adapters, SQLite/audit,
  and retention.
- Model-update promotion, quarantine, validation, activation, and rollback — the
  **capability exists in the architecture and codebase and is tested**, but it is
  **intentionally disabled for this single-user pilot** and promotes nothing. See
  "Model updates" below.
- Attacker-drill session isolation: `drill_sessions`, a single centralized corpus
  eligibility filter, and login-anchor / context-learning / enrollment-progress
  suppression for declared drills.
- Authenticated loopback REST/WebSocket API with bounded replay/snapshot resync and a
  six-view React dashboard.
- Consent-aware collection health, immutable day-disjoint freeze, full robustness
  matrix ledger, threshold/config freeze, runtime benchmark helpers, E1/E2
  implementations, and Windows packaging scripts.

## Model updates — implemented, and disabled for this pilot

The distinction matters and is not softened anywhere in this repository:

- **The capability exists.** G1–G6 promotion, quarantine, scheduled cadence, held-out
  validation, atomic activation, and rollback are all implemented and covered by tests.
- **This pilot does not enable or promote updates.** An update may only replace an active
  profile when it is shown not to have raised FAR. FAR needs impostor evidence; this
  project's only impostor evidence is the attacker drill, which is excluded from every
  corpus by design. A candidate's FAR is therefore unmeasurable *by construction*.
  `UpdateManager._validate` refuses with `VALIDATION_FAR_UNMEASURED`, and
  `python -m tools.updates run` refuses earlier still — before any training or artifact
  creation — with `UPDATE_REQUIRES_IMPOSTOR_COHORT`.
- **No gate was weakened to reach this state.** `quarantine_days: 7`,
  `retraining_cadence_days: 7`, `regression_tolerance: 0.02`,
  `min_promotable_windows: 10`, and `ml/training/gate.py` are unchanged.
- Consequently **E1 (drift benefit) and E2 (poisoning resistance) are unrun**, and are
  reported as pending a cohort round rather than as results.

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

See `docs/adr/ADR-014-single-participant-enrollment-and-unseen-attacker.md` for the
study-design decision, `docs/architecture.md` for component/privacy boundaries,
`docs/deployment/windows.md` for packaging, `docs/pilot/` for collection operations,
`docs/pilot/attack-drill-protocol.md` for the attacker-drill procedure,
`docs/update-manager.md` for G1–G6, and `docs/evaluation.md` for evidence rules.
See `startup.md` for Docker control-plane startup and the complete ordered Windows
startup procedure, including the native event collector.

## Evidence still requiring humans

The repository cannot manufacture consent, participant collection days, real Windows
hook/timing evidence, security approval for OS enforcement actions, informed mimicry,
live takeover, clean-machine/soak evidence, or academic review. Development thresholds
are explicitly unreviewed and no accuracy is claimed. Participant data, databases,
credentials, logs, evidence, and sensitive model artifacts remain ignored by Git.

## Known limitations of this build

Stated here rather than only in the report, so the repository does not overstate itself:

- **N=1.** One participant, self-collected and non-blind (the participant is the author).
  No cohort spread, no per-user distribution, no bootstrap confidence intervals over
  users — none of those are computable and none are presented.
- **No cross-user FAR, EER, ROC, or DET.** I1 zero-effort cross-evaluation needs a second
  enrolled user. The I2 informed-mimicry experiment likewise needs a cohort member and is
  not run.
- **The drill is a small sample.** One unseen classmate is a strong threat-model match and
  a weak statistical claim simultaneously. Both facts are reported together, always with N.
- **TRAIN is two calendar days, one of them partial.** The enrollment-length experiment
  (`PLAN.md` §10.5, open decision O3) has not been run, so the two-day training-admission
  policy in `config/ml.development.yaml` is provisional and argued rather than measured.
- **Model updates are disabled**, so E1 and E2 produce no result for this build.
- **Runtime tunables in the api, risk, context, orchestration, updates, and enforcement
  configs remain unreviewed development placeholders.** Only the storage and collection
  profiles have been reviewed for pilot use.
