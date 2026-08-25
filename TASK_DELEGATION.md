# Continuous Authentication Using Behavioral Biometrics — Task Delegation

**Source baseline:** `PLAN.md` v2.0 — Working Baseline  
**Team:** Joel, Manas, Akshay, Kanish  
**Purpose:** Implementation-ready ownership, dependency, interface, test, and delivery specification for human developers and coding agents.

> This document allocates implementation work; `PLAN.md` remains authoritative for scope, principles, ADRs, algorithms, schemas, experiments, and open decisions. If the two conflict, stop work and request a reviewed plan/ADR change.

## 1. Instructions for coding agents

Before changing code, an agent must:

1. Read all of `PLAN.md`, then all of this document and any applicable `AGENTS.md`.
2. Inspect the repository and current branch; do not assume the planned skeleton is already present.
3. Select a task ID, confirm its owner, dependencies, contract version, and acceptance criteria, and state the intended scope in the PR.
4. Follow the architecture and all firm ADRs. Never silently resolve an `[OPEN]` choice.
5. Make shared-interface changes in `protocol/` first, regenerate consumers, and obtain both producer and consumer review.
6. Reuse shared feature, configuration, storage, and protocol utilities. Do not create parallel implementations.
7. Keep changes inside the assigned module unless an interface update is necessary and reviewed.
8. Implement working behavior, validation, error handling, telemetry, documentation, and tests—not placeholders for core paths.
9. Use synthetic data for development fixtures. Never commit or expose participant data.
10. Run the narrow tests for the task, relevant cross-boundary contract tests, guardrails, and the affected component’s full suite.
11. Verify observable acceptance criteria and attach evidence to the PR.
12. Stop and ask when a requirement is ambiguous or would change an ADR, core principle, privacy boundary, security policy, or evaluation method.

### Non-negotiable prohibitions

- No typed characters, keycodes, window/document titles, URLs, clipboard contents, screenshots, accessibility/UI-tree data, or ordered reconstructable text may cross the collector callback boundary or be persisted.
- No time-of-day or day-of-week identity feature; dates exist only for audit and day-disjoint splitting.
- No application-specific model, application plugin, or code path that disables monitoring.
- No other user’s data in a user’s one-class training set.
- No training-data path around the Model Update Manager promotion gate.
- No tunable threshold, weight, cadence, window size, or quality limit hardcoded outside `config/`.
- No enforcement from one anomalous window; no dashboard dependency in enforcement.
- No model load on feature-schema mismatch; no participant data in Git.

## 2. Scope and decisions

The system is a local-first, Windows-primary, OS-wide verification layer using content-free keyboard and mouse dynamics. The canonical path is:

```text
OS input
  -> C/C++ collector -> framed named-pipe IPC -> Python ingestion
  -> shared feature extraction -> per-user modality models + calibration
  -> availability fusion -> context confidence -> EWMA + K-of-N
  -> state-gated decision -> SQLite/JSONL -> REST/WebSocket -> React dashboard
                                      -> gated model-update workflow
```

Baseline technologies, features, schemas, state names, risk actions, promotion gates, evaluation experiments, and exclusions are exactly those in `PLAN.md`. The following necessary choices are not final requirements:

- **Engineering Recommendation:** keep the planned Windows named-pipe, length-prefixed binary transport until the Phase 1 benchmark proves it insufficient. Preserve a transport abstraction; do not begin with shared memory.
- **Engineering Recommendation:** use generated, typed protocol bindings and golden contract fixtures in both C++ and Python. The exact schema code generator is selected in T-002 and recorded, not duplicated manually.
- **Engineering Recommendation:** use SQLite migrations with a schema-version table and transactional startup checks. The plan specifies SQLite/WAL but not a migration library.
- **Engineering Recommendation:** use short-lived local dashboard sessions with a securely stored password hash. The precise local authentication scheme is selected during T-017 security review; do not add cloud identity.
- **Engineering Recommendation:** package the Windows baseline as a versioned local installer only after clean-machine verification. Linux/X11 remains optional and must not delay Windows milestones.

All `PLAN.md` open decisions O1–O14 remain experimental decisions. Their designated task records evidence and updates the relevant ADR/configuration; no developer chooses a value by preference.

## 3. Ownership model and boundaries

| Member | Primary ownership | Substantial implementation responsibility | Required cross-review |
| --- | --- | --- | --- |
| **Joel** | Protocol governance; Python ingestion, storage/API; risk/decision/state orchestration | CI architecture, configuration validation, end-to-end integration lead | Kanish reviews protocol/IPC; Manas reviews feature/score boundaries; Akshay reviews API/WebSocket contracts |
| **Manas** | Shared feature pipeline; per-user modelling, calibration, and evaluation | Context-confidence implementation and statistical reporting | Joel reviews production scoring/risk handoff; Akshay reviews collection/evaluation provenance |
| **Akshay** | Ethical collection workflow/tooling; React dashboard and realtime client | Model Update Manager, quarantine, rollback, and update experiments | Joel reviews security gates/storage; Manas reviews retraining validation |
| **Kanish** | **All C/C++ work:** native hooks, timestamping, tokenisation, device/context capture, buffer, heartbeat, IPC | Synthetic generator, cross-language reliability/benchmark/fault-injection tooling, packaging and runtime profiling | Joel reviews ingestion contract; Manas reviews timing/feature fidelity; Akshay reviews health telemetry |

Ownership means accountable implementer and maintainer, not sole reviewer. Human-only study activities are shared as listed in Section 10 and never delegated to an automated agent.

## 4. Repository structure and code ownership

Use the exact top-level structure from `PLAN.md`:

```text
continuous-authentication/
├── AGENTS.md, PLAN.md, TASK_DELEGATION.md, README.md
├── protocol/{schemas,codegen,VERSION}
├── collector/{src/{hooks,timing,classify,context,ipc,buffer},include,tests,CMakeLists.txt}
├── backend/{app/{ingestion,features,models,risk,decisions,updates,storage,api,websocket},tests,pyproject.toml}
├── ml/{datasets,features,training,calibration,evaluation,baselines,experiments,notebooks}
├── dashboard/{src/{components,views,hooks,api},tests}
├── tools/{synthetic,faultinjection,benchmarks,guardrails}
├── config/{app_categories.yaml,thresholds.yaml,collection.yaml}
├── data/{raw,processed,frozen}                 # always ignored
├── docs/{adr,architecture,experiments,protocol,pilot,meetings}
└── .github/workflows/
```

