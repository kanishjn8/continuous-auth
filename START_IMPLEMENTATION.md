# Start Project Implementation

## Purpose

This file is a self-contained launch instruction for an agentic coding IDE. When this file is attached or supplied to an agent that has access to the project repository, the human only needs to identify themselves by team-member name.

Accepted team members:

- Joel
- Manas
- Akshay
- Kanish

Example user message:

```text
I am Joel. Start implementation.
```

The agent must then follow every instruction below. It must not ask the user to restate their assigned tasks or provide a separate implementation prompt.

---

# Instructions to the coding agent

You are implementing the capstone project **Continuous Authentication Using Behavioral Biometrics** for a four-person team. The user will state one of these names: Joel, Manas, Akshay, or Kanish.

Treat that name as the active developer identity. Determine their assigned work from this document, inspect the repository, and begin implementation immediately.

## 1. Required startup behavior

Before modifying code:

1. Locate and read the repository’s `PLAN.md` completely from beginning to end.
2. Locate and read `TASK_DELEGATION.md` completely if it is present.
3. Locate and follow all applicable `AGENTS.md` or equivalent repository instruction files.
4. Inspect the complete repository structure, current implementation, tests, configuration, protocol versions, Git status, and recent relevant history.
5. Determine which assigned tasks are already complete, partially implemented, ready, or blocked.
6. Check whether existing uncommitted changes belong to the user. Preserve them and avoid overwriting unrelated work.
7. Compare the repository against the task dependencies and acceptance criteria below.
8. Select the earliest high-priority assigned task whose dependencies are satisfied.
9. State briefly which task is starting, why it is ready, and which files are expected to change.
10. Proceed with implementation in the same response. Do not stop after presenting a plan unless a genuine blocker prevents safe work.

If `PLAN.md` is missing, stop and request it because the architecture and requirements cannot safely be reconstructed from this launcher alone. If `TASK_DELEGATION.md` is missing, this launcher’s ownership map remains sufficient to start, but `PLAN.md` is still mandatory.

## 2. Source-of-truth order

Use this precedence order:

1. Explicit instructions in the user’s current message.
2. Applicable repository `AGENTS.md` instructions.
3. `PLAN.md` architectural principles, ADRs, requirements, interfaces, experiments, and scope.
4. `TASK_DELEGATION.md` ownership, dependencies, interfaces, tests, and acceptance criteria.
5. This implementation launcher.
6. Existing implementation conventions where they do not conflict with the documents above.

If two authoritative documents conflict, do not silently choose one. Identify the precise conflict, avoid the affected implementation, continue independent work where possible, and request a human decision.

## 3. Project architecture that must be preserved

The baseline pipeline is:

```text
OS input
  -> native C/C++ collector
  -> framed local IPC
  -> Python ingestion
  -> shared feature extraction
  -> independent per-user keyboard and mouse anomaly models
  -> calibrated modality scores
  -> availability-weighted fusion
  -> context confidence
  -> temporal smoothing
  -> state-gated graded decision
  -> persistence, API/WebSocket, and dashboard
  -> security-gated model update workflow
```

Repository boundaries:

```text
protocol/     Shared schemas, generated bindings, API contracts, protocol version
collector/    All native C/C++ collection, timing, context, buffering, heartbeat, IPC
backend/      Ingestion, storage, scoring orchestration, risk, decisions, updates, API/WS
ml/           Shared features, training, calibration, baselines, evaluation, experiments
dashboard/    React/TypeScript monitoring interface
tools/        Synthetic streams, guardrails, fault injection, benchmarks
config/       Every tunable threshold, weight, cadence, and window parameter
data/         Local participant data; always ignored and never committed
docs/         ADR, protocol, pilot, experiment, architecture, and operating records
```

`protocol/` is authoritative for shared interfaces. Shared feature extraction must have one implementation used by both training and live inference. Tunable values belong in `config/`, not source code.

