# Runtime architecture and privacy boundaries

The Windows collector is the only component that observes native input callbacks. It
converts a callback-local platform key identifier immediately to a broad key class and
only content-free C1 events cross the named-pipe boundary. Ordered events remain in a
bounded memory ring until the shared feature extractor emits C2 aggregates; raw streams
are never written to storage.

```mermaid
flowchart LR
  OS["Windows input callbacks"] --> COL["C++ collector<br/>QPC, class conversion, bounded queue"]
  COL -->|"C1 framed events over local named pipe"| ING["Python ingestion<br/>validation, order, session, segment"]
  ING --> FEAT["Shared ml/features windowing"]
  FEAT --> MODEL["Per-user keyboard and mouse models"]
  MODEL --> RISK["Context confidence and risk/state policy"]
  RISK --> ACT["State-gated local enforcement adapters"]
  RISK --> STORE["SQLite aggregates and hash-chained audit"]
  STORE --> API["Authenticated loopback REST and WebSocket"]
  API --> UI["Monitoring dashboard"]
  STORE --> UPDATE["Quarantine and model-update promotion gate"]
  UPDATE --> MODEL
```

The dashboard and API are observers, not enforcement dependencies. A dashboard outage
cannot alter policy or action execution. Collector, pipe, model, storage, and watchdog
failures produce availability signals; enforcement remains fail-open, while bounded
replay/snapshots restore dashboard consistency after reconnect.

Persisted records are feature windows, scores, decisions, state/lifecycle metadata,
alerts, health/metrics, verification anchors, model metadata, candidate dispositions,
attacker-drill session declarations, and audit records. Typed content, raw key
identifiers, titles, document names, paths, URLs, clipboard data, and screen data are
prohibited at every boundary. Context affects only confidence with a configured nonzero
floor and never enters identity-model arrays.

## Attacker-drill data-flow boundary

A live attacker drill (ADR-014, `PLAN.md` §13.3) is declared per process with
`--drill-label` and recorded in the `drill_sessions` table, keyed by `session_id`.
Session grain is deliberate: a drill *is* a session — one person sits down, operates the
machine, and leaves. Recording it there rather than on `feature_windows` leaves the
protocol-generated `FeatureWindow`, the freeze digest, and every existing table
untouched, and gives every corpus loader one place to filter.

The flow is deliberately one-way. A drill window is **scored, context-assessed,
risk-fused, smoothed, escalated, enforced, alerted, audited, and streamed to the
dashboard** — suppressing any of that would mean the drill was not testing the live
system. What it may never do is become training data:

```
attacker window
  ├─→ score · risk · state transition · escalation · enforcement · alert · audit   ALLOWED
  ├─✗ every corpus loader            (drill_sessions filter, five call sites)
  │     └─✗ health · build_freeze · verify_freeze · load_frozen_corpus
  │           └─✗ require_enrollment_admission   (WINDOW_NOT_IN_FROZEN_CORPUS)
  │           └─✗ train_one_class_model · calibration · baseline construction
  │           └─✗ update candidate construction  (reads the frozen TRAIN partition only)
  ├─✗ update-candidate submission    (suppressed for drill sessions)
  ├─✗ automatic A1 login anchor      (the attacker did not authenticate)
  ├─✗ context-confidence learning    (suspended; assessment continues)
  └─✗ enrollment/calibration progress (suppressed; live risk state is NOT suppressed)
```

The last line is the distinction that matters most. *Enrollment and calibration
progress* — the bookkeeping that ages a user from `ENROLLING` to `CALIBRATING` to
`ACTIVE` — is suppressed, because attacker behaviour must not advance the legitimate
user's enrollment. *Live risk-state transitions*, including `DEGRADED`, and the entire
escalation ladder are **not** suppressed, because the drill exists to exercise them.
`RuntimeOrchestrator._progress_enrollment_and_calibration` is named for that boundary.

`backend/app/storage/drill.py` holds the single definition of corpus eligibility. Every
reader of `feature_windows` whose output can reach training applies it — there are five,
and they were enumerated rather than assumed:

| Site | Purpose |
| --- | --- |
| `tools/collection/repository.py` | health, `build_freeze`, `verify_freeze` |
| `tools/collection/corpus.py` | training / enrollment / validation / evaluation corpus |
| `backend/app/runtime/orchestrator.py` | enrollment-progress counts |
| `ml/experiments/app_usage_study.py` | dataset statistics |
| `tools/demo/bootstrap_first_model.py` | synthetic demo training |

`tools/collection/corpus.py` reads `feature_windows` directly rather than through
`load_window_summaries`, so it repeats the filter instead of inheriting it: a corpus
loader must never depend on someone else having filtered first.

Three readers deliberately do **not** filter, because excluding drill data there would
hide the drill rather than protect the corpus: retention (drill windows age out like any
other), the provenance lookup taken while storing a score, and the operator-facing
profile view in the API, where hiding a live drill from the operator watching it would be
actively misleading. That view is display only and drives no training, calibration, or
state decision.

A database predating storage migration `0004_attack_drill` raises
`DRILL_TABLE_MISSING` rather than silently answering "not a drill". Guardrail G12 fails
the build if a corpus loader stops filtering. The behavioural tests in
`backend/tests/test_attack_drill.py` are the real protection — they assert both halves of
the invariant, with controls proving the suppression is drill-specific and not a disabled
drill; G12 catches careless deletion.

All contracts originate in `protocol/`, all feature computations originate in
`ml/features/`, and all tunable thresholds, capacities, cadences, and quality limits
originate in `config/`. The default launcher records real `PILOT` data against a
separately reviewed storage and collection profile; provenance is derived solely from
the storage profile in use, with no other place to set it (spec Section 7.3). Runtime
tunables in the api, risk, context, orchestration, updates, and enforcement configs
remain unreviewed development placeholders regardless of which storage profile is
active.