`protocol/` is the sole shared-contract authority. `ml/features/` is the sole feature implementation imported by training and production inference. `config/` is the sole home of tunable values. Generated files must carry a generated-file marker and must never be hand-edited.

## 5. Shared contracts and integration rules

### 5.1 Contract catalogue

| Contract | Producer / owner | Consumer(s) | Required content and behavior |
| --- | --- | --- | --- |
| C1 Event frames | Collector / Kanish; schema / Joel | Ingestion / Joel | `KeyboardEvent`, `MouseEvent`, `ContextEvent`, `Heartbeat` from Plan §7.2; capture-time monotonic µs timestamp; sequence; device; no content. Length-prefixed ordered frames. Malformed/unknown versions rejected and counted without process failure. |
| C2 Feature window | `ml/features` / Manas | Storage, scoring / Joel+Manas | Plan §7.3 blocks and metadata; quality label and provenance; context separated from identity arrays. Sparse windows yield `INSUFFICIENT_DATA`, not fabricated vectors. |
| C3 Score result | Model layer / Manas | Risk engine / Joel | User/profile/schema/model versions; modality availability; raw and percentile-calibrated modality scores; model failure status. Raw scores never drive decisions. |
| C4 Risk decision | Risk engine / Joel | Persistence, WebSocket, dashboard / Akshay | Fused and smoothed values, confidence and source, thresholds/config version, state, level, action, reason, timestamp/window IDs. Must be replay-auditable. |
| C5 Model artifact | Training / Manas | Runtime loader / Joel; updater / Akshay | User, modality, feature schema, data ranges/provenance, hyperparameters, calibration, metrics, version/checksum. Mismatch or corruption causes `DEGRADED` plus availability alert. |
| C6 Update candidate | Gate / Akshay | Storage / Joel; training / Manas | Segment ID, G1–G6 evidence, A1–A3 anchor, quarantine and incident state, disposition/reason, immutable audit history. A4 can never satisfy verification. |
| C7 REST/OpenAPI | Backend / Joel | Dashboard / Akshay | Current state, profiles/enrollment, history, alerts, metrics, health, administration. Raw feature vectors are not unauthenticated output. Errors have stable code, safe message, correlation ID. |
| C8 WebSocket | Backend / Akshay | Dashboard / Akshay | Versioned envelope, monotonically increasing stream sequence, risk/alert/state/health events, reconnect cursor, snapshot/resync. Disconnect never affects enforcement. |
| C9 Configuration | Joel (schema); module owners (values) | All components | Validated startup configuration, explicit units/ranges/defaults, config version in decisions/experiments. Invalid security-relevant values fail startup loudly; collector/backend failure remains fail-open for users. |

### 5.2 Handoff protocol

1. Producer adds/updates schema, examples, invalid fixtures, and compatibility note.
2. Code generation and contract tests pass for all consumers.
3. Consumer can develop against golden fixtures before producer completion.
4. Producer and consumer jointly run the boundary test; both approve the PR.
5. Breaking changes increment `protocol/VERSION`, migrate storage/artifacts when applicable, and are never merged into only one side.

### 5.3 Dependency chain

```text
T-001 platform spike
  -> T-002 contracts/CI/guardrails -> T-004 collector + T-007 ingestion (parallel)
  -> T-005 collector context/IPC -> T-008 feature windows -> T-010 models
  -> T-011 calibration/evaluation -> T-013 risk/state/decision -> T-016 API/WS
  -> T-017 dashboard

T-003 synthetic generator -> T-004/T-007/T-008/T-013/T-017/T-019 (parallel fixture source)
T-006 collection programme -> frozen corpus -> T-011/T-012/T-015/T-020
T-013 + verification anchors -> T-018 Model Update Manager -> T-019 integration
T-019 integrated system + frozen corpus -> T-020 evaluation -> T-021 finalisation
```

## 6. Implementation work packages

Priority: **P0** gating/irrecoverable, **P1** required baseline, **P2** required after baseline, **P3** stretch only. Effort is relative person-weeks and includes implementation, tests, docs, and review.

### T-001 — Target-platform feasibility spike

- **Owner:** Kanish | **Reviewer:** Joel | **Priority/Effort:** P0 / 0.5
- **Purpose:** Resolve ADR-001 on actual Windows hardware before downstream commitment.
- **Dependencies:** none.
- **Files:** `collector/src/hooks/`, `collector/src/timing/`, `collector/CMakeLists.txt`, `docs/adr/ADR-001*.md`, `docs/experiments/platform-spike.md`.
- **Implementation:** Minimal global keyboard/mouse hook across focus changes; capture timestamp and event count only; record permissions, toolchain, hook backend, CPU and memory. Do not persist key identity.
- **Input/Output/Interface:** Physical OS events -> diagnostic counts/timing/overhead report; no production C1 requirement yet.
- **Errors:** Permission denial, unsupported hook backend, callback failure, and shutdown must be explicit and leave no orphan hook.
- **Tests:** Unit tests for clock abstraction; manual focus-switch matrix; 30-minute resource sample.
- **Acceptance:** Capture works system-wide on target hardware; privacy inspection passes; overhead and permissions documented; ADR-001 confirmed/revised by human sign-off.

### T-002 — Contracts, repository, CI, and guardrails

- **Owner:** Joel | **Reviewers:** all module owners | **Priority/Effort:** P0 / 1.0
- **Purpose:** Give parallel work one versioned source of truth and prevent architectural/privacy regressions.
- **Dependencies:** T-001 informs platform contract; otherwise starts immediately.
- **Files:** `protocol/`, `AGENTS.md`, component skeletons, `.github/workflows/`, `tools/guardrails/`, formatter/type/test configuration, `.gitignore`.
- **Implementation:** Define C1–C9 schemas/OpenAPI; generate C++/Pydantic bindings; establish builds/tests for C++17, Python 3.11+, React/TS; implement every Plan §19.3 guardrail; ensure `data/` cannot be tracked.
- **Inputs/Outputs:** JSON schemas and examples -> generated typed bindings and contract fixtures. CI returns per-component and guardrail status.
- **Errors:** Codegen drift, unknown schema version, forbidden field/dependency, hardcoded threshold, tracked data, and artifact mismatch fail CI with actionable messages.
- **Tests:** Schema positive/negative fixtures, deterministic regeneration/diff check, deliberate guardrail violation fixtures, trivial tests for every component.
- **Acceptance:** `protocol/VERSION` v1 frozen; both language bindings generated; CI green; each guardrail proven to fail on its violation; all four component skeletons build/run/test.