## 4. Active developer task routing

Select only the section matching the name provided by the user. Follow tasks in dependency and priority order rather than blindly following numerical order. The detailed requirements and acceptance criteria in `PLAN.md` and `TASK_DELEGATION.md` remain binding.

### If the user is Joel

Joel owns backend contracts, ingestion, persistence, risk decisions, API orchestration, and delivery coordination.

Assigned implementation tasks:

1. **T-002 — Contracts, repository, CI, and guardrails**
   - Establish the planned repository structure and component skeletons.
   - Define versioned event, feature-window, score, risk-decision, REST, and WebSocket contracts in `protocol/`.
   - Generate C++ and Pydantic bindings from the shared source.
   - Configure builds, formatting, typing, tests, CI, Git ignores, and all architectural/privacy guardrails from `PLAN.md`.

2. **T-007 — Ingestion, ordering, sessions, and segments**
   - Decode incremental length-prefixed frames.
   - Validate schema and version without re-timestamping events.
   - Detect gaps, duplicates, malformed frames, and ordering problems.
   - Maintain a bounded in-memory raw-event ring.
   - Attribute events to authenticated sessions and idle-split segments.

3. **T-009 — Persistence, audit, retention, and configuration**
   - Implement the complete SQLite/WAL schema from `PLAN.md`.
   - Add migrations, transactions, constraints, indexes, and schema versions.
   - Implement append-only JSONL auditing, rotation, compression, and retention.
   - Validate configuration and surface disk, permission, migration, and integrity failures loudly.

4. **T-013 — Risk engine, state machine, and decision layer**
   - Implement availability-renormalized score fusion.
   - Implement EWMA plus K-of-N smoothing, hysteresis, cooldown, and action budgets.
   - Ensure `INSUFFICIENT_DATA` holds risk state and breach counters.
   - Implement all specified user states and graded actions.
   - Enforce shadow mode and fail-open-with-loud-alerting behavior.
   - Persist a complete, replayable input trace for every decision.

5. **T-014 — Enforcement adapters and verification anchors**
   - Provide adapters for continue, soft challenge, reauthentication, and termination.
   - Record valid A1–A3 independent verification anchors.
   - Never accept continuous low risk alone as an anchor.
   - Ensure unavailable enforcement infrastructure cannot accidentally lock out users.

6. **T-016 — REST API and backend orchestration**
   - Implement authenticated state, profile, history, alert, metric, health, and administrative endpoints.
   - Maintain OpenAPI conformance, pagination, safe errors, and correlation IDs.
   - Coordinate the WebSocket contract with Akshay’s implementation.

7. **T-019 — Full-system integration, co-owned with Kanish**
   - Assemble lifecycle and recovery across all components.
   - Verify correct multi-user profile selection and absence of cross-contamination.
   - Consolidate configuration and support fault injection, soak tests, and clean installation.

8. **T-020 — Joel’s final-evaluation portion**
   - Freeze and checksum risk thresholds before evaluation.
   - Produce decision-level robustness and state-transition evidence.

9. **T-021 — Finalisation coordination**
   - Coordinate deployment instructions, diagrams, documentation, reproducibility, and demo readiness.

Mandatory reviews: collector privacy and IPC with Kanish; feature/score boundaries with Manas; update-manager security and storage with Akshay.

### If the user is Manas

Manas owns the shared feature pipeline, machine learning, calibration, evaluation, context confidence, and statistical reporting.

Assigned implementation tasks:

1. **T-008 — Shared windowing and feature extraction**
   - Implement configurable 30-second-or-100-keystroke window closing.
   - Implement every keyboard and mouse feature listed in `PLAN.md`.
   - Implement `FULL`, `KBD_ONLY`, `MOUSE_ONLY`, and `INSUFFICIENT_DATA` quality gates.
   - Normalize mouse features for device resolution/DPI.
   - Keep context completely outside identity-model feature arrays.
   - Guarantee deterministic, finite, content-free vectors through one shared implementation.

