# Single-User Enrollment and Unseen-Attacker Drill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the implementation match the team's single-participant enrollment + unseen-live-attacker architecture — activation without an impostor cohort, correct calibration and recovery behaviour, and a structurally enforced one-way data flow in which attacker behaviour can be scored but can never be trained on.

**Architecture:** Five stages that are currently fused get separated: per-user training, per-user calibration/validation, cross-participant FAR evaluation, live attacker drill, and post-activation updates. FAR moves from being an *input* to activation to being an *output* of the drill. Attacker sessions get a first-class, session-grained representation (one new table, no protocol change) that every corpus loader honours.

**Tech Stack:** Python 3.11+, Pydantic v2, scikit-learn, NumPy, SQLite (WAL), pytest, FastAPI. No protocol codegen changes. No C++ changes.

**Spec:** `status.md` (read-only audit, 2026-09-09) plus the ten accepted decisions recorded in the audit acceptance. `PLAN.md` §5.3, §10.1, §10.5, §12, §13.2, §13.3 and ADR-013 remain authoritative for everything this plan does not explicitly change.

## Global Constraints

- `ml/training/gate.py::require_promotion_gate` is **not modified**. Not one line.
- G1–G6 semantics, `quarantine_days: 7`, `retraining_cadence_days: 7`, `regression_tolerance: 0.02`, `min_promotable_windows: 10` are **unchanged**.
- No file under `protocol/` is edited and `python protocol/codegen/generate.py --check` must keep passing untouched.
- No change to `feature_windows`, `scores`, `sessions`, `segments`, `users`, `model_profiles`, or any existing table. Exactly one new table is added.
- FAR is never fabricated. `0.0` is never written for an unmeasured FAR.
- FRR remains a genuinely measured number in every code path that reports one.
- `python tools/guardrails/check.py`, `python -m pytest`, and `python -m mypy backend/app ml tools` pass before every commit.
- The attacker is never a second enrolled participant, never has a consent or enrollment record, and never gets a `user_id` of their own.
- Every threshold introduced lives under `config/`. None is hardcoded.

---

# PART I — DESIGN

## 1. The exact desired lifecycle