### T-003 — Synthetic event and score generator

- **Owner:** Kanish | **Reviewer:** Manas | **Priority/Effort:** P0 / 0.8 (**non-C/C++ responsibility**)
- **Purpose:** Unblock all software paths without participant data and provide known timing ground truth.
- **Dependencies:** C1/C2 fixtures from T-002.
- **Files:** `tools/synthetic/`, fixtures under component test directories, documentation.
- **Implementation:** Deterministic seeded generation of keyboard-class, mouse, context, heartbeat, gaps, duplicates, malformed frames, modality sparsity, user patterns, anomaly/takeover traces, and known inter-event intervals. Emit provenance `synthetic`.
- **Inputs/Outputs:** Validated scenario configuration + seed -> C1 frames and expected C2/score/risk assertions.
- **Errors:** Invalid distributions/ranges rejected; no silent clipping; output schema validated before write/stream.
- **Tests:** Reproducibility by seed; timing exactness; invariant/property tests; no forbidden content fields.
- **Acceptance:** The same config yields byte-identical logical events; timestamp-fidelity and risk scenarios can consume outputs; synthetic data is visibly labelled and cannot enter headline evaluation.

### T-004 — Native capture, timing, and content-free classification

- **Owner:** Kanish | **Reviewers:** Joel (privacy), Manas (timing) | **Priority/Effort:** P0 / 2.0
- **Purpose:** Produce trustworthy system-wide keyboard/mouse events; this is the project’s highest-impact technical layer.
- **Dependencies:** T-001, C1 v1.
- **Files:** `collector/src/{hooks,timing,classify}/`, `collector/include/`, `collector/tests/`.
- **Implementation:** Platform abstraction; Windows hook backend; `QueryPerformanceCounter` capture inside callback; sequence allocation; complete Plan §6 ADR-004 key-class taxonomy; repeat/button/scroll semantics; raw keycode discarded immediately after classification.
- **Inputs/Outputs:** OS callbacks -> internal generated C1 structs. No disk I/O and no blocking work in callbacks.
- **Errors:** Unsupported events become `OTHER` or counted diagnostic per contract; clock failure and hook loss surface health failure; safe unhook on exit.
- **Tests:** Class mapping, monotonicity, rollover/repeat, click pairing, focus changes; source/schema privacy guards; synthetic timestamp fidelity.
- **Acceptance:** All real event types emit schema-valid, capture-timestamped records; no content crosses callback; known intervals reconstruct within documented tolerance; multi-hour callback memory remains bounded.

### T-005 — Native context, devices, buffer, heartbeat, and IPC

- **Owner:** Kanish | **Reviewers:** Joel, Akshay | **Priority/Effort:** P0 / 2.0
- **Purpose:** Reliably deliver ordered C1 frames and health evidence across the native/Python boundary.
- **Dependencies:** T-002, T-004; app registry interface from T-009.
- **Files:** `collector/src/{context,buffer,ipc}/`, corresponding headers/tests, benchmark notes.
- **Implementation:** Foreground process only (never title/path/URL); app ID/category resolution; device class and resolution/DPI; bounded ring buffer with documented overload policy; heartbeat fields; length-prefixed named-pipe transport behind interface; clean reconnect/shutdown.
- **Inputs/Outputs:** Internal events -> ordered C1 frames. Ingestion availability/backpressure -> health counters.
- **Errors:** Pipe unavailable invokes bounded retry and visible health state; overflow applies configured policy and increments counters; malformed registry data defaults safely to `UNKNOWN`.
- **Tests:** Producer/consumer golden frames, boundary/partial frames, reconnect, overflow, sequence continuity, heartbeat timeout simulation, typical/stress throughput.
- **Acceptance:** Zero drops at typical human rates; stress drop behavior measured; heartbeat exposes uptime/drop/high-water; no unbounded memory; ADR-002 confirmed/revised only from benchmark evidence.

### T-006 — Collection programme and health tooling

- **Owner:** Akshay | **Reviewers:** Manas, privacy review by Joel | **Priority/Effort:** P0 continuous / 1.5 engineering + human operations
- **Purpose:** Obtain an ethical, multi-day, multimodal, provenance-correct corpus before the irrecoverable freeze deadline.
- **Dependencies:** Planning begins immediately; collection build requires T-004/T-005/T-007/T-009.
- **Files:** `docs/pilot/`, `ml/datasets/`, collection administration tooling, `config/collection.yaml`, health dashboard/report, freeze manifests under `data/frozen/` (ignored).
- **Implementation:** Public free-text dataset adapter; pause control; consent/enrollment records; installation/support workflow; per-participant days/windows/modality/quality/device/gap health; A3 periodic verification schedule; immutable freeze manifest and day partitions; provenance audit.
- **Inputs/Outputs:** Consent + locally collected C2 windows -> coverage reports and versioned frozen dataset manifest. Participant identifiers must be pseudonymous.
- **Errors:** Collection gaps/device changes/low coverage alert early; missing consent makes data ineligible; incomplete provenance prevents freeze; pause must stop capture promptly.
- **Tests:** Dataset-loader fixtures, consent eligibility rules, health calculations, freeze reproducibility/checksums, content audit, pause behavior on real machine.
- **Acceptance:** Stage 0 limitation documented; all four members self-collect once stable; pilot data has valid consent; target days/shortfalls reported; frozen splits reproduce exactly; no participant data enters Git.

### T-007 — Ingestion, ordering, sessions, and segments