2. **T-010 — Per-user training, calibration, and artifacts**
   - Train independent keyboard and mouse Isolation Forest models for each user.
   - Add per-user preprocessing and percentile calibration.
   - Implement the required statistical baseline and alternative one-class model.
   - Version artifacts with user, modality, schema, provenance, hyperparameters, calibration, metrics, and checksums.
   - Refuse corrupt or schema-incompatible artifacts.

3. **T-011 — Day-disjoint evaluation and required ML experiments**
   - Implement day-disjoint and leave-one-day-out splits.
   - Compute window-level FAR, FRR, EER, ROC/DET, and per-user distributions.
   - Implement zero-effort impostor cross-evaluation.
   - Run modality/fusion ablation, model baseline comparison, enrollment-length experiment, and feature reduction.
   - Prevent leakage and record configurations, data versions, code versions, and exclusions.

4. **T-012 — Context confidence layer**
   - Implement bootstrap application categories and neutral `UNKNOWN` handling.
   - Derive per-user/per-category confidence from genuine-score variance where data is sufficient.
   - Implement fallback arbitration and a hard nonzero confidence floor.
   - Measure whether context reduces FRR without increasing FAR.

5. **T-020 — Final evaluation lead**
   - Lead fixed-split accuracy metrics, per-user statistics, bootstrap confidence intervals, figures, and reproducible reporting.
   - Integrate the team’s informed-impostor, live-hijack, runtime, update-manager, and robustness evidence.
   - Ensure every claim includes appropriate cohort, volume, hardware, and limitations context.

6. **T-021 — ML and evaluation documentation**
   - Finalize feature, model, experiment, statistical, and reproducibility documentation.

Mandatory reviews: production score/risk boundary with Joel; collection provenance and dataset freeze with Akshay; collector timestamp/feature fidelity with Kanish.

### If the user is Akshay

Akshay owns ethical collection tooling and operations, the realtime dashboard, WebSocket stream, and the Model Update Manager.

Assigned implementation tasks:

1. **T-006 — Collection programme and health tooling**
   - Implement the public free-text dataset adapter and clearly label its limitations.
   - Prepare pause, enrollment, consent-eligibility, installation-support, and provenance workflows.
   - Track per-participant days, windows, modality balance, quality labels, gaps, and device changes.
   - Support scheduled A3 verification anchors during collection.
   - Ensure missing consent or provenance prevents evaluation use.

2. **T-015 — Collection analytics and evaluation freeze**
   - Implement coverage reporting and shortfall alerts.
   - Create immutable, checksum-versioned day-disjoint train, validation, and evaluation manifests.
   - Prevent post-freeze mutation and dataset leakage.

3. **T-016 — WebSocket streaming and resynchronization**
   - Implement versioned risk, alert, state, and health event envelopes.
   - Add monotonically increasing stream sequences, bounded clients, reconnect cursors, snapshots, and state resynchronization.
   - Ensure WebSocket or dashboard failure cannot affect enforcement.

4. **T-017 — Authenticated real-time dashboard**
   - Implement Overview, Live, Alerts, History, System Health, and Profile views.
   - Make behavioral and availability alerts unmistakably different.
   - Implement authentication, reconnect/stale/offline behavior, enrollment progress, update history, and clearly labelled replay mode.

5. **T-018 — Model Update Manager, quarantine, and rollback**
   - Implement promotion gates G1–G6 at segment granularity.
   - Accept only A1–A3 verification anchors and reject A4 low risk alone.
   - Implement quarantine ageing, incident invalidation, fixed-cadence retraining, and full auditing.
   - Validate candidate models against held-out genuine and impostor data before atomic activation.
   - Implement retained versions, safe failure behavior, and one-step rollback.
   - Implement drift-benefit E1 and poisoning-resistance E2 experiments.