```
┌── STAGE 1 ── COLLECTION ─────────────────────────────────────────────────┐
│ backend.app.runtime.cli --participant-id manas-01 (+ collector)          │
│ writes feature_windows(user_id=manas-01, provenance=PILOT)               │
│ state: ENROLLING · no model · no enforcement · risk fail-open            │
│ INVARIANT: only the legitimate user operates the machine in this stage   │
└──────────────────────────────────────────────────────────────────────────┘
                                   ↓
┌── STAGE 2 ── FREEZE ─────────────────────────────────────────────────────┐
│ tools.collection freeze --version pilot-v1                              │
│ write-once 0o400 manifest; per-participant day-disjoint TRAIN/VAL/EVAL   │
│ excludes: ineligible provenance, missing consent, pre-enrollment days,   │
│           AND (new) every window belonging to a declared drill session   │
│ INVARIANT: the manifest is the sole definition of trainable data         │
└──────────────────────────────────────────────────────────────────────────┘
                                   ↓
┌── STAGE 3 ── TRAIN (single-user) ────────────────────────────────────────┐
│ tools.enrollment activate → ml.training.isolation_forest.train_user_...  │
│ TRAIN partition only · one user_id · ADR-013 enrollment admission gate   │
│ INVARIANT: train_one_class_model raises on >1 distinct user_id           │
└──────────────────────────────────────────────────────────────────────────┘
                                   ↓
┌── STAGE 4 ── CALIBRATE (single-user) ────────────────────────────────────┐
│ PercentileCalibrator.fit on the user's own TRAIN raw scores              │
│ FRR measured on the held-out VALIDATION partition, at the deployed       │
│   operating point (risk.medium_threshold on the calibrated risk scale)   │
│ FAR: measured only if another participant exists; otherwise UNMEASURED   │
│ INVARIANT: the EVALUATION partition is never read here                   │
└──────────────────────────────────────────────────────────────────────────┘
                                   ↓
┌── STAGE 5 ── ACTIVATE ───────────────────────────────────────────────────┐
│ SQLiteUpdateRepository.activate_profile → model_profiles(status=ACTIVE)  │
│ ValidationReport: accepted=True, FRR real, FAR None,                     │
│   code=ENROLLMENT_INITIAL_PROFILE_NO_IMPOSTOR_COHORT                     │
│ INVARIANT: activation asserts "a model exists and loads", nothing more   │
└──────────────────────────────────────────────────────────────────────────┘
                                   ↓
┌── STAGE 6 ── SHADOW / CALIBRATION PERIOD ────────────────────────────────┐
│ ENROLLING → CALIBRATING → (only after N post-activation scored windows)  │
│   → ACTIVE.  Decisions computed and logged; enforcement suppressed.      │
│ operator reviews the live score distribution before enabling enforcement │
│ INVARIANT: pre-activation UNAVAILABLE scores cannot satisfy this         │
└──────────────────────────────────────────────────────────────────────────┘
                                   ↓
┌── STAGE 7 ── LIVE OPERATION ─────────────────────────────────────────────┐
│ state ACTIVE · enforcement.enabled=true                                  │
│ CONTINUE → SOFT_CHALLENGE → REAUTH → TERMINATE, with cooldown + budget   │
│ INVARIANT: a transient failure degrades and then RECOVERS; DEGRADED is   │
│            never permanent                                               │
└──────────────────────────────────────────────────────────────────────────┘
                                   ↓
┌── STAGE 8 ── LIVE ATTACKER DRILL ────────────────────────────────────────┐
│ backend.app.runtime.cli --participant-id manas-01 --drill-label drill-01 │
│ session recorded in drill_sessions; classmate operates the machine       │
│ windows scored against manas-01's ACTIVE profile; risk trace captured    │
│ SUPPRESSED for the whole session: A1 anchor, update-candidate submission,│
│   context-confidence learning, enrollment/calibration progress counters  │
│ INVARIANT: drill windows are excluded from every corpus loader forever   │
└──────────────────────────────────────────────────────────────────────────┘
                                   ↓
┌── STAGE 9 ── OPTIONAL EVALUATION ────────────────────────────────────────┐
│ drill risk traces + detection latency  = the project's impostor evidence │
│ ml.evaluation.* cross-participant I1   = only if a cohort ever exists    │
│ INVARIANT: evaluation reads; it never activates, deactivates, or trains  │
└──────────────────────────────────────────────────────────────────────────┘
                                   ↓
┌── STAGE 10 ── OPTIONAL MODEL UPDATES ────────────────────────────────────┐
│ DISABLED for this project. tools.updates run fails fast and loudly with  │
│ UPDATE_REQUIRES_IMPOSTOR_COHORT. G1-G6 and quarantine are NOT weakened.  │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Change specification by area

### 2.1 Activation / validation — **REQUIRED**

`tools/enrollment/activate.py::_measure_validation_metrics` is rewritten around three facts:

1. **Genuine scores are mandatory.** If the participant's own VALIDATION partition produces no scorable window, activation must still fail — that is a real data problem, not a cohort problem. New error `ENROLLMENT_VALIDATION_NO_GENUINE`.
2. **Impostor scores are optional.** Their absence yields `false_acceptance_rate = None`, not an exception.
3. **FRR needs an operating point, and without impostors there is no EER.** The replacement operating point is the one the deployed system actually uses: `risk_settings.risk.medium_threshold` on the calibrated risk scale. Because `backend/app/models/service.py` computes `calibrated_risk = 1 - percentile/100`, the equivalent percentile threshold is `(1 - medium_threshold) * 100` — with the shipped `0.45`, that is percentile `55`. FRR is then the fraction of the user's own held-out VALIDATION windows that the live risk engine would place at or above MEDIUM.

That number is more useful than an EER-point FRR, because it answers the question the operator actually has before a drill: *"how often will my own normal behaviour trip the first rung of the ladder?"*

When a second participant **does** exist in the frozen corpus, the existing pooled-EER path is kept unchanged and FAR is measured as it is today. The two paths differ only in whether impostor scores were found.

### 2.2 `ValidationMetrics` / protocol contracts — **REQUIRED**

Verified blast radius (grepped, not assumed):

| Consumer | Change needed |
| --- | --- |
| `protocol/schemas/*`, `protocol/generated/*` | **None.** `ValidationMetrics`/`ValidationReport` appear nowhere in `protocol/`. Confirmed by `grep -rn "false_acceptance\|ValidationMetrics" protocol/`. |
| `backend/app/storage/migrations/*` | **None.** `model_profiles.validation_json` is `TEXT NOT NULL CHECK(json_valid(...))` — `null` is valid JSON. |
| `backend/app/updates/manager.py` | `false_acceptance_rate: float \| None`; `__post_init__` skips the range check when `None`; `ValidationReport` gains `operating_point: str`; `_validate` rejects when either FAR is `None`. |
| `backend/app/updates/repository.py::_profile_from_row` | `ValidationMetrics(**data)` already round-trips `None`; add `operating_point=validation_data.get("operating_point", "")` so pre-existing rows still load. |
| `backend/app/api/*` | **None.** Only `profile_version` is read (`backend/app/api/backend.py:137`). |
| `dashboard/src/*` | **None.** No consumer of FAR/FRR exists. |
| `tools/enrollment/activate.py` | Rewritten (2.1). |
| `tools/updates/run.py` | Fails fast (2.8). |
| `tools/experiments/drift.py` | Constructs `ValidationMetrics`/`ValidationReport`; add the new field. |
| `tools/demo/bootstrap_first_model.py` | Replace the fabricated `ValidationMetrics(0.0, 0.0)` with `(None, None)`-shaped honesty; fix the stale provenance docstring. |
| Tests | `backend/tests/test_update_manager.py`, `tools/enrollment/tests/test_activate.py`, `tools/updates/tests/test_run.py`, `tools/experiments/tests/test_drivers.py` |

**Reason codes**

| Code | Meaning |
| --- | --- |
| `ENROLLMENT_INITIAL_PROFILE_NO_IMPOSTOR_COHORT` | *(new)* First profile. FRR measured at the policy operating point. FAR unmeasured — the frozen corpus contains no other participant. No prior profile to regress against. |
| `ENROLLMENT_INITIAL_PROFILE_NO_BASELINE` | *(kept, unchanged)* First profile in a corpus that **does** contain other participants: FRR and FAR both measured, still no prior profile to regress against. |
| `VALIDATION_FAR_UNMEASURED` | *(new)* A scheduled **update** could not be certified because FAR is unmeasured on either side. Always `accepted=False`. |

`operating_point` is a short free-form audit string, e.g.
`"frr@calibrated_risk>=0.450 (risk.medium_threshold); far=unmeasured (no impostor cohort in manifest)"`.
It is serialised into the existing JSON column and requires no migration.

**Why `accepted=True` is honest at enrollment with `far=None`:** `accepted` gates *activation*, and a first profile's acceptance criterion is the ADR-013 enrollment gate (consent, corpus integrity, volume, day coverage), not a regression check. There is nothing to regress against, and the code says so. `accepted` for an *update* means something different — "this candidate is not worse than the active profile" — which is exactly why the update path must refuse an unmeasured FAR.

### 2.3 Enrollment distinct-day configuration — **REQUIRED**

The current defect is **scope conflation**, not a value that is too high. One config key is being used for two unrelated policies:

| Policy | Question it answers | Correct home | Correct scope |
| --- | --- | --- | --- |
| Live state-machine enrollment | "How much observed evidence before the runtime stops treating this user as enrolling?" | `config/risk.*.yaml → enrollment.min_windows / min_distinct_days` | **All** of the user's `feature_windows`, counted live |
| First-profile training admission | "How much of the frozen TRAIN partition must a first model be built from?" | *(new)* `config/ml.*.yaml → enrollment.min_train_windows / min_train_distinct_days` | **TRAIN partition only** |

`tools/enrollment/activate.py` currently feeds the first into the second. That is why a 60/20/20 split silently turns a "3 distinct days" rule into a "6 collection days" requirement that appears nowhere in any document.

**The fix is to separate them, not to lower a number to fit the data.** After separation:
- `risk.enrollment.min_windows = 20`, `min_distinct_days = 3` — **unchanged**, still governing `ENROLLING → CALIBRATING` against the live corpus-wide counts (currently 1221 windows across 4 days: satisfied).
- `ml.enrollment.min_train_windows` / `min_train_distinct_days` — **new**, explicitly TRAIN-scoped, and requiring an explicit policy statement.

**The policy statement for `min_train_distinct_days`:** *"A first profile must be trained on at least this many distinct calendar days, so that a single day's posture, device position, or mood cannot alone define the identity baseline."* The smallest value at which that sentence is true is **2** — one day means no cross-day variation is represented at all, which is precisely the weakness `config/risk.demo-live-single-day.yaml` warns about. Setting it to 3 or higher is not wrong, but nothing in the repository justifies a specific higher number: `PLAN.md` §10.5 says this must be answered by the enrollment-length experiment (O3), which has not run. Therefore:

- Set `min_train_distinct_days: 2` and `min_train_windows: 20`.
- Record it with a `config_version` that says it is provisional and pending O3.
- State in the config comment, in one line, that with the current 60/20/20 freeze this means a 4-day round is admissible and a 3-day round is not.

That the current 4-day corpus happens to satisfy it is an **outcome** of the policy, not its purpose. Anyone reviewing this must be able to read the policy sentence alone and agree with it without knowing how much data exists.

**Where it lives.** `MLConfig` (`ml/features/config.py`) is a validated-then-raw dict loader with no `extra="forbid"`, so a new top-level `enrollment:` section costs one `_require_positive` call and no protocol change. `config/risk.*.yaml` cannot host it honestly anyway: `_RiskRuntimeConfig` requires `development_only: Literal[True]`, so a `config/risk.pilot.yaml` would have to lie — the same reason `tools/enrollment/activate.py` refuses to default `--risk-config`.

`EnrollmentAdmission.min_windows` / `.min_distinct_days` are renamed to `min_train_windows` / `min_train_distinct_days` so the field names disclose their scope. `EnrollmentAdmission` is a plain dataclass in `ml/training/enrollment.py`; the rename touches `tools/enrollment/activate.py`, `tools/experiments/drift.py`, and `ml/tests/test_enrollment_gate.py` only.

### 2.4 State recovery (DEGRADED) — **REQUIRED**

`RiskEngine.component_recovered()` (`backend/app/risk/engine.py:329`) has no production caller. Two recovery triggers are needed, both minimal:

1. **Scoring recovers.** In `RiskEngine.process`, once `_required_failure()` returns `None` *and* `_fusion()` succeeds, the inputs the engine degraded over are demonstrably back. If the state is `DEGRADED`, recover before building the decision. This is the trigger for "the model was missing and now loads."
2. **Heartbeat recovers.** In `RuntimeOrchestrator._handle_heartbeat`, when a heartbeat arrives while `self._heartbeat_failed` is `True`, clear the flag *and* call `self._risk_engine.component_recovered()`. This is the trigger for "the collector was restarted."

`UserStateMachine.recover()` raises unless the state is `DEGRADED`, so both call sites guard on that. `recover()` restores `_pre_degraded`, so a user who degraded while `ACTIVE` returns to `ACTIVE` and one who degraded while `ENROLLING` returns to `ENROLLING` — correct in both directions.

**Also required:** persist the transition. `storage.upsert_user` is currently called only from `start_authenticated_session` and `_progress_state`, so the live DB says `ENROLLING` while 613 decisions were emitted carrying `user_state=DEGRADED`. Degrade and recover must both write through, or the audit trail contradicts itself.

### 2.5 Calibration-state progression — **REQUIRED**

`_progress_state` counts `SELECT COUNT(*) FROM scores WHERE user_id = ?` — a lifetime total. The live database already holds 1221 such rows, every one of them written while no model existed, with all modalities `UNAVAILABLE`. On the first window after activation the user would jump `ENROLLING → CALIBRATING → ACTIVE` within a single window, and the shadow period required by `PLAN.md` §5.3 would never happen.

`scores.profile_version` **already exists** as a column, and pre-activation rows carry the literal string `"unavailable"` (from `ProfileArtifacts(user_id, "unavailable", "unavailable", None, None)` in `_refresh_profile`). The fix is therefore a `WHERE` clause plus a usability filter:

- Count only rows whose `profile_version` equals the currently loaded profile's version.
- Count only rows where at least one modality actually scored, so `INSUFFICIENT_DATA` windows during a lunch break cannot age the user into `ACTIVE`.

This needs no migration, no new column, and no new config key.

### 2.6 Attack-session / data separation — **REQUIRED**

**Chosen representation: one new table, keyed by `session_id`.**

```sql
-- backend/app/storage/migrations/0004_attack_drill.sql
CREATE TABLE drill_sessions (
    session_id      TEXT PRIMARY KEY REFERENCES sessions(session_id) ON DELETE CASCADE,
    drill_label     TEXT NOT NULL CHECK(length(drill_label) > 0),
    declared_at_utc TEXT NOT NULL,
    schema_version  TEXT NOT NULL
);
```

**Why session grain, and why a table:**

- A drill *is* a session — one person sits down, operates the machine, and leaves. Window grain would repeat the same fact 400 times and invite partial labelling.
- `feature_windows` maps to `FeatureWindow`, which subclasses the protocol-generated model. A column there forces `protocol/codegen` regeneration including the C++ structs, and changes `_window_digest` in `tools/collection/freeze.py`. Both are explicitly out of scope.
- `sessions.entry_auth_evidence` is a constrained anchor enum with a different meaning; overloading it would corrupt `verification_anchors` semantics and G2.
- `storage_metadata` is a flat key/value store — a list-in-a-string is neither queryable nor foreign-key protected, and a typo would silently un-label a drill.
- One table, one foreign key, zero changes to existing tables, zero protocol changes.

**How a drill is declared:** `--drill-label <text>` on `backend/app/runtime/cli.py`, threaded through `create_collection_application` into `RuntimeOrchestrator`. It is opt-in and per-process, so a normal collection run cannot accidentally be labelled and a drill run cannot accidentally be unlabelled — the operator types a different command.

**What the label suppresses, for the whole session:**

| Suppressed | Why |
| --- | --- |
| `create_segment` → `_submit_segment_candidate` | An attacker segment must never become an update candidate, even a rejected one |
| `enforcement.record_authenticated_entry` (the automatic A1 anchor) | The person at the keyboard did not authenticate; granting A1 would hand the attacker the one anchor G2 exists to withhold |
| `context_layer.observe_genuine` | See 2.7 |
| `_progress_state` | Attacker windows must not advance enrollment or calibration counters |
| Inclusion in `load_window_summaries` | Cascades to `health`, `build_freeze`, `verify_freeze`, and `load_frozen_corpus` |

**What is deliberately NOT suppressed:** scoring, risk fusion, smoothing, decisions, enforcement actions, alerts, the audit log, and the WebSocket stream. That is the entire point of the drill.

**The single exclusion point:** `tools/collection/repository.py::load_window_summaries` gains
`WHERE session_id NOT IN (SELECT session_id FROM drill_sessions)`.
Because `build_freeze`, `verify_freeze`, the health report, and `tools/collection/corpus.py::load_frozen_corpus` (via `verify_freeze`) all draw from that one loader, a single filter closes every downstream path at once. `WindowSummary` is left unchanged, so `_window_digest` and manifest checksums are untouched.

**Resulting invariant chain:**

```
attacker window
  ├─→ score  ·  risk  ·  decision  ·  enforcement  ·  alert  ·  audit      ALLOWED
  ├─✗ load_window_summaries       (filtered on drill_sessions)
  │     └─✗ build_freeze → manifest → load_frozen_corpus
  │           └─✗ require_enrollment_admission  (WINDOW_NOT_IN_FROZEN_CORPUS)
  │           └─✗ train_one_class_model  →  no model, no calibration
  │           └─✗ tools.updates.run.build_candidate (reads frozen TRAIN only)
  ├─✗ _submit_segment_candidate   (skipped for drill sessions)      no candidate
  ├─✗ observe_genuine             (learning suspended)              no baseline shift
  └─✗ _progress_state             (skipped)                         no state advance
```

Note the belt-and-braces property: even if the drill label were forgotten, `require_enrollment_admission` still refuses once a profile is active (`PROFILE_ALREADY_ACTIVE`), and `build_candidate` still reads only the already-written frozen manifest. The drill label removes the *procedural* risk — freezing again, later, after an attack — which is the only vector the audit found.

### 2.7 Context-confidence baseline — **RECOMMENDED**

Correcting one point from the audit: `ContextConfidenceLayer._statistics` and `._baselines` are **in-memory dicts**, never persisted. Attacker influence therefore dies at backend restart and can never reach the database or a model. The exposure is real but session-scoped: within one drill, an attacker whose windows score LOW shifts the confidence weighting used for later windows *in that same drill*, which distorts the very measurement the drill exists to produce.

Minimal fix: a `suspend_learning: bool` flag on `ContextConfidenceLayer`, set for the lifetime of a drill session, making `observe_genuine` a no-op. `assess()` continues to work from whatever statistics were learned during genuine operation — which is the correct comparison baseline.

Classified RECOMMENDED rather than REQUIRED because it cannot contaminate a model or a corpus. It is required for the *drill measurement* to be trustworthy, so in practice it should ship with 2.6.

### 2.8 Model updates — **REQUIRED (as an explicit refusal)**

**Decision: single-user automatic updates stay disabled, and the refusal becomes explicit rather than incidental.**

Why disabled:

1. **There is no impostor evidence that may legally reach an update.** The only impostor data this project will ever hold is the drill, and the drill is — by the invariant in 2.6 — excluded from every corpus. So a candidate's FAR is unmeasurable by construction, and under 2.2 an unmeasurable FAR means the update cannot be certified as non-regressive. Refusing is the correct answer, not a limitation to work around.
2. **`PLAN.md` §12 makes adaptation "earned" (P7).** Earning it requires evidence this configuration cannot produce. Relaxing `quarantine_days`, `retraining_cadence_days`, `regression_tolerance`, or the G1–G6 gates to make an update possible would weaken the exact safeguard the project claims as a contribution.
3. **It is not on the critical path.** The project's contribution here is enrollment plus live detection. E1 (drift benefit) and E2 (poisoning resistance) both need a cohort and are already documented as pending human protocols.

What changes: `tools/updates/run.py::run_update` gains an early, explicit refusal — if the frozen corpus contains only one participant, return `UpdateRunOutcome(status="SKIPPED", code="UPDATE_REQUIRES_IMPOSTOR_COHORT")` before any training happens, instead of raising a generic `ValueError` deep inside `build_candidate` after artifacts have already been written to disk.

**Explicitly NOT changed:** `require_promotion_gate`, `submit_segment`, `reassess`, `_validate`'s tolerance, `quarantine_days`, `retraining_cadence_days`, `min_promotable_windows`, `retained_model_versions`.

### 2.9 Documentation — **REQUIRED**

| File | Change |
| --- | --- |
| `PLAN.md` | New **ADR-014 — Single-participant enrollment with unseen live attacker**, plus a §20 Document Control entry marking this a **major revision (v3.0)** because it changes the evaluation methodology. Must state what is given up: no I1 matrix, no cross-user FAR, no per-user metric spread, no cohort confidence intervals — and what replaces it: the live drill as primary impostor evidence, reported with N and detection latency. |
| `docs/evaluation.md` | Add the single-participant order of operations; state that FAR is unmeasured in that variant and that the drill supplies the impostor result. |
| `docs/architecture.md` | One paragraph on the drill data-flow invariant and the `drill_sessions` boundary. |
| *(new)* `docs/pilot/attack-drill-protocol.md` | The operator procedure. **Must lead with: the corpus is frozen and the profile activated before the attacker touches the machine, and no freeze is ever created after a drill without excluding drill sessions.** |
| `DATA_COLLECTION_GUIDE.md` | Correct "Minimum useful: 3 distinct days / plan for 5" to the post-change policy; add the drill command; add an explicit "freeze before drill" line. |
| `guide.md` | Correct "at least 2 different calendar days"; align with `DATA_COLLECTION_GUIDE.md` so the two stop disagreeing. |
| `tools/demo/bootstrap_first_model.py` | Fix the stale docstring claim that it accepts any provenance — `require_promotion_gate` now rejects `TEAM`/`PILOT`. |
| `config/collection.pilot.yaml` | Update the "Five days partitions to…" comment, which is true about the split but misleading about admissibility. |

### 2.10 Tests — **REQUIRED**

Enumerated with exact assertions in Part III (§10).

---

## 3. Change classification

### REQUIRED — the architecture is wrong without these

| # | Change | Area |
| --- | --- | --- |
| R1 | `false_acceptance_rate: float \| None`; `_validate` rejects unmeasured FAR | 2.2 |
| R2 | `ValidationReport.operating_point` audit string | 2.2 |
| R3 | Activation succeeds with genuine-only scores; FRR at the policy operating point | 2.1 |
| R4 | Split live-state-machine thresholds from training-admission thresholds; new `ml.enrollment.*` | 2.3 |
| R5 | `EnrollmentAdmission` fields renamed to disclose TRAIN scope | 2.3 |
| R6 | DEGRADED → recovery wired on both scoring and heartbeat triggers | 2.4 |
| R7 | Degrade/recover transitions persisted via `upsert_user` | 2.4 |
| R8 | Calibration counts only post-activation, actually-scored windows | 2.5 |
| R9 | `drill_sessions` table + `--drill-label` + five suppressions | 2.6 |
| R10 | `load_window_summaries` excludes drill sessions | 2.6 |
| R11 | `run_update` refuses explicitly with `UPDATE_REQUIRES_IMPOSTOR_COHORT` | 2.8 |
| R12 | ADR-014 + the documentation set | 2.9 |
| R13 | The pre-drill test suite | §10 |

### RECOMMENDED — safety and integrity, not correctness

| # | Change | Rationale |
| --- | --- | --- |
| S1 | `ContextConfidenceLayer.suspend_learning` during drills | In-memory only, but distorts the drill's own measurement (2.7) |
| S2 | Guardrail **G12**: source-scan asserting `load_window_summaries` filters on `drill_sessions` and `_submit_segment_candidate` is drill-gated | Regex guardrails are weak; the behavioural tests in §10 are the real protection. G12 catches careless deletion. |
| S3 | Record `max(stored_at_utc)` covered by each freeze in the manifest header | Makes "was this corpus frozen before the drill?" checkable after the fact |
| S4 | Fail `tools.collection freeze` loudly if any drill session exists and would have contributed windows | Turns the procedural risk into a startup error |
| S5 | Tighten G1 to `level is RiskLevel.LOW` only | `PLAN.md` §12.1 says "exceeded MEDIUM", so the current reading is defensible; tightening is a security judgement for a human, and updates are disabled anyway |

### OPTIONAL — defer

| # | Change |
| --- | --- |
| O1 | `config/risk.pilot.yaml` + relaxing `development_only: Literal[True]` to an explicit `review_status` field. Not needed while `ml.enrollment.*` holds the new policy. |
| O2 | Persisting context-confidence statistics across restarts |
| O3 | Dashboard surface for `ValidationReport` (nothing consumes it today) |
| O4 | A drill view in the dashboard showing the live risk trace with a marked takeover point |
| O5 | I3 replay/synthetic impostor testing |

### MUST NOT BE DONE

| # | Anti-change | Why |
| --- | --- | --- |
| N1 | **Enroll a second participant to satisfy activation** | Inverts the experiment: the impostor would become a cohort member whose data sits in the frozen corpus |
| N2 | **Collect day 5 or day 6 to clear the current gates** | Day 5 changes nothing (TRAIN stays at 2 days); day 6 clears the day gate and then hits the FAR gate |
| N3 | **Write `FAR = 0.0` when unmeasured** | Fabricated metric; the exact failure the original spec was written to prevent |
| N4 | **Lower `quarantine_days`, `retraining_cadence_days`, `regression_tolerance`, or `min_promotable_windows`** | Weakens a claimed contribution to make a disabled feature run |
| N5 | **Modify `ml/training/gate.py::require_promotion_gate`** | It is the poisoning boundary and is not implicated in any finding |
| N6 | **Remove the `user_has_active_profile` refusal from the enrollment gate** | It is what stops attacker data re-enrolling after activation |
| N7 | **Give the attacker their own `user_id`, consent, or enrollment record** | Makes them an enrolled participant; also breaks scoring against the legitimate profile |
| N8 | **Add a field to `FeatureWindow` or any protocol schema for the drill flag** | Forces codegen + C++ changes and re-grains a session-level fact to window level |
| N9 | **Use `config/risk.demo-live-single-day.yaml` for the drill** | `min_distinct_days: 1` produces a single-day baseline; the file itself says its output must not be cited |
| N10 | **Delete or rewrite the 1221 collected windows** | They are sound data; the pre-activation `scores` rows are handled by R8, not by deletion |

---

## 4. The 4-day dataset — the policy decision

**The decision needed is not "what threshold makes 4 days pass." It is "which of two different policies is `min_distinct_days`, and where does each belong."** Section 2.3 sets this out; the summary:

Today one config key answers two questions at once. Separating them yields:

- **`config/risk.*.yaml → enrollment.min_distinct_days: 3` — unchanged.** Governs the live `ENROLLING → CALIBRATING` transition against the user's whole observed history. The current corpus has 4 distinct days and 1221 windows, so this is already satisfied and needs no discussion.
- **`config/ml.*.yaml → enrollment.min_train_distinct_days` — new, and this is the decision.** Governs how many distinct days of the *frozen TRAIN partition* a first profile must be built from.

The policy sentence to approve or reject:

> *A first profile must be trained on at least two distinct calendar days, so that a single day's posture, device position, or mood cannot alone define the identity baseline. Fewer than two days means no cross-day variation is represented at all. More than two is not currently justified by evidence: `PLAN.md` §10.5's enrollment-length experiment (open decision O3) has not been run, and choosing a higher number would be an assumption dressed as a threshold.*

Approve that sentence and the value is 2. It should be recorded with `config_version: ml-enrollment-provisional-pending-o3-v1` and a one-line comment noting the consequence — under the current 60/20/20 freeze, a 4-day round is admissible and a 3-day round is not.

**The current dataset satisfying this is a consequence, not the reason.** A reviewer must be able to read the sentence alone, with no knowledge of how much data exists, and agree.

**Verified partition behaviour** (`_partition_days` executed against `config/collection.pilot.yaml`):

| Collection days | TRAIN | VALIDATION | EVALUATION | Admissible at `min_train_distinct_days: 2` |
| --- | --- | --- | --- | --- |
| 3 | 1 | 1 | 1 | ✗ |
| **4 (current)** | **2** | **1** | **1** | **✓** |
| 5 | 2 | 2 | 1 | ✓ |
| 6 | 3 | 2 | 1 | ✓ |

TRAIN would hold days 2026-09-05 and 2026-09-06: 236 keyboard-scorable and 345 mouse-scorable windows against a `min_baseline_windows` of 20. VALIDATION would be 2026-09-07 (192 kbd / 351 mouse) — ample for a real FRR measurement. EVALUATION, 2026-09-08, stays untouched for the headline result.

---

## 5. FAR behaviour — precise specification

**When no impostor cohort exists in the frozen manifest:**

1. Score every one of the participant's own VALIDATION windows through every trained modality artifact, pooling percentile scores across modalities exactly as the current code does (percentile scores share one calibrated 0–100 scale by construction, so pooling is legitimate).
2. If that pool is empty → **raise** `ENROLLMENT_VALIDATION_NO_GENUINE`. Activation must not proceed without a real genuine measurement.
3. Convert the deployed operating point: `percentile_threshold = (1 - risk.medium_threshold) * 100`. With the shipped `0.45`, that is `55.0`.
4. `false_rejection_rate = mean(genuine_percentiles < percentile_threshold)` — the fraction of the user's own held-out behaviour that the live engine would place at or above MEDIUM. **Genuinely measured.**
5. `false_acceptance_rate = None`. **Never `0.0`. Never estimated. Never imputed.**
6. `ValidationReport(accepted=True, code="ENROLLMENT_INITIAL_PROFILE_NO_IMPOSTOR_COHORT", operating_point="frr@calibrated_risk>=0.450 (risk.medium_threshold); far=unmeasured (no impostor cohort in manifest)", baseline=metrics, candidate=metrics)`.
7. Activate through `SQLiteUpdateRepository.activate_profile` — the same audited path, unchanged.

**When a second participant *is* present in the manifest:** the existing pooled-EER path runs unchanged and both metrics are measured, with `code="ENROLLMENT_INITIAL_PROFILE_NO_BASELINE"`. The two paths are selected purely by whether impostor scores were found — no flag, no config, no operator choice.

**Downstream consumers requiring change:** exactly those in the 2.2 table. `protocol/`, the SQL schema, the REST API, and the dashboard require **no change at all** — confirmed by grep, not assumed.

---

## 6. Live attacker drill — the data-flow invariant

**Permitted:**
```
attacker input → collector → ingestion → feature window → score
              → context assessment → risk fusion → smoothing → risk level
              → decision → enforcement action → alert → audit → dashboard
```

**Structurally forbidden, at named enforcement points:**

| Forbidden path | Blocked at | Failure mode if reached |
| --- | --- | --- |
| → enrollment | `load_window_summaries` filter, then `require_enrollment_admission` | `WINDOW_NOT_IN_FROZEN_CORPUS` |
| → baseline / model training | `train_one_class_model` reads only manifest-admitted windows | `EnrollmentAdmissionError` |
| → calibration | `PercentileCalibrator.fit` runs only inside `train_one_class_model` | unreachable |
| → update candidate | `_submit_segment_candidate` skipped for drill sessions | no row written |
| → retraining | `build_candidate` reads the frozen TRAIN partition only | `no TRAIN-partition windows found` |
| → context baseline | `observe_genuine` suspended | no-op |
| → enrollment/calibration counters | `_progress_state` skipped | no state transition |

**Representation:** the `drill_sessions` table of 2.6 — one table, session-grained, foreign-keyed to `sessions`, no change to any existing table, no protocol change, no codegen. The full rationale for rejecting window-level columns, `entry_auth_evidence` overloading, and `storage_metadata` is in 2.6.

**Operator sequence, which the protocol document must state in this order:**
1. Stop collection. 2. Freeze the corpus. 3. Activate the profile. 4. Observe the shadow period. 5. Enable enforcement. 6. Start the backend **with `--drill-label`**. 7. Attacker operates. 8. Stop. 9. Export the risk trace. 10. **Never create a new freeze that includes drill sessions.**

---

## 7. DEGRADED recovery — intended trigger and minimal change

**Intended trigger (from `PLAN.md` ADR-011):** `DEGRADED` means "enforcement is suspended because a component is unavailable, and this is loudly alerted." It ends when the component is available again.

**Two concrete availability facts, two call sites:**

| Fact | Detected at | Minimal change |
| --- | --- | --- |
| Required modality models are scoring again | `RiskEngine.process`, after `_required_failure()` returns `None` and `_fusion()` succeeds | If `state_machine.state is UserState.DEGRADED`, call `component_recovered()` and re-read `state` before building the decision |
| The collector heartbeat resumed | `RuntimeOrchestrator._handle_heartbeat` | If `self._heartbeat_failed`, clear it and call `self._risk_engine.component_recovered()` |

Both guard on `state is DEGRADED` because `UserStateMachine.recover()` raises otherwise. `_pre_degraded` already restores the pre-degradation state correctly in both directions.

**Plus persistence:** every degrade and every recover writes through `storage.upsert_user`, so the `users` table stops contradicting the decision records.

**Why this is demo-critical:** without it, a single heartbeat gap — and the live database already shows 18 — permanently suspends enforcement for the rest of the backend process, silently, with `STATE_GATED_NO_ENFORCEMENT` on every subsequent action.

---

## 8. CALIBRATING — what counts

**Counts toward calibration:** a `scores` row whose `profile_version` equals the currently active profile's version **and** whose `score_json` shows at least one modality with `available: true`.

**Cannot count:**
- Any row written before activation. Those carry `profile_version = "unavailable"`, so the equality filter excludes all 1221 of them without any date logic.
- Any row from a superseded profile version, so a future rollback or update restarts the shadow period rather than inheriting the old one's credit.
- `INSUFFICIENT_DATA` windows, so an idle lunch break cannot age a user into `ACTIVE`.
- Any drill-session window (`_progress_state` is skipped entirely for drill sessions).

`risk.enrollment.calibration_windows` keeps its current meaning and value (10) — it is now applied to a correctly scoped count. No new config key.

---

## 9. Model updates — decision

**Disabled for this project, explicitly.** Full reasoning in 2.8. In one line: an update cannot be certified without a FAR measurement, this configuration cannot produce one legally, and refusing is the honest outcome. `run_update` fails fast with `UPDATE_REQUIRES_IMPOSTOR_COHORT` before writing any artifact. **No safeguard is weakened.** The E1/E2 experiments remain documented as pending a cohort, exactly as `docs/evaluation.md` already says.

---

## 10. Tests that must exist before the live attacker drill

Every one of these must be green before an attacker touches the machine.

**Activation and FAR (`tools/enrollment/tests/test_activate.py`)**
1. `test_activates_a_single_participant_profile` — one participant, 4 days, no cohort → returns a `ModelProfile`; `validation.candidate.false_acceptance_rate is None`; `false_rejection_rate` is a float in `[0, 1]`; `code == "ENROLLMENT_INITIAL_PROFILE_NO_IMPOSTOR_COHORT"`.
2. `test_far_is_never_fabricated_as_zero` — asserts `is None`, explicitly `not == 0.0`.
3. `test_frr_is_measured_at_the_policy_operating_point` — construct VALIDATION windows with known percentile outcomes; assert the reported FRR equals the hand-computed fraction below `(1 - medium_threshold) * 100`.
4. `test_refuses_activation_with_no_genuine_validation_scores` — raises `ENROLLMENT_VALIDATION_NO_GENUINE`.
5. `test_two_participant_corpus_still_measures_far` — the existing two-participant fixture keeps producing a measured FAR and `ENROLLMENT_INITIAL_PROFILE_NO_BASELINE`.
6. `test_the_evaluation_partition_is_never_loaded` — **existing test, must still pass unmodified.**
7. `test_refuses_a_user_who_already_has_an_active_profile` — **existing test, must still pass unmodified.**

**Update safety (`backend/tests/test_update_manager.py`)**
8. `test_update_is_rejected_when_far_is_unmeasured` — `_validate` with `None` on either side → `accepted is False`, `code == "VALIDATION_FAR_UNMEASURED"`.
9. `test_promotion_gate_semantics_unchanged` — the full existing G1–G6 suite passes untouched.

**Training-admission policy (`ml/tests/test_enrollment_gate.py`)**
10. `test_rejects_a_single_day_train_partition` — `INSUFFICIENT_DISTINCT_DAYS`.
11. `test_admits_a_two_day_train_partition_at_the_configured_policy`.
12. `test_foreign_user_window_is_rejected` — **existing, unmodified.**
13. `test_window_absent_from_manifest_is_rejected` — **existing, unmodified.**

**Config consistency (`ml/tests/test_config.py`)**
14. `test_partitioning_can_satisfy_the_train_day_policy` — for the configured minimum round, assert `_partition_days` yields a TRAIN partition meeting `ml.enrollment.min_train_distinct_days`. This is the check whose absence let the two config files disagree.

**State recovery (`backend/tests/test_risk_engine.py`)**
15. `test_degraded_recovers_when_scoring_succeeds_again` — degrade via an unavailable model, then supply a fully-scored window → state returns to its pre-degradation value and `enforcement_applied` becomes possible again.
16. `test_degraded_recovers_when_heartbeat_resumes` — orchestrator-level; heartbeat timeout then arrival → recovered.
17. `test_degrade_and_recover_are_persisted` — the `users` row matches the engine state after each transition.
18. `test_single_anomalous_window_cannot_enforce` — **existing, unmodified.**

**Calibration progression (`backend/tests/test_runtime_integration.py`)**
19. `test_pre_activation_scores_do_not_satisfy_calibration` — seed `scores` rows with `profile_version='unavailable'`, activate, feed one window → state is `CALIBRATING`, **not** `ACTIVE`.
20. `test_calibration_completes_only_after_configured_post_activation_windows` — exactly `calibration_windows` scored windows are required.
21. `test_insufficient_data_windows_do_not_count_toward_calibration`.

**Drill isolation (`backend/tests/test_attack_drill.py`, new file)** — *the ones that matter most*
22. `test_drill_windows_are_scored_and_produce_decisions` — the drill must actually work.
23. `test_drill_windows_are_excluded_from_load_window_summaries`.
24. `test_drill_windows_never_enter_a_freeze_manifest` — freeze a DB containing both genuine and drill sessions; assert no drill `window_id` appears in `records`.
25. `test_drill_segments_never_become_update_candidates` — zero `update_candidates` rows for drill segments, **including rejected ones**.
26. `test_no_a1_anchor_is_recorded_for_a_drill_session`.
27. `test_drill_windows_do_not_advance_user_state`.
28. `test_context_learning_is_suspended_during_a_drill` — `ContextConfidenceLayer.statistics()` is unchanged across the drill.
29. `test_enrollment_admission_rejects_a_drill_window_even_if_smuggled_in` — hand-construct an `EnrollmentAdmission` whose manifest omits the drill window; assert `WINDOW_NOT_IN_FROZEN_CORPUS`.

**End-to-end rehearsal (`backend/tests/test_runtime_integration.py`)**
30. `test_synthetic_takeover_escalates_through_the_ladder` — drive `tools/synthetic --scenario takeover` through the full runtime with a trained profile and enforcement enabled against stub adapters; assert the ladder reaches `REAUTH`, that a risk trace exists across the transition, and that the whole drill leaves the training corpus byte-identical.

**Regression suites that must remain green, unmodified:** the full `python -m pytest` run, `python tools/guardrails/check.py`, `python protocol/codegen/generate.py --check`, and `python -m mypy backend/app ml tools`.

---

## 11. Implementation order

Ordered by "what must be true before the next step is safe," not by size.

| Phase | Task | Why here |
| --- | --- | --- |
| **A — unblock activation** | T1 · FAR-unmeasured contract (R1, R2) | Everything in activation depends on the metric type |
| | T2 · Training-admission day policy (R4, R5) | Must be settled before a freeze is created |
| | T3 · Single-user activation (R3) | Consumes T1 and T2 |
| **B — make the activated system behave** | T4 · Calibration progression (R8) | Must land **before** the first activation is used live, or the shadow period is lost forever on this corpus |
| | T5 · DEGRADED recovery + persistence (R6, R7) | Must land before enforcement is enabled |
| **C — make the drill safe** | T6 · `drill_sessions` + `--drill-label` + suppressions (R9, R10) | Must exist before any attacker touches the machine |
| | T7 · Context-learning suspension (S1) | Ships with T6; drill measurement integrity |
| | T8 · Update refusal (R11) + guardrail G12 (S2) | Closes the last automated path |
| **D — record and verify** | T9 · ADR-014 + documentation (R12) | The methodology change must be written down before results are produced under it |
| | T10 · Pre-drill verification suite (R13) | Final gate |

**Operational milestones interleaved with the code:**

- After **Phase A**: freeze the corpus (`--version pilot-v1`), then activate. Not before — `build_freeze` is write-once and refuses to overwrite, so freezing under a policy you are about to change wastes the version name.
- After **Phase B**: run normally for the shadow period; review the live score distribution; only then set `enforcement.enabled: true`.
- After **Phase C**: rehearse the drill against synthetic input (test 30) before running it with a person.
- **Phase D** completes before any number from the drill is reported anywhere.

---

## 12. Verdict on the existing 4-day dataset

**RETAINED and used, after the implementation changes. Not frozen now. Not extended. Not discarded.**

- **Not discarded.** 1221 windows across 4 distinct days, all `PILOT`, all under an active consent recorded 2026-09-05T06:17:14Z which precedes the first collection day. 593 FULL, 688 keyboard-scorable, 1107 mouse-scorable — against a `min_baseline_windows` of 20. It is clean, single-participant, genuine data with no attacker contamination, because no drill has been run.
- **Not extended.** Day 5 produces a 2-day TRAIN partition, identical to day 4 — it would change nothing. Day 6 would clear the day gate only to hit the FAR gate, costing two days for no progress. Both are ruled out by the accepted decisions (9) and (10).
- **Not frozen yet.** Freezing is write-once and irreversible. It should happen after Phase A, when the training-admission policy is settled, so `pilot-v1` is created under the rules it will actually be trained under.
- **Retained and used.** After Phase A: freeze → TRAIN = 2026-09-05 + 2026-09-06, VALIDATION = 2026-09-07, EVALUATION = 2026-09-08 → activate with a measured FRR and an honestly unmeasured FAR.

One caveat to carry into the report: 2026-09-05 is a partial day (100 windows against 288–450 on the others), so the TRAIN partition is effectively "one full day plus a partial day." That is worth stating alongside any result, and it is an argument for eventually running `PLAN.md` §10.5's enrollment-length experiment — but it is not a reason to delay the drill, and it is not a reason to collect more days before the blockers are fixed.

---

# PART II — TASKS

> Each task ends with an independently testable deliverable and a commit. Run
> `python -m pytest`, `python tools/guardrails/check.py`, and
> `python -m mypy backend/app ml tools` before every commit.

### Task 1: FAR-unmeasured contract

**Files:**
- Modify: `backend/app/updates/manager.py` (`ValidationMetrics`, `ValidationReport`, `UpdateManager._validate`)
- Modify: `backend/app/updates/repository.py:26-43` (`_profile_from_row`)
- Test: `backend/tests/test_update_manager.py`

**Interfaces:**
- Produces: `ValidationMetrics(false_rejection_rate: float, false_acceptance_rate: float | None)`; `ValidationReport(accepted: bool, code: str, baseline: ValidationMetrics, candidate: ValidationMetrics, operating_point: str = "")`. Tasks 3, 8 and `tools/experiments/drift.py` consume both.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_update_manager.py
def test_validation_metrics_accepts_unmeasured_far() -> None:
    metrics = ValidationMetrics(false_rejection_rate=0.12, false_acceptance_rate=None)
    assert metrics.false_acceptance_rate is None


def test_update_is_rejected_when_far_is_unmeasured(update_settings, repository) -> None:
    manager = UpdateManager(update_settings, repository)
    build = CandidateBuild(
        profile_version="v2",
        keyboard_artifact_version="k2",
        keyboard_checksum="a" * 64,
        mouse_artifact_version=None,
        mouse_checksum=None,
        baseline_metrics=ValidationMetrics(0.10, None),
        candidate_metrics=ValidationMetrics(0.09, None),
    )
    report = manager._validate(build)
    assert report.accepted is False
    assert report.code == "VALIDATION_FAR_UNMEASURED"
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest backend/tests/test_update_manager.py -k "unmeasured" -v`
Expected: FAIL — `ValueError: false acceptance rate must be in [0, 1]`.

- [ ] **Step 3: Implement**

```python
# backend/app/updates/manager.py
@dataclass(frozen=True)
class ValidationMetrics:
    false_rejection_rate: float
    #: ``None`` means NOT MEASURED -- there was no impostor evidence to
    #: measure against. It never means "zero". Any consumer that needs a
    #: FAR comparison must refuse rather than substitute a value.
    false_acceptance_rate: float | None

    def __post_init__(self) -> None:
        if not 0 <= self.false_rejection_rate <= 1:
            raise ValueError("false rejection rate must be in [0, 1]")
        if self.false_acceptance_rate is None:
            return
        if not 0 <= self.false_acceptance_rate <= 1:
            raise ValueError("false acceptance rate must be in [0, 1]")


@dataclass(frozen=True)
class ValidationReport:
    accepted: bool
    code: str
    baseline: ValidationMetrics
    candidate: ValidationMetrics
    #: Free-form audit string naming the operating point the metrics were
    #: measured at, and stating plainly when FAR was not measured.
    operating_point: str = ""
```

```python
# backend/app/updates/manager.py :: UpdateManager._validate
    def _validate(self, build: CandidateBuild) -> ValidationReport:
        tolerance = self.settings.update_manager.regression_tolerance
        baseline_far = build.baseline_metrics.false_acceptance_rate
        candidate_far = build.candidate_metrics.false_acceptance_rate
        if baseline_far is None or candidate_far is None:
            # An update may only replace an active profile when it is shown
            # not to have raised FAR. With FAR unmeasured that showing is
            # impossible, so the honest answer is refusal -- never a
            # substituted zero (PLAN.md P7: adaptation must be earned).
            return ValidationReport(
                accepted=False,
                code="VALIDATION_FAR_UNMEASURED",
                baseline=build.baseline_metrics,
                candidate=build.candidate_metrics,
                operating_point="far unmeasured on at least one side; regression undecidable",
            )
        frr_regression = (
            build.candidate_metrics.false_rejection_rate
            - build.baseline_metrics.false_rejection_rate
        )
        far_regression = candidate_far - baseline_far
        accepted = frr_regression <= tolerance and far_regression <= tolerance
        return ValidationReport(
            accepted=accepted,
            code="VALIDATION_PASSED" if accepted else "VALIDATION_REGRESSION",
            baseline=build.baseline_metrics,
            candidate=build.candidate_metrics,
            operating_point="pooled-EER threshold on VALIDATION with cross-user impostors",
        )
```

```python
# backend/app/updates/repository.py :: _profile_from_row
    validation = ValidationReport(
        accepted=bool(validation_data["accepted"]),
        code=str(validation_data["code"]),
        baseline=ValidationMetrics(**validation_data["baseline"]),
        candidate=ValidationMetrics(**validation_data["candidate"]),
        # Profiles written before operating_point existed still load.
        operating_point=str(validation_data.get("operating_point", "")),
    )
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest backend/tests/test_update_manager.py -v`
Expected: PASS, including every pre-existing G1–G6 test.

- [ ] **Step 5: Commit**

```bash
git add backend/app/updates/manager.py backend/app/updates/repository.py backend/tests/test_update_manager.py
git commit -m "feat: represent an unmeasured FAR as None and refuse to certify updates without it

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Training-admission day policy, scoped and named

**Files:**
- Modify: `config/ml.development.yaml` (new `enrollment:` section)
- Modify: `ml/features/config.py` (validate the new section)
- Modify: `ml/training/enrollment.py` (`EnrollmentAdmission` field names)
- Modify: `tools/enrollment/activate.py`, `tools/experiments/drift.py` (call sites)
- Test: `ml/tests/test_enrollment_gate.py`, `ml/tests/test_config.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `EnrollmentAdmission(..., min_train_windows: int, min_train_distinct_days: int, ...)` and `ml_config.raw["enrollment"]["min_train_windows" | "min_train_distinct_days"]`. Task 3 consumes both.

- [ ] **Step 1: Write the failing tests**

```python
# ml/tests/test_config.py
def test_ml_config_exposes_the_training_admission_policy() -> None:
    config = load_config(Path("config/ml.development.yaml"))
    enrollment = config.raw["enrollment"]
    assert enrollment["min_train_windows"] >= 1
    assert enrollment["min_train_distinct_days"] >= 2


def test_partitioning_can_satisfy_the_train_day_policy() -> None:
    """The freeze split and the training-admission policy must agree.

    Nothing checked this before, which is how config/collection.pilot.yaml
    and config/risk.development.yaml came to disagree silently.
    """
    settings = load_collection_settings(Path("config/collection.pilot.yaml"))
    policy = load_config(Path("config/ml.development.yaml")).raw["enrollment"]
    days = [f"2026-09-{index:02d}" for index in range(5, 5 + settings.min_distinct_days + 1)]
    assignments = _partition_days(days, settings)
    train_days = {day for day, part in assignments.items() if part == "TRAIN"}
    assert len(train_days) >= policy["min_train_distinct_days"]
```

```python
# ml/tests/test_enrollment_gate.py
def test_rejects_a_single_day_train_partition(admission_factory, make_window) -> None:
    windows = [make_window(window_id=f"w{i}", collection_day="2026-09-05") for i in range(30)]
    admission = admission_factory(windows, min_train_windows=20, min_train_distinct_days=2)
    with pytest.raises(EnrollmentAdmissionError, match="INSUFFICIENT_DISTINCT_DAYS"):
        require_enrollment_admission("participant-01", windows, admission)


def test_admits_a_two_day_train_partition(admission_factory, make_window) -> None:
    windows = [
        make_window(window_id=f"w{i}", collection_day="2026-09-05" if i < 15 else "2026-09-06")
        for i in range(30)
    ]
    admission = admission_factory(windows, min_train_windows=20, min_train_distinct_days=2)
    require_enrollment_admission("participant-01", windows, admission)
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest ml/tests/test_config.py ml/tests/test_enrollment_gate.py -v`
Expected: FAIL — `KeyError: 'enrollment'` and `TypeError: unexpected keyword argument 'min_train_windows'`.

- [ ] **Step 3: Implement**

```yaml
# config/ml.development.yaml  (append)

# First-profile TRAINING-ADMISSION policy (ADR-013). This is NOT the live
# state-machine threshold in config/risk.*.yaml -- that one asks "how much
# has the runtime observed before this user stops being ENROLLING?" and is
# counted over the user's whole history. This one asks "how much of the
# frozen TRAIN partition must a first model actually be built from?"
#
# Policy: a first profile must be trained on at least two distinct calendar
# days, so a single day's posture, device position, or mood cannot alone
# define the identity baseline. One day represents no cross-day variation at
# all. A higher number is not currently justified: PLAN.md Section 10.5's
# enrollment-length experiment (open decision O3) has not been run, and
# choosing one would be an assumption dressed as a threshold.
#
# Consequence under config/collection.pilot.yaml's 60/20/20 freeze: a 4-day
# collection round is admissible (TRAIN = 2 days); a 3-day round is not.
enrollment:
  min_train_windows: 20
  min_train_distinct_days: 2
```

Bump the file's own provenance marker in a comment: `# policy status: provisional, pending PLAN.md Section 10.5 (O3)`.

```python
# ml/features/config.py :: load_config -- add to the required-section block
        enr = raw["enrollment"]
    ...
    _require_positive(enr["min_train_windows"], "enrollment.min_train_windows")
    _require_positive(enr["min_train_distinct_days"], "enrollment.min_train_distinct_days")
```

```python
# ml/training/enrollment.py -- rename the two fields so their scope is legible
@dataclass(frozen=True)
class EnrollmentAdmission:
    settings: CollectionSettings
    consent: ConsentRecord | None
    enrollment: EnrollmentRecord | None
    manifest_window_ids: frozenset[str]
    observed_at_by_window: Mapping[str, datetime]
    #: Scoped to the TRAIN partition, not to the whole corpus. The live
    #: state machine's own thresholds live in config/risk.*.yaml and are a
    #: different policy; conflating them turned a "3 distinct days" rule
    #: into an undocumented "6 collection days" requirement.
    min_train_windows: int
    min_train_distinct_days: int
    user_has_active_profile: bool
    participant_id: str
```

and in `require_enrollment_admission`, replace the two comparisons and keep the
existing `INSUFFICIENT_WINDOWS` / `INSUFFICIENT_DISTINCT_DAYS` reason codes verbatim.

```python
# tools/enrollment/activate.py -- source the policy from the ML config
    admission = EnrollmentAdmission(
        ...
        min_train_windows=int(ml_config.raw["enrollment"]["min_train_windows"]),
        min_train_distinct_days=int(ml_config.raw["enrollment"]["min_train_distinct_days"]),
        ...
    )
```

`risk_settings` remains a parameter of `activate_first_profile` — it is still
needed for the operating point in Task 3.

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest ml/tests/ tools/enrollment/tests/ tools/experiments/tests/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add config/ml.development.yaml ml/features/config.py ml/training/enrollment.py tools/enrollment/activate.py tools/experiments/drift.py ml/tests/
git commit -m "feat: separate first-profile training admission from live state-machine thresholds

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Single-participant activation

**Files:**
- Modify: `tools/enrollment/activate.py` (`_measure_validation_metrics`, `activate_first_profile`, module docstring)
- Test: `tools/enrollment/tests/test_activate.py`

**Interfaces:**
- Consumes: `ValidationMetrics`/`ValidationReport` from Task 1; `EnrollmentAdmission` field names from Task 2.
- Produces: `_measure_validation_metrics(participant_id, profile, validation_windows_by_user, *, medium_threshold: float) -> tuple[ValidationMetrics, str]` returning the metrics and the `operating_point` string.

- [ ] **Step 1: Write the failing tests**

```python
# tools/enrollment/tests/test_activate.py
def test_activates_a_single_participant_profile(single_participant_fixture) -> None:
    profile = activate_first_profile(**single_participant_fixture)
    assert profile.validation.accepted is True
    assert profile.validation.code == "ENROLLMENT_INITIAL_PROFILE_NO_IMPOSTOR_COHORT"
    assert profile.validation.candidate.false_acceptance_rate is None
    assert 0.0 <= profile.validation.candidate.false_rejection_rate <= 1.0
    assert "unmeasured" in profile.validation.operating_point


def test_far_is_never_fabricated_as_zero(single_participant_fixture) -> None:
    profile = activate_first_profile(**single_participant_fixture)
    far = profile.validation.candidate.false_acceptance_rate
    assert far is None
    assert far != 0.0


def test_refuses_activation_with_no_genuine_validation_scores(
    single_participant_fixture, monkeypatch
) -> None:
    monkeypatch.setattr(
        corpus_module,
        "load_frozen_corpus",
        _corpus_with_empty_validation(corpus_module.load_frozen_corpus),
    )
    with pytest.raises(ValueError, match="ENROLLMENT_VALIDATION_NO_GENUINE"):
        activate_first_profile(**single_participant_fixture)


def test_two_participant_corpus_still_measures_far(enrollment_fixture) -> None:
    profile = activate_first_profile(**enrollment_fixture)
    assert profile.validation.code == "ENROLLMENT_INITIAL_PROFILE_NO_BASELINE"
    assert profile.validation.candidate.false_acceptance_rate is not None
```

Add a `single_participant_fixture` alongside the existing two-participant
`enrollment_fixture`, built from the same helpers with `participant_ids = ["participant-01"]`.

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tools/enrollment/tests/test_activate.py -v`
Expected: FAIL — `ValueError: ENROLLMENT_VALIDATION_INSUFFICIENT`.

- [ ] **Step 3: Implement**

```python
def _measure_validation_metrics(
    participant_id: str,
    profile: dict[str, ModelArtifact],
    validation_windows_by_user: dict[str, list],
    *,
    medium_threshold: float,
) -> tuple[ValidationMetrics, str]:
    """Measure FRR always; measure FAR only when impostor evidence exists.

    Genuine scores are mandatory: a corpus that cannot produce one is a
    data problem and must stop activation. Impostor scores are not, because
    a single-participant corpus can never contain any -- and an absent
    cohort is a fact to record, never a number to invent.

    Without impostors there is no Equal Error Rate, so FRR is measured at
    the operating point the deployed system actually uses.
    ``backend/app/models/service.py`` maps a normality percentile to risk as
    ``1 - percentile/100``, so ``risk.medium_threshold`` corresponds to the
    percentile ``(1 - medium_threshold) * 100``. The reported FRR is
    therefore the fraction of the participant's own held-out windows that
    the live risk engine would place at or above MEDIUM.
    """

    genuine: list[float] = []
    impostor: list[float] = []
    for artifact in profile.values():
        cross_results = zero_effort_cross_evaluation(
            {participant_id: artifact}, validation_windows_by_user
        )
        result = cross_results.get(participant_id)
        if result is None:
            continue
        genuine.extend(result.genuine_scores)
        impostor.extend(result.all_impostor_scores().tolist())

    if not genuine:
        raise ValueError(
            "ENROLLMENT_VALIDATION_NO_GENUINE: no scorable VALIDATION-partition "
            f"windows were available for participant {participant_id!r}; a first "
            "profile is never activated without a real genuine measurement"
        )

    genuine_arr = np.asarray(genuine, dtype=float)
    if impostor:
        impostor_arr = np.asarray(impostor, dtype=float)
        eer = compute_eer(genuine_arr, impostor_arr)
        rates = compute_far_frr(genuine_arr, impostor_arr, eer.threshold)
        return (
            ValidationMetrics(
                false_rejection_rate=rates.frr,
                false_acceptance_rate=rates.far,
            ),
            "pooled-EER threshold on VALIDATION with cross-user impostors",
        )

    percentile_threshold = (1.0 - medium_threshold) * 100.0
    frr = float(np.mean(genuine_arr < percentile_threshold))
    operating_point = (
        f"frr@calibrated_risk>={medium_threshold:.3f} (risk.medium_threshold); "
        "far=unmeasured (no impostor cohort in manifest)"
    )
    return ValidationMetrics(false_rejection_rate=frr, false_acceptance_rate=None), operating_point
```

```python
# tools/enrollment/activate.py :: activate_first_profile
    metrics, operating_point = _measure_validation_metrics(
        participant_id,
        profile_artifacts,
        validation_corpus.windows_by_user,
        medium_threshold=risk_settings.risk.medium_threshold,
    )
    ...
        validation=ValidationReport(
            accepted=True,
            code=(
                "ENROLLMENT_INITIAL_PROFILE_NO_BASELINE"
                if metrics.false_acceptance_rate is not None
                else "ENROLLMENT_INITIAL_PROFILE_NO_IMPOSTOR_COHORT"
            ),
            # accepted=True gates ACTIVATION, and a first profile's
            # acceptance criterion is the ADR-013 enrollment gate, not a
            # regression check -- there is no prior profile to regress
            # against. The reason code and operating_point say so. An
            # UPDATE means something different and refuses an unmeasured
            # FAR (backend/app/updates/manager.py::_validate).
            baseline=metrics,
            candidate=metrics,
            operating_point=operating_point,
        ),
```

Also move `save_artifact` for both modalities to **after** the metrics call, so a
failed measurement no longer leaves orphan `.joblib` files with no database row.

Rewrite the module docstring's second bullet to describe the two paths honestly.

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tools/enrollment/tests/test_activate.py -v`
Expected: PASS, including the unmodified `test_the_evaluation_partition_is_never_loaded`
and `test_refuses_a_user_who_already_has_an_active_profile`.

- [ ] **Step 5: Commit**

```bash
git add tools/enrollment/ && git commit -m "feat: activate a first profile without an impostor cohort, with FAR recorded as unmeasured

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Calibration counts only post-activation scored windows

**Files:**
- Modify: `backend/app/runtime/orchestrator.py::_progress_state`
- Test: `backend/tests/test_runtime_integration.py`

**Interfaces:**
- Consumes: nothing. Independent of Tasks 1–3.
- Produces: no new public signature.

- [ ] **Step 1: Write the failing tests**

```python
def test_pre_activation_scores_do_not_satisfy_calibration(runtime_with_activated_profile) -> None:
    """1221 pre-activation rows in the live DB carry profile_version='unavailable'.

    Counting them let a user reach ACTIVE within one window of activation,
    skipping the PLAN.md Section 5.3 shadow period entirely.
    """
    runtime = runtime_with_activated_profile(pre_activation_score_rows=50)
    runtime.feed_one_scored_window()
    assert runtime.user_state() is UserState.CALIBRATING


def test_calibration_completes_only_after_configured_post_activation_windows(
    runtime_with_activated_profile,
) -> None:
    runtime = runtime_with_activated_profile(pre_activation_score_rows=50)
    for _ in range(runtime.calibration_windows):
        runtime.feed_one_scored_window()
    assert runtime.user_state() is UserState.ACTIVE


def test_insufficient_data_windows_do_not_count_toward_calibration(
    runtime_with_activated_profile,
) -> None:
    runtime = runtime_with_activated_profile(pre_activation_score_rows=0)
    for _ in range(runtime.calibration_windows * 2):
        runtime.feed_one_insufficient_data_window()
    assert runtime.user_state() is UserState.CALIBRATING
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest backend/tests/test_runtime_integration.py -k calibration -v`
Expected: FAIL — state is `ACTIVE` after the first window.

- [ ] **Step 3: Implement**

```python
    def _progress_state(self, *, after_score: bool) -> None:
        if self._active_user is None or self._risk_engine is None:
            return
        if not self._profile_available or self._scoring is None:
            return
        if self._drill_label is not None:      # added by Task 6; harmless until then
            return
        profile_version = self._scoring.profile.profile_version
        with self.storage.database.connection() as connection:
            counts = connection.execute(
                """
                SELECT COUNT(*) AS windows, COUNT(DISTINCT collection_day) AS days
                FROM feature_windows WHERE user_id = ?
                """,
                (self._active_user,),
            ).fetchone()
            # Only windows scored by THIS profile, and only where a model
            # actually produced a score, may age a user out of CALIBRATING.
            # Rows written before activation carry profile_version
            # 'unavailable' and are excluded by the equality alone; the
            # json_extract clauses additionally exclude INSUFFICIENT_DATA
            # windows, so an idle lunch break cannot complete calibration.
            scored = connection.execute(
                """
                SELECT COUNT(*) AS scored FROM scores
                WHERE user_id = ? AND profile_version = ?
                  AND (json_extract(score_json, '$.keyboard.available') = 1
                       OR json_extract(score_json, '$.mouse.available') = 1)
                """,
                (self._active_user, profile_version),
            ).fetchone()
        transition = self._risk_engine.state_machine.observe_progress(
            enrollment_windows=int(counts["windows"]),
            distinct_days=int(counts["days"]),
            calibration_windows=int(scored["scored"]) if after_score else 0,
        )
        if transition is not None:
            self.storage.upsert_user(self._active_user, transition.current)
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest backend/tests/test_runtime_integration.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/runtime/orchestrator.py backend/tests/test_runtime_integration.py
git commit -m "fix: count only post-activation scored windows toward calibration

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: DEGRADED recovery and state persistence

**Files:**
- Modify: `backend/app/risk/engine.py` (`process`, `component_recovered`)
- Modify: `backend/app/runtime/orchestrator.py` (`_handle_heartbeat`, a `_persist_state` helper)
- Test: `backend/tests/test_risk_engine.py`, `backend/tests/test_runtime_integration.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `RiskEngine.component_recovered() -> StateTransition | None` (was `-> None`), so the orchestrator can persist the transition it caused.

- [ ] **Step 1: Write the failing tests**

```python
def test_degraded_recovers_when_scoring_succeeds_again(engine_with_active_profile) -> None:
    engine = engine_with_active_profile
    engine.process(_request_with_unavailable_model())
    assert engine.state_machine.state is UserState.DEGRADED
    outcome = engine.process(_request_with_both_modalities_scored())
    assert engine.state_machine.state is UserState.ACTIVE
    assert outcome.decision.risk_level is not RiskLevel.UNAVAILABLE


def test_degraded_recovers_when_heartbeat_resumes(runtime) -> None:
    runtime.advance_past_heartbeat_timeout()
    runtime.check_heartbeat()
    assert runtime.user_state() is UserState.DEGRADED
    runtime.feed_heartbeat()
    assert runtime.user_state() is UserState.ACTIVE


def test_degrade_and_recover_are_persisted(runtime) -> None:
    runtime.advance_past_heartbeat_timeout()
    runtime.check_heartbeat()
    assert runtime.stored_user_state() is UserState.DEGRADED
    runtime.feed_heartbeat()
    assert runtime.stored_user_state() is UserState.ACTIVE
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest backend/tests/test_risk_engine.py -k degraded -v`
Expected: FAIL — state stays `DEGRADED`; stored state stays `ENROLLING`.

- [ ] **Step 3: Implement**

```python
# backend/app/risk/engine.py :: RiskEngine.process
        failure = self._required_failure(request)
        if failure is not None:
            return self._failure_outcome(request, failure)
        try:
            fused, keyboard_used, mouse_used = self._fusion(request)
        except Exception as exc:
            return self._failure_outcome(request, f"{type(exc).__name__}: {exc}")

        # The inputs this engine degraded over are demonstrably back: every
        # required modality scored and fusion produced a value. Without this,
        # DEGRADED is a one-way door and a single transient failure suspends
        # enforcement for the entire life of the process (ADR-011 describes a
        # recoverable state, not a terminal one).
        if self.state_machine.state is UserState.DEGRADED:
            recovery = self.component_recovered()
            if recovery is not None and self._recovery_sink is not None:
                self._recovery_sink(recovery)
            state = self.state_machine.state
```

```python
    def component_recovered(self) -> StateTransition | None:
        if self.state_machine.state is not UserState.DEGRADED:
            return None
        return self.state_machine.recover()
```

`_recovery_sink` is a new optional constructor argument, mirroring the existing
`_decision_sink` / `_alert_sink` pattern; the orchestrator passes
`self._persist_state`.

```python
# backend/app/runtime/orchestrator.py
    def _persist_state(self, transition: StateTransition) -> None:
        if self._active_user is not None:
            self.storage.upsert_user(self._active_user, transition.current)

    def _handle_heartbeat(self, event: Heartbeat) -> None:
        self._last_heartbeat_arrival = time.monotonic()
        if self._heartbeat_failed and self._risk_engine is not None:
            # The collector is back. Clearing the flag alone only stopped the
            # repeat alert; the state machine stayed DEGRADED forever.
            recovery = self._risk_engine.component_recovered()
            if recovery is not None:
                self._persist_state(recovery)
        self._heartbeat_failed = False
        ...
```

`_failure_outcome` and `heartbeat_lost` additionally route their `degrade(...)`
transition through `_recovery_sink`/`_persist_state` so degradation is persisted too.

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest backend/tests/test_risk_engine.py backend/tests/test_runtime_integration.py -v`
Expected: PASS, including the unmodified `test_single_anomalous_window_cannot_enforce`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/risk/engine.py backend/app/runtime/orchestrator.py backend/tests/
git commit -m "fix: recover from DEGRADED when scoring or the heartbeat returns, and persist the transition

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: Drill sessions — representation, declaration, and suppression

**Files:**
- Create: `backend/app/storage/migrations/0004_attack_drill.sql`
- Modify: `backend/app/storage/service.py` (`declare_drill_session`, `is_drill_session`)
- Modify: `backend/app/runtime/orchestrator.py` (accept `drill_label`; suppress five behaviours)
- Modify: `backend/app/runtime/application.py`, `backend/app/runtime/cli.py` (`--drill-label`)
- Modify: `tools/collection/repository.py::load_window_summaries` (exclusion)
- Test: `backend/tests/test_attack_drill.py` (new), `tools/collection/tests/test_collection.py`

**Interfaces:**
- Consumes: `_progress_state`'s drill guard from Task 4.
- Produces: `StorageService.declare_drill_session(session_id: str, drill_label: str) -> None`; `RuntimeOrchestrator(..., drill_label: str | None = None)`; `create_collection_application(..., drill_label: str | None = None)`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_attack_drill.py
def test_drill_windows_are_scored_and_produce_decisions(drill_runtime) -> None:
    """The drill must actually work -- suppression is about training, not scoring."""
    emissions = drill_runtime.feed_attacker_session()
    assert any(e.event_type is StreamEventType.RISK for e in emissions)


def test_drill_windows_are_excluded_from_load_window_summaries(drill_runtime) -> None:
    drill_runtime.feed_attacker_session()
    summaries = load_window_summaries(drill_runtime.database_path)
    assert not any(s.window_id in drill_runtime.drill_window_ids for s in summaries)


def test_drill_windows_never_enter_a_freeze_manifest(drill_runtime, tmp_path) -> None:
    drill_runtime.feed_genuine_session()
    drill_runtime.feed_attacker_session()
    document = build_freeze(
        load_window_summaries(drill_runtime.database_path),
        destination=tmp_path / "manifest.json",
        version="drilltest",
        consents=drill_runtime.consents,
        enrollments=drill_runtime.enrollments,
        settings=drill_runtime.collection_settings,
    )
    frozen = {record["window_id"] for record in document["records"]}
    assert frozen.isdisjoint(drill_runtime.drill_window_ids)


def test_drill_segments_never_become_update_candidates(drill_runtime) -> None:
    drill_runtime.feed_attacker_session()
    assert drill_runtime.update_candidate_count_for_drill_segments() == 0


def test_no_a1_anchor_is_recorded_for_a_drill_session(drill_runtime) -> None:
    drill_runtime.feed_attacker_session()
    assert drill_runtime.anchor_count_for_drill_session() == 0


def test_drill_windows_do_not_advance_user_state(drill_runtime) -> None:
    before = drill_runtime.user_state()
    drill_runtime.feed_attacker_session()
    assert drill_runtime.user_state() is before


def test_enrollment_admission_rejects_a_drill_window_even_if_smuggled_in(
    drill_runtime, make_admission_without_drill_windows
) -> None:
    admission = make_admission_without_drill_windows()
    with pytest.raises(EnrollmentAdmissionError, match="WINDOW_NOT_IN_FROZEN_CORPUS"):
        require_enrollment_admission(
            drill_runtime.participant_id, drill_runtime.drill_windows, admission
        )
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest backend/tests/test_attack_drill.py -v`
Expected: FAIL — `no such table: drill_sessions`.

- [ ] **Step 3: Implement**

```sql
-- backend/app/storage/migrations/0004_attack_drill.sql
-- A declared attacker drill (PLAN.md Section 13.3). Session-grained: a drill
-- IS a session. Recording it here rather than on feature_windows keeps the
-- protocol-generated FeatureWindow and every existing table untouched, and
-- gives every corpus loader one place to filter.
CREATE TABLE drill_sessions (
    session_id      TEXT PRIMARY KEY REFERENCES sessions(session_id) ON DELETE CASCADE,
    drill_label     TEXT NOT NULL CHECK(length(drill_label) > 0),
    declared_at_utc TEXT NOT NULL,
    schema_version  TEXT NOT NULL
);
```

```python
# tools/collection/repository.py :: load_window_summaries
            SELECT window_id, user_id, session_id, segment_id, t_start_us, t_end_us,
                   quality_label, key_event_count, mouse_event_count, collection_day,
                   provenance, keyboard_features_json, mouse_features_json, context_json,
                   schema_version, stored_at_utc
            FROM feature_windows
            -- Declared attacker-drill sessions are observed and scored but are
            -- never corpus data. Filtering here closes health, build_freeze,
            -- verify_freeze, and load_frozen_corpus in one place.
            WHERE session_id NOT IN (SELECT session_id FROM drill_sessions)
            ORDER BY user_id, stored_at_utc, window_id
```

In `RuntimeOrchestrator.__init__`, store `self._drill_label = drill_label`. Then:

- `start_authenticated_session`: when `self._drill_label is not None`, call
  `self.storage.declare_drill_session(session_id, self._drill_label)` and **skip**
  `self.enforcement.record_authenticated_entry(...)`.
- `_on_lifecycle`, `SEGMENT_ENDED` branch: skip `self._submit_segment_candidate(...)`
  when `self._drill_label is not None`.
- `_progress_state`: the guard already added in Task 4.
- `_process_window_inner`: skip `self.context_layer.observe_genuine(...)` (Task 7
  replaces this with the explicit suspension flag).

`--drill-label` is added to `backend/app/runtime/cli.py` with help text that says
plainly: *"Declare this session an attacker drill. Its windows are scored and
enforced but are permanently excluded from every training corpus. Never use it
for genuine collection."*

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest backend/tests/test_attack_drill.py tools/collection/tests/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/storage/migrations/0004_attack_drill.sql backend/app/storage/service.py backend/app/runtime/ tools/collection/repository.py backend/tests/test_attack_drill.py
git commit -m "feat: represent attacker drills as sessions and exclude them from every training corpus

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: Suspend context-confidence learning during a drill

**Files:**
- Modify: `backend/app/risk/context.py` (`suspend_learning`)
- Modify: `backend/app/runtime/orchestrator.py` (set it when a drill starts)
- Test: `backend/tests/test_context_confidence.py`, `backend/tests/test_attack_drill.py`

**Interfaces:**
- Consumes: `RuntimeOrchestrator._drill_label` from Task 6.
- Produces: `ContextConfidenceLayer.suspend_learning(enabled: bool) -> None`.

- [ ] **Step 1: Write the failing test**

```python
def test_context_learning_is_suspended_during_a_drill(drill_runtime) -> None:
    """In-memory only, so it cannot reach a model -- but it distorts the
    drill's own measurement, which is the number the drill exists to produce."""
    before = dict(drill_runtime.context_layer.statistics())
    drill_runtime.feed_attacker_session_with_low_risk_windows()
    assert dict(drill_runtime.context_layer.statistics()) == before
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest backend/tests/test_attack_drill.py -k context -v`
Expected: FAIL — statistics changed.

- [ ] **Step 3: Implement**

```python
# backend/app/risk/context.py
    def suspend_learning(self, enabled: bool) -> None:
        """Stop accumulating genuine statistics without affecting assessment.

        `assess()` keeps using whatever was learned during genuine
        operation -- which is the correct comparison baseline for a drill.
        """
        self._learning_suspended = enabled

    def observe_genuine(self, *, user_id: str, shares, risk_score: float) -> None:
        if self._learning_suspended:
            return
        ...
```

Initialise `self._learning_suspended = False` in `__init__`, and call
`context_layer.suspend_learning(True)` from `RuntimeOrchestrator.__init__` when
`drill_label is not None`.

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest backend/tests/test_context_confidence.py backend/tests/test_attack_drill.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/risk/context.py backend/app/runtime/orchestrator.py backend/tests/
git commit -m "feat: suspend context-confidence learning for the duration of an attacker drill

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: Explicit update refusal and guardrail G12

**Files:**
- Modify: `tools/updates/run.py::run_update` (early refusal), module docstring
- Modify: `tools/guardrails/check.py` (G12)
- Test: `tools/updates/tests/test_run.py`, `tools/guardrails/tests/test_guardrails.py`

**Interfaces:**
- Consumes: `UpdateRunOutcome` (unchanged).
- Produces: `UpdateRunOutcome(run_id="", status="SKIPPED", code="UPDATE_REQUIRES_IMPOSTOR_COHORT", profile=None)`.

- [ ] **Step 1: Write the failing tests**

```python
def test_update_refuses_a_single_participant_corpus_before_training(
    single_participant_update_fixture, monkeypatch
) -> None:
    trained: list[str] = []
    monkeypatch.setattr(
        isolation_forest,
        "train_user_profile",
        lambda *a, **k: trained.append("trained"),
    )
    outcome = run_update(**single_participant_update_fixture)
    assert outcome.status == "SKIPPED"
    assert outcome.code == "UPDATE_REQUIRES_IMPOSTOR_COHORT"
    assert trained == []          # refuses BEFORE writing any artifact


def test_guardrail_g12_requires_the_drill_exclusion() -> None:
    findings = check_drill_exclusion(
        "tools/collection/repository.py",
        "SELECT window_id FROM feature_windows ORDER BY user_id",
    )
    assert findings and findings[0].rule == "G12_DRILL_EXCLUSION"
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tools/updates/tests/test_run.py tools/guardrails/tests/ -v`
Expected: FAIL — a generic `ValueError` from inside `build_candidate`; `check_drill_exclusion` undefined.

- [ ] **Step 3: Implement**

```python
# tools/updates/run.py :: run_update -- before constructing the UpdateManager
    validation_corpus = corpus_module.load_frozen_corpus(database, manifest, "VALIDATION")
    if len(validation_corpus.windows_by_user) < 2:
        # A scheduled update may only replace an active profile when it is
        # shown not to have raised FAR, and FAR needs impostor evidence. The
        # drill is this project's only impostor evidence and is deliberately
        # excluded from every corpus, so that showing is impossible here.
        # Refusing is the correct answer; the alternative would be relaxing
        # a safeguard to make a disabled feature run (PLAN.md P7).
        return UpdateRunOutcome("", "SKIPPED", "UPDATE_REQUIRES_IMPOSTOR_COHORT", None)
```

```python
# tools/guardrails/check.py
_DRILL_EXCLUSION = re.compile(r"drill_sessions")

def check_drill_exclusion(path: str, text: str) -> list[Finding]:
    """G12: corpus loaders must exclude declared attacker-drill sessions.

    A regex cannot prove the filter is correct -- backend/tests/test_attack_drill.py
    does that. This catches the filter being deleted.
    """
    if "FROM feature_windows" not in text or _DRILL_EXCLUSION.search(text):
        return []
    return _finding(
        "G12_DRILL_EXCLUSION", path, "corpus loader does not exclude drill_sessions"
    )
```

Register it in `scan_repository` for `tools/collection/repository.py` and
`tools/collection/corpus.py`, and update the `main()` banner to `G01-G12`.

Rewrite the `tools/updates/run.py` module docstring to lead with the refusal
and its reasoning.

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tools/updates/tests/ tools/guardrails/tests/ -v && python tools/guardrails/check.py`
Expected: PASS; `guardrails passed: G01-G12`.

- [ ] **Step 5: Commit**

```bash
git add tools/updates/run.py tools/guardrails/check.py tools/updates/tests/ tools/guardrails/tests/
git commit -m "feat: refuse single-participant updates explicitly and guard the drill exclusion

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: ADR-014 and the documentation set

**Files:**
- Modify: `PLAN.md` (ADR-014, §20 Document Control, §13.2/§13.5 amendments)
- Modify: `docs/evaluation.md`, `docs/architecture.md`, `DATA_COLLECTION_GUIDE.md`, `guide.md`, `config/collection.pilot.yaml` comment, `tools/demo/bootstrap_first_model.py` docstring
- Create: `docs/pilot/attack-drill-protocol.md`

**Interfaces:** none — documentation only.

- [ ] **Step 1: Write ADR-014**

Insert after ADR-013 in `PLAN.md`, following the existing ADR format exactly
(Status / Context / Decision / Consequences / Rejected alternative). It must state:

- **Status:** Accepted, 2026-09-09. Supersedes the cohort assumption in §9.3 for this build.
- **Context:** §9.3 planned an 8–15 person cohort; the delivered study has one participant. The implementation made cross-participant FAR a precondition of activation (spec §8.4), which for N=1 makes activation unreachable and would force enrolling the attacker as a participant — inverting the experiment.
- **Decision:** First-profile activation requires a measured FRR and permits an explicitly unmeasured FAR. Cross-participant I1 evaluation is optional research functionality, separate from activation. The live attacker drill is the primary impostor evidence.
- **Consequences — stated plainly, not softened:** no I1 cross-user matrix; no cross-user FAR; no per-user metric distribution; no cohort confidence intervals; §13.5's statistical-honesty requirements must be rewritten around N=1 and a small number of live trials. Every reported result must carry N and the drill count adjacent to it. Automatic model updates are disabled, so E1 and E2 remain unrun.
- **Rejected alternative:** enrolling a second participant to satisfy the activation code. Rejected because it would place the impostor's data inside the frozen corpus, making them a cohort member rather than an unseen attacker, and would change the experiment to fit the implementation.

Add a `v3.0` row to the §20 Document Control table stating: *current assumption → new evidence → decision → resulting plan update*, per §20's own requirement.

- [ ] **Step 2: Write the drill protocol**

`docs/pilot/attack-drill-protocol.md`, leading with the freeze-ordering rule:

```markdown
## Non-negotiable ordering

1. Stop collection.
2. Freeze the corpus.            ← BEFORE any attacker touches the machine
3. Activate the profile.
4. Observe the shadow period.
5. Enable enforcement.
6. Start the backend WITH --drill-label.
7. Attacker operates.
8. Stop; export the risk trace.

Never create a freeze that includes drill sessions. `load_window_summaries`
excludes them and guardrail G12 protects that filter, but the ordering above
is what makes the guarantee auditable rather than merely enforced.
```

- [ ] **Step 3: Correct the day guidance**

`DATA_COLLECTION_GUIDE.md` currently says "Minimum useful: **3** distinct days… Plan for 5";
`guide.md` says "at least 2 different calendar days". Replace both with the
post-Task-2 policy and make them agree, citing
`config/ml.development.yaml → enrollment.min_train_distinct_days` and
`config/collection.pilot.yaml → freeze.min_distinct_days` by name so a reader
can see which is which. Add the `--drill-label` command and a "freeze before
drill" line to the guide's operational sections.

- [ ] **Step 4: Verify**

Run: `python tools/guardrails/check.py && python -m pytest`
Expected: PASS. Re-read ADR-014 and confirm it states what is given up, not only what is gained.

- [ ] **Step 5: Commit**

```bash
git add PLAN.md docs/ DATA_COLLECTION_GUIDE.md guide.md config/collection.pilot.yaml tools/demo/bootstrap_first_model.py
git commit -m "docs: record ADR-014 single-participant enrollment with unseen live attacker

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 10: Pre-drill verification suite

**Files:**
- Modify: `backend/tests/test_runtime_integration.py` (the end-to-end rehearsal)
- Create: `docs/pilot/pre-drill-checklist.md`

**Interfaces:**
- Consumes: everything from Tasks 1–9.

- [ ] **Step 1: Write the failing rehearsal test**

```python
def test_synthetic_takeover_escalates_through_the_ladder(activated_runtime) -> None:
    """Rehearse the drill end to end before a person is involved.

    Drives tools/synthetic --scenario takeover through the full runtime with
    a trained profile and enforcement enabled against stub adapters.
    """
    corpus_before = load_window_summaries(activated_runtime.database_path)
    trace = activated_runtime.run_synthetic_takeover(drill_label="rehearsal-01")

    assert DecisionAction.SOFT_CHALLENGE in trace.actions
    assert DecisionAction.REAUTH in trace.actions
    assert trace.first_high_risk_window_index is not None
    assert activated_runtime.user_state() is UserState.ACTIVE   # never stuck DEGRADED

    corpus_after = load_window_summaries(activated_runtime.database_path)
    assert {w.window_id for w in corpus_after} == {w.window_id for w in corpus_before}
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest backend/tests/test_runtime_integration.py -k takeover -v`
Expected: FAIL — the helper does not exist yet.

- [ ] **Step 3: Implement the harness**

Build `run_synthetic_takeover` on the existing `tools/synthetic --scenario takeover`
generator and the existing integration fixtures. Stub the enforcement adapters so
no real prompt is spawned and no workstation is locked; assert on `ActionOutcome`
records rather than OS side effects.

- [ ] **Step 4: Run the full gate**

```powershell
python protocol/codegen/generate.py --check
python tools/guardrails/check.py
python -m pytest
python -m mypy backend/app ml tools
```
Expected: all PASS. All 30 tests from §10 green.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_runtime_integration.py docs/pilot/pre-drill-checklist.md
git commit -m "test: rehearse the attacker drill end to end and assert corpus immutability

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

# PART III — SELF-REVIEW

**Spec coverage.** All twelve requested items are covered: lifecycle (§1), the ten change areas (§2.1–2.10 → Tasks 1–9), the four-way classification (§3), the 4-day policy decision (§4, Task 2), FAR behaviour (§5, Tasks 1 and 3), the drill invariant and its representation (§6, Tasks 6–7), DEGRADED recovery (§7, Task 5), CALIBRATING (§8, Task 4), the update decision (§9, Task 8), the pre-drill tests (§10, Task 10), implementation order (§11), and the dataset verdict (§12).

**Placeholder scan.** No TBDs. Every code step carries the actual code. Every test step carries the actual assertions. The one place a value is left open — `min_train_distinct_days` — is deliberately presented as a policy sentence requiring human approval, with the recommended value and its justification stated, per the instruction not to change a threshold merely to make the current dataset pass.

**Type consistency.** `ValidationMetrics.false_acceptance_rate: float | None` and `ValidationReport.operating_point: str` are introduced in Task 1 and used with those exact names in Tasks 3 and 8. `EnrollmentAdmission.min_train_windows` / `.min_train_distinct_days` are introduced in Task 2 and used with those names in Task 3. `_measure_validation_metrics` returns `tuple[ValidationMetrics, str]` in Task 3 and is unpacked as such at its one call site. `RiskEngine.component_recovered()` changes from `-> None` to `-> StateTransition | None` in Task 5 and both call sites handle the return. `RuntimeOrchestrator._drill_label` is referenced by the Task 4 guard and defined in Task 6 — Task 4's guard is written to be inert until then, and the ordering note in §11 records that dependency.

**Known cross-task ordering hazard.** Task 4's `_progress_state` guard references `self._drill_label`, which Task 6 defines. If Tasks 4 and 6 are executed by separate agents in parallel, Task 4 must define `self._drill_label = None` in `__init__` itself and Task 6 must then only change how it is populated. Execute them in the §11 order and this does not arise.