- **Owner:** Joel | **Reviewer:** Kanish | **Priority/Effort:** P0 / 1.5
- **Purpose:** Validate C1, preserve capture timing, and attribute events without persisting raw streams.
- **Dependencies:** C1 v1, T-003; integrates with T-005.
- **Files:** `backend/app/ingestion/`, ingestion tests.
- **Implementation:** Incremental length-frame decode; schema/version validation; gap/duplicate/out-of-order detection; in-memory bounded raw-event ring; authenticated session lifecycle; 15-minute initial configurable idle segment split; wall-clock anchor for audit only.
- **Inputs/Outputs:** C1 frames -> validated ordered events with `session_id`/`segment_id`, health counters, and lifecycle events for T-008/T-013.
- **Errors:** Reject/count malformed, duplicate, and invalid-order records while continuing; transport/model infrastructure failures emit availability events; never re-timestamp.
- **Tests:** Chunked/partial frames, corrupt lengths, sequence wrap policy, gaps/duplicates, idle boundaries, shutdown/recovery, bounded memory, C++ integration fixtures.
- **Acceptance:** Real and synthetic streams ingest continuously; known timing remains unchanged; invalid records do not crash the pipeline; session/segment IDs attach correctly; raw event retention remains memory-only and bounded.

### T-008 — Shared windowing and feature extraction

- **Owner:** Manas | **Reviewers:** Joel, Kanish | **Priority/Effort:** P0 / 2.5
- **Purpose:** Convert C1 events to deterministic, content-free C2 windows using one implementation for training and inference.
- **Dependencies:** C1/C2, T-003; T-007 for live integration.
- **Files:** `ml/features/`, thin `backend/app/features/` adapter, tests, feature dictionary.
- **Implementation:** Configurable 30s-or-100-keystroke closing; all keyboard and mouse features in Plan §7.3; context block separated; resolution/DPI normalization; Plan ADR-005 labels; per-user normalization artifact; provenance and metadata; discard ordered event state at close.
- **Inputs/Outputs:** Ordered attributed events + config -> C2 window and optional no-score marker. Identity arrays contain keyboard/mouse only.
- **Errors:** Empty/sparse data -> `INSUFFICIENT_DATA`; missing releases or DPI handled deterministically and flagged; no NaN/inf stored; invalid config fails startup.
- **Tests:** Unit formulas, boundary windows, sparse/single/missing-pair events, DPI invariance, class transitions, determinism, property ranges, training/inference parity, content impossibility.
- **Acceptance:** Every listed feature is implemented/documented; identical input/config yields identical vector; no NaN leakage; idle never becomes anomaly; backend imports the shared implementation rather than copying it.

### T-009 — Persistence, audit, retention, and configuration

- **Owner:** Joel | **Reviewer:** Akshay | **Priority/Effort:** P1 / 1.7
- **Purpose:** Persist only approved aggregates and make every state/decision/update reconstructible with bounded storage.
- **Dependencies:** C2/C4/C6/C9, T-002; migrations coordinate with dependent tasks.
- **Files:** `backend/app/storage/`, migrations/schema, JSONL logger, `config/*.yaml`, tests.
- **Implementation:** All Plan §7.4 tables; WAL mode; transactions and indexes; app registry; append-only JSONL audit; daily rotation/compression and configurable retention; schema/config version; access-restricted local paths; raw debug capture off by default and prohibited in pilot.
- **Inputs/Outputs:** C2/C3/C4/C6/health records -> queryable SQLite and JSONL audit; retention job outputs counts/status.
- **Errors:** Migration/integrity/disk-full/permission failure triggers loud availability alert and safe degraded behavior; partial writes roll back; corrupt config rejected.
- **Tests:** Migrations up/down or forward-recovery policy, concurrency/WAL, rollback, retention/rotation, audit parity, disk failure, forbidden-column guard, app-registry behavior.
- **Acceptance:** Required tables and constraints exist; decision audit reproduces inputs; retention bounds growth; storage cannot contain content fields; failure is loud and does not lock out a legitimate user.

### T-010 — Per-user training, calibration, and artifacts

- **Owner:** Manas | **Reviewers:** Joel, Akshay | **Priority/Effort:** P1 / 2.0
- **Purpose:** Train independent, low-cost one-class keyboard/mouse profiles with comparable calibrated scores.
- **Dependencies:** T-008, early T-006 data, C3/C5.
- **Files:** `ml/{training,calibration,baselines}/`, `backend/app/models/`, experiment docs.
- **Implementation:** Per-user/per-modality Isolation Forest; per-user preprocessing; percentile calibration against enrollment distribution; statistical baseline and at least one alternative one-class model; model metadata/checksum/version; atomic load/swap; no impostor training data.
- **Inputs/Outputs:** Eligible genuine day-partitioned C2 windows -> C5 artifacts; C2 inference -> C3 score.
- **Errors:** Too little data keeps user `ENROLLING`; unavailable modality not scored; corrupt/schema-mismatched artifact refused and causes `DEGRADED`; invalid numeric output contained and alerted.
- **Tests:** User isolation, calibration bounds/monotonicity, artifact round-trip/checksum/mismatch, missing modality, reproducibility by seed, inference budget.
- **Acceptance:** A new window scores through production code; artifacts contain every Plan §10.8 field; cross-user training contamination test passes; raw scores never directly feed T-013.

### T-011 — Day-disjoint evaluation and required ML experiments

- **Owner:** Manas | **Reviewer:** Akshay | **Priority/Effort:** P1 / 2.2
- **Purpose:** Resolve O3/O5/O6/O7/O12 with reproducible evidence, not assumptions.
- **Dependencies:** T-006 data, T-008, T-010.
- **Files:** `ml/evaluation/`, `ml/experiments/`, notebooks limited to exploration, `docs/experiments/`.
- **Implementation:** Day-disjoint and leave-one-day-out splits; random split only labelled diagnostic; window FAR/FRR/EER, ROC/DET and per-user distributions; I1 cross-user matrix; dual vs single fusion/missing-modality ablation; baseline comparison; 1/2/3/5/7+ day enrollment curve; feature reduction; config/data/code versions recorded.
- **Inputs/Outputs:** Frozen or declared interim dataset + experiment config -> metrics, figures, reports, ADR/config recommendations.
- **Errors:** Day overlap, missing provenance, insufficient users/days, or evaluation reuse aborts/labels result; no silent user exclusion.
- **Tests:** Split disjointness, metric known examples, leakage detection, reproducibility, per-user aggregation, artifact provenance.
- **Acceptance:** Required comparisons share features/splits/calibration; every eligible user reported; exclusions explicit; enrollment thresholds and fusion choices trace to results; no interim result is mislabelled headline evidence.