6. **T-020 — Akshay’s final-evaluation portion**
   - Coordinate informed-impostor and live-hijack experiment operations.
   - Execute and document Model Update Manager experiments E1 and E2.

7. **T-021 — Collection, dashboard, and update documentation**
   - Finalize operator, pilot, monitoring, and update-manager documentation.

Human-only work remains human: recruitment, consent, participant briefing, physical installation support, mimicry, and live takeover cannot be delegated to the coding agent.

Mandatory reviews: update security/storage with Joel; retraining validation with Manas; collector health telemetry with Kanish.

### If the user is Kanish

Kanish owns **all C and C++ work**, plus substantial non-C/C++ reliability, synthetic-data, integration, packaging, and runtime evaluation work.

Assigned implementation tasks:

1. **T-001 — Target-platform feasibility spike**
   - Demonstrate Windows system-wide keyboard/mouse capture across focus changes.
   - Validate the monotonic clock, permissions, shutdown, and baseline overhead.
   - Record evidence for ADR-001 without persisting key identity.

2. **T-003 — Synthetic event and score generator**
   - Implement deterministic seeded streams for keyboard classes, mouse activity, context, heartbeat, modality sparsity, malformed frames, gaps, duplicates, anomalies, and takeovers.
   - Provide known timing ground truth and expected downstream assertions.
   - Ensure synthetic provenance can never be mistaken for evaluation evidence.

3. **T-004 — Native capture, timing, and content-free classification**
   - Implement the C++17 hook abstraction and Windows backend.
   - Assign `QueryPerformanceCounter` timestamps inside callbacks.
   - Implement sequence allocation and the complete key-class taxonomy.
   - Discard raw keycodes immediately and perform no blocking callback I/O.

4. **T-005 — Native context, devices, buffer, heartbeat, and IPC**
   - Resolve foreground process only—never title, path, URL, or content.
   - Capture application ID/category, device class, screen resolution, and DPI.
   - Implement bounded buffering, overload policy, heartbeat, and health counters.
   - Implement length-prefixed Windows named-pipe transport behind a swappable abstraction.
   - Benchmark throughput before considering shared memory.

5. **T-019 — Integration, fault injection, packaging, and reliability, co-owned with Joel**
   - Implement cross-component lifecycle and recovery tooling.
   - Build the complete fault-injection matrix from `PLAN.md`.
   - Run multi-hour/multi-day stability checks for memory, clocks, buffers, database growth, and log rotation.
   - Produce a reproducible clean-machine Windows installation package and instructions.

6. **T-020 — Runtime-cost evaluation**
   - Measure collector/backend CPU, memory, throughput, dropped events, and p50/p95/p99 processing latencies on documented real hardware.
   - Preserve raw benchmark evidence and reproducible configurations.

7. **T-021 — Native, deployment, and reliability documentation**
   - Finalize collector build, IPC, installation, profiling, and reliability documentation.

Linux/X11 support is optional and must not begin until the Windows baseline and required milestones are secure.

Mandatory reviews: C++/Python contract with Joel; timing/feature fidelity with Manas; health telemetry and dashboard interpretation with Akshay.

## 5. Universal architectural and privacy rules

These rules are non-negotiable:

1. Capture input OS-wide, not through application plugins, browser extensions, accessibility trees, widgets, or DOMs.
2. Never capture, transmit, persist, log, or expose typed characters, raw keycodes, ordered reconstructable text, window titles, document names, file paths, URLs, clipboard contents, screenshots, or screen contents.
3. Raw keycodes may exist only transiently inside the native hook callback for immediate class conversion.
4. Never use time-of-day or day-of-week as an authentication signal.
5. Maintain one independent profile per enrolled user. Never train a user’s model on another user’s data.
6. Application context is a confidence modifier only. It must never enter identity-model features or select an application-specific model.
7. No application, state, or context may disable monitoring or reduce effective confidence to zero.
8. Missing modalities are availability conditions, not anomalous feature values.
9. `INSUFFICIENT_DATA` produces no score and holds risk state; it cannot escalate or decay risk.
10. No single anomalous window may trigger punitive enforcement.
11. Infrastructure failures fail open for the user while raising a distinct HIGH availability alert and entering `DEGRADED`.
12. Collector heartbeat loss is also a tamper/security event, but does not force termination by default.
13. Training updates require the complete promotion gate, quarantine, independent verification, fixed schedule, held-out regression validation, versioning, and rollback.
14. Participant data, local databases, logs, credentials, and sensitive artifacts must never be committed.
15. Model artifacts with incompatible feature-schema versions must be refused, never silently loaded.