### T-012 — Context confidence layer

- **Owner:** Manas | **Reviewers:** Joel, Akshay | **Priority/Effort:** P2 / 1.2
- **Purpose:** Reduce context-driven false positives without creating an evasion path.
- **Dependencies:** T-006 category data, T-011 scores, T-013 integration.
- **Files:** context module under `backend/app/risk/`, `config/app_categories.yaml`, evaluation/tests, dashboard payload update.
- **Implementation:** Bootstrap process category map; neutral `UNKNOWN`; per-user/category genuine-score variance; observation threshold; empirical-over-bootstrap arbitration; configurable hard confidence floor; log source/value/effect.
- **Inputs/Outputs:** Context block + user/category history + fused score -> bounded confidence and trace.
- **Errors:** Unknown/insufficient/corrupt statistics fall back neutrally; invalid floor rejected; never zero or disable scoring.
- **Tests:** Arbitration, fallback, floor anti-evasion, unseen apps, no context in model features, FAR/FRR efficacy comparison.
- **Acceptance:** Monitoring remains active for every app; floor mechanically enforced; effect on FAR/FRR reported; ADR-008 retained/revised only with human sign-off.

### T-013 — Risk engine, state machine, and decision layer

- **Owner:** Joel | **Reviewers:** Manas, Akshay | **Priority/Effort:** P1 / 2.5
- **Purpose:** Turn C3 into stable, graded, auditable C4 decisions without single-window punishment.
- **Dependencies:** T-009/T-010, C3/C4/C9; T-012 plugs in later.
- **Files:** `backend/app/{risk,decisions}/`, scenario tests, `config/thresholds.yaml`.
- **Implementation:** Availability-renormalised fusion; equal initial weights then T-011 results; EWMA + K-of-N; `INSUFFICIENT_DATA` holds state and counters; hysteresis; cooldown/action budget; `CONTINUE/SOFT_CHALLENGE/REAUTH/TERMINATE`; per-user `ENROLLING/CALIBRATING/ACTIVE/DEGRADED/SUSPENDED`; shadow mode; fail-open; heartbeat tamper; full trace.
- **Inputs/Outputs:** C3 + state/context/config -> C4 and distinct behavioral/availability alerts.
- **Errors:** Missing model/backend/feature exception -> `DEGRADED`, enforcement suspended, HIGH availability alert, audit. Heartbeat loss also raises security/tamper event. No exception may accidentally terminate a session.
- **Tests:** Every Plan §13.6 scenario; threshold boundaries; one-window non-enforcement invariant; sustained escalation/recovery; hold semantics; cooldown; state transitions; replay determinism; property/state-machine tests.
- **Acceptance:** Single anomaly cannot enforce; sustained trace follows configured ladder; calibration logs without enforcement; failure is fail-open and loud; every decision reconstructs exactly; all tunables externalized.

### T-014 — Enforcement adapters and verification anchors

- **Owner:** Joel | **Reviewer:** Akshay | **Priority/Effort:** P1 / 1.0
- **Purpose:** Execute graded local actions and generate independent anchors without coupling core policy to UI.
- **Dependencies:** T-013, session lifecycle T-007; OS action mechanism requires security sign-off.
- **Files:** `backend/app/decisions/` adapters, local challenge/reauth integration, tests/docs.
- **Implementation:** Adapter interface for continue, soft challenge, OS/local reauth, termination; action outcome/audit; A1 login/unlock, A2 successful reauth, A3 low-frequency scheduled prompt; action budget. Enforcement active only in `ACTIVE`.
- **Inputs/Outputs:** C4 requested action -> outcome + anchor record. Failed reauth returns explicit failure for policy handling.
- **Errors:** Adapter unavailable/permission denied fails open, alerts, and audits; timeout cannot loop prompts; cancelled/failed verification is never an anchor.
- **Tests:** Mock adapter state scenarios, action budget, anchor authenticity, shadow/degraded non-enforcement, real Windows manual matrix.
- **Acceptance:** Actions and outcomes are auditable; A1–A3 are independently evidenced; A4 low risk alone is rejected; no dashboard dependency; OS-specific behavior documented.

### T-015 — Collection analytics and dataset freeze

- **Owner:** Akshay | **Reviewer:** Manas | **Priority/Effort:** P0 deadline / 1.0
- **Purpose:** Make data shortfalls visible and freeze an evaluation corpus without leakage.
- **Dependencies:** T-006, T-008/T-009.
- **Files:** collection analytics, freeze tool, coverage reports/manifests.
- **Implementation:** Coverage metrics per participant; modality/quality distributions; gap/device-change reports; consent/provenance eligibility; checksum manifest; immutable day-disjoint train/validation/evaluation assignment; post-freeze drift data segregated.
- **Inputs/Outputs:** Stored eligible windows -> health dashboard, versioned manifest/splits.
- **Errors:** Insufficient days/windows reported, not hidden; overlap or checksum drift blocks freeze; late data cannot mutate frozen version.
- **Tests:** Known coverage fixtures, split leakage, manifest rebuild, identifier privacy, provenance mixing.
- **Acceptance:** Weekly health report from first collection; freeze is reproducible and immutable; every headline record maps to consent/provenance and a disjoint partition.

### T-016 — REST API, WebSocket, and health telemetry

- **Owner:** Joel (REST/orchestration) and Akshay (WebSocket stream/resync) | **Accountable owner:** Joel | **Priority/Effort:** P1 / 1.6 total
- **Purpose:** Expose authenticated state/history/health and reliable live C8 events without entering enforcement’s critical path.
- **Dependencies:** C7/C8, T-009/T-013.
- **Files:** `backend/app/{api,websocket}/`, OpenAPI schema, tests.
- **Implementation:** Plan API surfaces; pagination/filtering; authenticated admin operations; versioned stream sequence; snapshot + cursor resync; collector/storage/model/system metrics; correlation IDs; safe serialization.
- **Inputs/Outputs:** Storage/runtime events -> C7 responses/C8 envelopes. Client cursor -> missed-event replay or fresh snapshot.
- **Errors:** Stable safe API error envelope; slow clients bounded/disconnected; reconnect resync; no raw sensitive vectors unauthenticated; UI failure has zero enforcement effect.
- **Tests:** OpenAPI conformance, auth/authorization, pagination, disconnect/reconnect/gap, backpressure, redaction, service-down independence.
- **Acceptance:** Dashboard can reconstruct current state after disconnect; health/alert types preserved; contract generated/tested; enforcement tests pass with API/dashboard stopped.