## 6. Open decisions and engineering recommendations

Values and choices marked `[OPEN]` in `PLAN.md` must be resolved only by their designated experiment and human review. This includes dataset selection, enrollment thresholds, quality gates, final feature set, fusion strategy and weights, risk/smoothing parameters, update cadence/quarantine, context retention, model alternatives, secondary platform support, and joint interaction features.

When an unspecified implementation detail is necessary:

1. Prefer a small reversible choice consistent with the existing architecture.
2. Label it **Engineering Recommendation** in documentation or the implementation report.
3. Explain the reason, trade-off, and how it can be changed.
4. Do not use this mechanism to decide architecture, privacy, security, evaluation methodology, or an ADR.

## 7. Dependency and readiness rules

Do not implement against an invented interface merely because another task is unfinished.

- If a shared contract is missing, implement or propose it in `protocol/` first if the active developer owns T-002; otherwise use an existing approved fixture or report the dependency.
- Synthetic fixtures may unblock downstream development before live collection.
- Public or synthetic data may validate mechanics but cannot produce headline project results.
- UI work may use versioned C7/C8 fixtures before the backend is ready.
- ML pipeline work may use synthetic/public data for mechanics; reported results require approved self-collected/pilot data and day-disjoint partitions.
- Context confidence requires sufficient category data and a functioning risk engine.
- The Model Update Manager requires verification-anchor evidence and multi-week data; its logic can be built against synthetic gate fixtures earlier.
- Final evaluation requires an immutable dataset freeze, fixed thresholds, and an integrated system.
- Optional/stretch work cannot displace Windows collection correctness, multi-day data collection, feature/model/risk baselines, integration, or mandatory experiments.

When an assigned task is blocked, continue with another dependency-ready assigned task or test fixture. Ask the user only when no meaningful in-scope progress remains or a human decision is required.

## 8. Implementation loop

For each task or testable subtask:

1. Confirm its existing state and dependencies.
2. Read the relevant `PLAN.md` sections and shared contracts again.
3. Define or update acceptance tests before critical-path implementation where practical.
4. Implement the smallest complete vertical slice.
5. Add validation, error behavior, telemetry, configuration, migrations/codegen, and documentation as applicable.
6. Run focused tests during implementation.
7. Run the affected component suite, contract tests, guardrails, linting, and type checks.
8. Perform safe integration checks with adjacent modules.
9. Compare observable behavior against acceptance criteria.
10. Summarize files changed, tests run, acceptance evidence, remaining work, and any Engineering Recommendation.
11. Continue to the next dependency-ready portion unless user input, external human activity, or an architectural decision is genuinely required.

Do not claim an entire task is complete when only scaffolding, mocks, happy-path code, or unit tests exist.

## 9. Testing expectations

Every change must include proportionate tests:

- C++: clock, classification, buffer/drop, heartbeat, IPC framing, reconnect, cleanup, resource bounds, and cross-language fixtures.
- Ingestion/storage: partial/malformed frames, ordering/gaps/duplicates, session/segment boundaries, migrations, WAL/concurrency, retention, disk/permission failures, and bounded memory.
- Features: formula tests, property/range tests, empty/sparse/missing-pair inputs, determinism, DPI normalization, no NaN/inf, and training/inference parity.
- Models/evaluation: per-user isolation, calibration bounds, artifact mismatch/corruption, day leakage, known metric fixtures, deterministic splits, and missing modalities.
- Risk/decisions: every robustness scenario from `PLAN.md`, state-machine properties, one-window non-enforcement, sustained escalation, hold behavior, cooldown, fail-open, and replay determinism.
- Updates: every G1–G6 gate, A1–A4 behavior, quarantine, incident invalidation, schedule, regression rejection, atomic activation failure, rollback, and bypass guardrail.
- API/WebSocket/dashboard: contract conformance, authentication, safe errors, pagination, event order, reconnect/resync, backpressure, stale/offline state, accessibility, and alert distinction.
- Integration: full pipeline, multi-user selection, component restarts, collector kill, model/storage failure, malformed events, dashboard disconnect, device change, fault injection, soak, and clean installation.

Skipped required tests must be reported with a reason, owner, and concrete follow-up; they do not count as passing.

## 10. Git and collaboration behavior

- Preserve unrelated and pre-existing user changes.
- Use a focused branch such as `task/T-###-short-name` when authorized and appropriate.
- Keep each change focused on one task or coherent testable portion.
- Include the task ID in commits and pull requests when creating them is requested or part of the established workflow.
- Do not commit, push, open a pull request, install unapproved dependencies, or perform destructive Git operations unless the user’s request and environment authorize it.
- Shared-interface changes require producer and consumer tests and review.
- Do not modify another owner’s module merely for convenience. Coordinate through contracts and focused cross-boundary changes.
- Never discard or overwrite changes you did not create.

## 11. Human-only blockers

The coding agent must prepare tooling and instructions but cannot substitute itself for:

- Real-hardware permission and hook validation.
- Participant recruitment, informed consent, and briefing.
- Natural-use behavioral data collection.
- Installation support on participant-owned machines.
- Informed-impostor mimicry and physical live-session takeover.
- Subjective user-experience assessment.
- Approval of ADR, privacy, escalation, failure, update-security, dataset-freeze, or academic claims.
- Interpretation and framing of final research results.

When reaching one of these gates, provide a concise human procedure, required evidence, and the independent software work that can continue meanwhile.

## 12. Definition of Done

A task is complete only when:

- Required functionality exists in the intended repository structure.
- No core placeholder, pass-through, fake response, or unresolved core TODO remains.
- Interfaces and generated bindings are consistent and versioned.
- Inputs are validated and expected failures behave as specified.
- Configuration is externalized and validated.
- Relevant telemetry and audit evidence exist.
- Unit, property, contract, integration, guardrail, lint, and type checks pass as applicable.
- Privacy, security, provenance, and retention invariants pass.
- Acceptance criteria from `PLAN.md` and `TASK_DELEGATION.md` are demonstrably satisfied.
- Required cross-owner review points are identified.
- Documentation is updated and another developer can continue the work.

## 13. Required progress reporting

Keep progress reports concise and evidence-based. At the start report:

```text
Active developer: <name>
Assigned task selected: <task ID and name>
Repository status: <not started / partial / ready / blocked>
Satisfied dependencies: <list>
Files expected to change: <list>
Starting implementation now.
```

At each meaningful completion report:

```text
Completed: <task or subtask>
Implemented: <observable behavior>
Tests: <commands/suites and result>
Acceptance evidence: <specific result>
Remaining assigned work: <next dependency-ready item>
Blockers or Engineering Recommendations: <only if applicable>
```

Do not repeatedly ask whether to continue. Continue through safe, dependency-ready assigned work until the requested implementation is complete or a genuine blocker requires human input.

---

# Launch command

After receiving this file, the user only needs to send:

```text
I am <Joel | Manas | Akshay | Kanish>. Start implementation.
```

On receiving that message, execute the startup behavior and begin the first dependency-ready assigned task immediately.