### T-017 — Authenticated real-time dashboard

- **Owner:** Akshay | **Reviewers:** Joel (security), Kanish (health accuracy) | **Priority/Effort:** P2 / 2.2
- **Purpose:** Make live state, alerts, audit history, profiles, and runtime cost observable and demonstrable.
- **Dependencies:** C7/C8, T-016; can start from fixtures T-003.
- **Files:** `dashboard/src/{components,views,hooks,api}/`, `dashboard/tests/`.
- **Implementation:** Overview, Live, Alerts, History/export, System Health, Profile status; unmistakable behavioral vs availability visual language; WebSocket reconnect/resync; local auth; enrollment progress; update queue/history; labelled replay demo.
- **Inputs/Outputs:** C7/C8 -> accessible visual state and user operations. No enforcement output.
- **Errors:** Offline/stale banner, retry/backoff, last-known timestamp, safe empty/error states; never display “protected/normal” when backend unavailable.
- **Tests:** Components, accessibility, auth, stream reducer/order, reconnect/resync, alert distinction, replay label, API mocks and integrated smoke test.
- **Acceptance:** All required views show live data; system-unavailable state is unmistakable; reconnect becomes consistent; dashboard shutdown does not affect decisions; co-location threat is documented.

### T-018 — Model Update Manager, quarantine, and rollback

- **Owner:** Akshay | **Reviewers:** Joel (security/storage), Manas (model validation) | **Priority/Effort:** P2 / 2.5
- **Purpose:** Adapt to genuine drift while demonstrating resistance to profile poisoning.
- **Dependencies:** T-006 A3 data, T-009/T-010/T-014, C5/C6.
- **Files:** `backend/app/updates/`, training integration, tests, update experiment scripts, dashboard payloads.
- **Implementation:** Segment gates G1–G6; anchors A1–A3 and explicit A4 rejection; quarantined candidate ageing/incident discard; fixed-cadence retraining; held-out genuine + impostor regression gate; atomic activation; retained versions; one-step rollback; reasoned audit.
- **Inputs/Outputs:** Completed segments/anchors/incidents/config -> C6 dispositions, candidate model, validation report, C5 activation/rollback.
- **Errors:** Missing evidence rejects candidate; job failure leaves active model untouched; regression rejects; atomic swap/rollback handles crash; no reactive per-session training.
- **Tests:** Every gate individually and combined, poisoned segment injection, quarantine time, incident invalidation, scheduler, regression, atomic failure, version rollback, bypass guardrail.
- **Acceptance:** Only fully eligible segments promote; poisoned segments fail E2 gate; failed/regressed models never activate; rollback restores prior checksum; all transitions and reasons audit/display correctly.

### T-019 — Integration, fault injection, packaging, and long-run reliability

- **Owner:** Kanish | **Co-owner:** Joel | **Reviewers:** Manas, Akshay | **Priority/Effort:** P1 / 2.2 Kanish + 1.0 Joel (**substantial non-C/C++ responsibility for Kanish**)
- **Purpose:** Make the components operate continuously as one recoverable local system.
- **Dependencies:** T-005, T-008–T-018 baseline complete.
- **Files:** `tools/faultinjection/`, deployment scripts/config, installer, integration tests/docs, reliability reports.
- **Implementation:** Full startup/shutdown/recovery; dependency health; multi-user profile selection; every Plan §13.6 fault; multi-hour/day resource and database/log monitoring; reproducible clean-machine Windows install; configuration consolidation.
- **Inputs/Outputs:** Synthetic/real activity and injected failures -> full pipeline decisions/dashboard/audit/reliability report/install package.
- **Errors:** Component restarts, collector kill, pipe breaks, corrupt model, DB unavailable, malformed events, UI disconnect, device change; prescribed fail-open behavior must persist.
- **Tests:** Automated E2E and fault matrix; real multi-hour/day soak; clean-machine manual install; memory/clock/buffer/DB growth checks.
- **Acceptance:** Unattended E2E MVP; every robustness scenario matches plan; correct multi-user profile with no contamination; no resource growth; documented clean install succeeds; recovery preserves auditable state.

### T-020 — Final accuracy, performance, and security evaluation

- **Accountable owner:** Manas | **Implementation owners:** Manas (metrics/statistics), Kanish (runtime benchmarks), Akshay (update E1/E2 + experiment operations), Joel (threshold freeze + robustness traces) | **Priority/Effort:** P1 / ~1.5 each
- **Purpose:** Produce reproducible evidence for every claim.
- **Dependencies:** Frozen T-015 corpus and integrated T-019 system.
- **Files:** `ml/experiments/`, `tools/benchmarks/`, `docs/experiments/`, generated figures/tables (not participant data).
- **Implementation:** Tune on validation then lock config checksum; window/decision FAR/FRR/EER/ROC/DET; I1 matrix; I2 informed mimicry; live hijack A/B/context traces; collector/backend CPU/memory, throughput, drop rate and p50/p95/p99 latencies; E1 drift benefit/E2 poisoning; robustness matrix; bootstrap CIs; per-user spreads; literature comparison and limitations.
- **Inputs/Outputs:** Frozen splits, fixed artifacts/config, live human protocols -> reproducible metrics/figures/reports with cohort/volume/hardware context.
- **Errors:** Any post-freeze tuning, split leak, missing config/data version, or undisclosed exclusion invalidates the run; weak results are reported honestly, not retuned away.
- **Tests:** Evaluation-code known fixtures; frozen-checksum enforcement; repeat-run equivalence; benchmark clock validity; report traceability audit.
- **Acceptance:** Thresholds frozen before evaluation; I1, I2, and live hijack completed; runtime percentiles/hardware reported; every figure regenerates; cohort size, per-user spread, CIs and limitations are adjacent to claims.

### T-021 — Optimisation, documentation, demo, and reproducibility

- **Accountable owner:** Joel | **Owners:** all, by module | **Priority/Effort:** P1 / 0.8 each
- **Purpose:** Deliver a defensible, navigable, reproducible capstone without speculative changes.
- **Dependencies:** T-020.
- **Files:** READMEs, ADR/protocol/deployment/experiment docs, architecture diagrams, report/presentation assets, reproducibility package.
- **Implementation:** Optimise only measured bottlenecks; final diagrams; install/deployment guide; complete report narrative; demo with recorded fallback; exact configs and commands; limitations; individual contribution records.
- **Inputs/Outputs:** Verified repository and T-020 evidence -> final system, report, presentation, demo, reproducibility package.
- **Errors:** Broken links/commands, unreproducible result, unrecorded ADR divergence, or missing limitation blocks release.
- **Tests:** Fresh-clone build/test/install rehearsal, result regeneration, demo rehearsal, documentation link/config audit.
- **Acceptance:** Another developer can build and reproduce results from instructions; demo has tested fallback; repository clean; every result traces to data/config/code/experiment; contribution record matches merged work.

## 7. Development phases and parallel plan

| Phase / timing | Required work and parallelism | Gate |
| --- | --- | --- |
| 0 — Week 1 | T-001 and early T-002 in parallel; T-003 begins once C1 drafts exist; T-006 consent/recruitment begins immediately | M0/M1: platform proven, protocol v1/codegen/CI/guardrails/synthetic fixtures green |
| 1 — Weeks 2–4 | Kanish T-004/T-005; Joel T-007/T-009; Manas begins T-008 against synthetic; Akshay continues T-006 | M2: timestamp fidelity, privacy boundary, typical-rate zero drops, bounded collector |
| 2 — Weeks 3–6 | T-008/T-009 complete while live collector integrates; collection starts as soon as safe | M3/M4: deterministic feature matrix, idle gate, retention, collection underway |
| 3/4 — Weeks 4–8 | T-006/T-015 continuous; Manas T-010/T-011; Joel prepares C3/C4 consumers; UI uses fixtures | M5: live profile scoring plus required ablation/baseline/enrollment experiments |
| 5 — Weeks 5–9 | Joel T-013/T-014; Akshay and Joel T-016; synthetic scenario tests run before enforcement | M6: graded audited decisions, shadow mode, fail-open/loud failures |
| 6 — Weeks 7–11 | Akshay T-017; Kanish health telemetry; all dogfood collector; never delay core/data for polish | M7: live monitoring with distinct alert classes |
| 7 — Weeks 10–12 | Manas T-012 from category evidence; Akshay ensures required anchors/collection coverage | Context efficacy and confidence floor proven |
| 8 — Weeks 11–13 | Akshay T-018; Manas validation hook; Joel storage/security review | Promotion/poisoning/rollback tests pass |
| 9 — Weeks 12–13 | Kanish+Joel T-019; all owners resolve boundary defects | M8/M9: continuous integrated clean-install system |
| 10 — Weeks 13–15 | T-020; schedule human I2/hijack/runtime work first; freeze thresholds before runs | M10/M11: complete reproducible evidence |
| 11 — Week 16 | T-021 across team | M12: final system/report/demo/reproducibility |

Under schedule pressure preserve the Plan §17.1 order. Linux/X11, I3 replay, joint interaction features, and extra dashboard polish are P3 and may not displace Windows correctness, collection days, day-disjoint evaluation, risk behavior, integration, or required experiments.

## 8. Test ownership and system verification

Each owner writes and maintains unit and component tests for their task. Cross-boundary tests have two owners.

| Test layer | Accountable owner | Required coverage |
| --- | --- | --- |
| C++ collector | Kanish | Hooks, clock, classification, context/device, buffer/drop, heartbeat, framing, reconnect, resource bounds |
| Protocol/guardrails | Joel | Generated parity, valid/invalid fixtures, privacy/schema/config/data/model guards |
| Ingestion/storage/API/risk | Joel | Framing/order, lifecycle, migrations/failures, C7, state/property/scenario tests, audit replay |
| Features/models/evaluation/context | Manas | Formula/property/determinism, parity, user isolation, calibration/artifacts, leakage-free metrics, floor efficacy |
| Dashboard/update manager/collection | Akshay | Realtime reducer/resync, auth/a11y, alert distinction, G1–G6/A1–A4, quarantine/rollback, freeze/provenance |
| Cross-language boundary | Kanish + Joel | Golden C1 frames, partial/malformed streams, timestamp fidelity, throughput |
| Score/risk boundary | Manas + Joel | C3 calibration/availability semantics, model failure, replayed decisions |
| Update/training boundary | Akshay + Manas | Candidate evidence, regression gate, atomic artifact activation |
| E2E/fault/soak/install | Kanish + Joel | Entire Plan §13.6 matrix, multi-user, restarts, multi-day resources, clean machine |
| Final experiments | Manas | Fixed splits/configs, metrics and CIs; task-specific owners supply human/runtime/update evidence |

CI must run fast unit, contract, lint/type, frontend, and guardrail jobs on every PR. Platform integration, soak, benchmark, and human experiment suites are scheduled/manual jobs with retained evidence. A skipped required test needs a linked issue, reason, owner, and expiry; it cannot silently count as green.

## 9. Git and collaboration workflow

- Use a protected `main` branch; it must always build and pass required CI. Prefer short branches named `task/T-###-short-name`.
- One task or coherent contract change per PR. Large tasks use vertically testable sub-PRs while preserving the same task ID.
- Commit format: `T-###: imperative summary`. Do not mix formatting or unrelated refactors with feature work.
- PR description includes scope, dependencies, contract/config versions, tests run, acceptance evidence, privacy/security impact, and unresolved items.
- At least one reviewer other than the author is required. Protocol, privacy, security, dataset-freeze, and ADR changes require the designated cross-reviewers in this document and human sign-off.
- Shared contract PRs merge before producer/consumer implementations. Regenerate bindings in the same PR and attach compatibility fixtures.
- Avoid merge conflicts through directory ownership, small PRs, and early interface freezes. Coordinate before modifying another owner’s files; do not duplicate code to avoid coordination.
- Rebase/update from `main` before final verification; use non-destructive conflict resolution. Squash only if repository policy chooses it consistently.
- Never commit data, model artifacts containing participant-derived parameters unless explicitly approved and sanitized, credentials, local databases, logs, or generated experiment caches.
- Tag milestone-integrated states (`M1`, `M2`, etc.) and record protocol/config/model/data versions in experiment manifests.

## 10. Human-only responsibilities

Automation may prepare materials and tooling, but humans must perform and approve:

| Activity | Lead | Required participation/sign-off |
| --- | --- | --- |
| Hardware hook/permission and clean-install validation | Kanish | Joel observes integration evidence |
| Participant recruitment, consent, briefing, and support | Akshay | All members recruit/support; guide/ethics sign-off as applicable |
| Natural team self-collection | Akshay coordinates | Joel, Manas, Akshay, Kanish each participate voluntarily |
| Feature/threshold/model interpretation | Manas | Joel security/usability sign-off; full-team review of ADR changes |
| Escalation/fail-open/update security policy | Joel | Akshay and guide/team sign-off |
| Initial application-category curation | Manas | Akshay reviews cohort relevance |
| Informed-impostor and live hijack experiments | Akshay schedules | All members participate in counterbalanced roles; Manas owns analysis |
| Real-hardware runtime/subjective impact | Kanish | All participating machine owners |
| Results framing, limitations, report, and demo | Joel coordinates | All members and academic guide |

Consent records are not source-controlled. No agent contacts participants, grants consent, executes a physical takeover, or interprets academic claims without human review.

## 11. Coding and engineering standards

- Follow P1–P10 and ADR-001–ADR-012; changes use Plan §20 evidence → decision → update workflow.
- C++17: RAII, deterministic cleanup, bounded allocations in event paths, no blocking I/O in hook callbacks, `clang-format`/`clang-tidy`, explicit units/types for time and sequence.
- Python 3.11+: typed public boundaries, Pydantic v2 validation, focused modules, async cancellation/timeouts handled, `ruff`, `black`, `mypy`, pytest/hypothesis. No notebook-only production logic.
- TypeScript/React: strict TypeScript, accessible semantic components, API types derived from contracts, explicit loading/offline/stale/error states, Vitest/RTL.
- Validate every external boundary: OS callbacks, pipe frames, configuration, database rows, model artifacts, API requests, and WebSocket messages.
- Use stable error codes, correlation IDs, structured logs, and safe messages. Never log sensitive raw input.
- Configuration includes units, allowed ranges, and version. Experiment outputs record the exact configuration checksum.
- Keep modules cohesive and dependencies directed toward contracts/shared libraries; do not create circular imports or parallel feature/schema definitions.
- Maintain deterministic seeds/splits for reproducibility; label nondeterminism and record environment/hardware.
- Do not optimize without measurement. Report latency percentiles and bounded memory, not only averages.
- No core TODO/pass/mock implementation may be merged. Temporary scaffolding must be isolated, clearly labelled, and removed before its task is accepted.

## 12. Definition of Done

A task is done only when all applicable items are true:

- Implementation, migrations/codegen/configuration, error paths, telemetry, documentation, and cleanup are complete in the planned folders.
- Dependencies and versioned interfaces are satisfied; producer/consumer contract tests pass.
- Unit, property, integration, guardrail, and affected full-suite tests pass; manual/hardware evidence is attached where required.
- Privacy, security, state, failure, retention, and provenance behavior matches the plan.
- Acceptance criteria are demonstrated with observable output, not merely asserted.
- No participant data, credentials, logs, databases, or forbidden fields are committed.
- The owner has self-reviewed; required independent reviewers approve; CI is green.
- The change is integration-ready, does not break unrelated modules, and leaves no core placeholder.
- Open decisions remain open unless their named experiment and human review closed them, with ADR/plan/config records updated.

## 13. Workload and balance review

| Member | Estimated engineering share | Complexity profile | Balance assessment |
| --- | ---: | --- | --- |
| Joel | ~25–27% | Broad cross-cutting contracts, persistence, ingestion, security-sensitive state/risk/actions, API, integration | High coordination and policy complexity; fewer isolated UI/experiment tasks offsets central review load. |
| Manas | ~24–26% | Numerically dense feature implementation, ML/calibration, leakage-resistant experiments, context and final statistics | Substantial research and production-code responsibility; long-running evaluation load is balanced against limited platform/ops ownership. |
| Akshay | ~23–25% engineering, plus highest human-operations load | Collection tooling/ethics operations, realtime dashboard, security-sensitive update manager and experiments | Task count is moderate but update-manager complexity and participant critical path are large; dashboard polish is the first reducible portion if overloaded. |
| Kanish | ~25–28% | **All C/C++**, highest-risk timestamp/privacy/IPC path, plus Python/tooling, fault injection, packaging, profiling, clean-install reliability | Not restricted to C/C++: T-003 and much of T-019/T-020 are substantial cross-language/non-C++ responsibilities. Optional Linux work is excluded unless baseline load permits. |

The distribution is balanced by risk and effort rather than task count. No member owns only support work. Overlap is intentional only at contracts, security review, integration, and experiments. If actual velocity differs, rebalance unstarted work at weekly review while preserving: (1) all C/C++ with Kanish, (2) authoritative module ownership, (3) independent review, and (4) the priority order in Plan §17.1.

## 14. Final traceability checklist

Before declaring the project complete, verify:

- Every Plan phase 0–11, milestone M0–M12, schema/table/component, required feature block, state/action, promotion gate, anchor, robustness scenario, and required experiment maps to a task above.
- C/C++ work is entirely Kanish-owned and Kanish also delivered non-C/C++ generator/reliability/benchmark work.
- Collection began early, consent/provenance audits passed, and frozen day-disjoint data is immutable.
- Context never enters identity models; missing modalities and idle windows cannot become anomalies.
- Failures are fail-open and loud; heartbeat loss is also a tamper/security event; dashboard loss cannot change enforcement.
- Thresholds were tuned only on validation data and frozen before evaluation.
- FAR/FRR/EER, I1, I2, live hijack latency, runtime percentiles, E1/E2, per-user distributions, CIs, hardware/cohort context, and limitations are reproducible.
- Every Engineering Recommendation was either adopted with a recorded rationale or replaced through reviewed evidence; none became an undocumented requirement.

---

**Document status:** Implementation delegation baseline derived from the complete `PLAN.md` v2.0. Revise this file alongside affected contracts/ADRs when evidence changes ownership, sequencing, or interfaces.
