# PILOT as the Default End-to-End Workflow — Design Specification

**Date:** 2026-09-03
**Status:** Proposed. Awaiting human review.
**Scope:** Make `PILOT` the default provenance for normal data-collection runs and
complete the PILOT lifecycle through evaluation, training, promotion, and deployment.

---

## 1. Purpose

The project can currently collect real participant data and store it correctly, but the
lifecycle around that data is incomplete: real data cannot legitimately reach model
training, there is no supported loader from the pilot database into the evaluation
pipeline, the A3 verification anchor exists only as an uncalled primitive, and several
documents and code paths still describe normal collection as synthetic.

This specification defines the complete set of changes required to run a five-day human
pilot and then evaluate and train from its output, without removing, weakening,
bypassing, or disabling any existing research or data-integrity guardrail.

## 2. Constraints honoured

Every change in this document is bound by `AGENTS.md`:

1. No application-specific model, integration, or monitoring path is introduced.
2. No typed content, key identity, window title, path, URL, clipboard, or screen data is
   added to any record, log, or API surface.
4. No time-of-day or day-of-week feature is added. The A3 prompt cadence is an
   operational schedule for verification evidence, not a model input, and never reaches
   `ml/features/`.
5. No path disables monitoring or reduces effective confidence to zero.
6. No training data is admitted outside an explicit, reviewed gate. Section 5 adds a
   second gate; it does not remove or soften the existing one.
7. No tunable threshold, weight, cadence, window size, or quality limit is hardcoded
   outside `config/`.
8. No participant data, credential, database, audit log, or model artifact is committed.
9. No model loads across a feature-schema version mismatch.
11. No ADR is changed and no `[OPEN]` item is closed without explicit human approval.
    Section 5.4 drafts ADR-013 for approval; drafting it does not approve it.

Additionally, per the review that produced this specification: existing synthetic data
and synthetic/development functionality are not deleted, invalidated, or modified.

## 3. Organising principle

**The storage profile is the only place provenance is set.**

`StorageSettings.collection_provenance` (`backend/app/storage/config.py`) derives
provenance from the store's own `data_policy`: `SYNTHETIC_ONLY` yields `SYNTHETIC`,
`APPROVED_COLLECTION` yields `PILOT`. There is no second parameter, flag, or environment
variable through which a run could disagree with its store.

This is what makes the requirements structural rather than conventional:

- A normal run defaults to `config/storage.pilot.yaml` and therefore records `PILOT`.
- Recording synthetic data requires explicitly passing the development profile.
- `StorageService._enforce_provenance` refuses mismatches in **both** directions, so an
  approved-collection store cannot accept synthetic records and a development store
  cannot accept participant records.
- `protocol/schemas/contracts.schema.json` enforces
  `DEVELOPMENT => SYNTHETIC_ONLY` and `PILOT | EVALUATION => APPROVED_COLLECTION` at the
  contract level, so a hand-edited profile cannot express the unsafe combination.
- The two profiles use different root directories, so the pilot corpus and the existing
  synthetic corpus never mix and the synthetic corpus is never overwritten.

No change in this specification introduces an alternative way to set provenance.

## 4. Already PILOT-ready

The following is implemented and verified in the working tree. It is listed so the
implementation plan does not redo it.

| Area | State |
| --- | --- |
| Provenance derivation | `StorageSettings.collection_provenance` derives from `data_policy`; no second source exists |
| Pilot storage profile | `config/storage.pilot.yaml`: `PILOT` / `APPROVED_COLLECTION`, separate root directory, 120-day retention, `pilot_mode: true`, raw debug capture forbidden |
| Storage enforcement | `_enforce_provenance` rejects mismatches in both directions |
| Runtime entry point | `create_collection_application` takes `participant_id` and never chooses provenance |
| CLI default | `--storage-config` defaults to `config/storage.pilot.yaml`; `--participant-id` added with `--synthetic-user` retained as a compatibility alias |
| Contract constraints | Environment/policy and `pilot_mode`/raw-capture rules enforced in `contracts.schema.json` |
| Deployment | `run-development.ps1 -ParticipantId` defaults to the pilot profile; `package.ps1` ships `storage.pilot.yaml`; `install.ps1` states which profile is default |
| Collection administration | Consent and enrollment records, pseudonym validation, eligibility reasons, aggregate health report, write-once day-disjoint freeze manifest, evaluation freeze lock |
| Pilot documentation | `docs/pilot/README.md`, `docs/pilot/operator-checklist.md`, `docs/pilot/participant-brief-template.md` |
| Regression cover | `backend/tests/test_provenance_flow.py` covers both directions and the default |
| Synthetic preserved | `config/storage.development.yaml`, `config/synthetic.development.yaml`, `tools/synthetic/`, and the existing synthetic corpus are untouched |

## 5. The promotion gate

### 5.1 Why PILOT data is currently unable to reach training

`ml/training/gate.py::require_promotion_gate` requires that every `TEAM` or `PILOT`
window's segment carry a `PROMOTED` `UpdateCandidate` with all six gates passed. For a
user's **first** model this is unsatisfiable by construction, not merely inconvenient:

- **G3 counts scored windows.** `RuntimeOrchestrator._submit_segment_candidate` computes
  `scored_windows` as the number of score rows where a modality is `available`. Scoring
  requires an ACTIVE profile: `DirectoryProfileProvider.__call__` returns `None` when no
  ACTIVE row exists in `model_profiles`, so every modality score is `available=false` and
  `scored_windows` is `0`. `submit_segment` therefore marks every enrollment-era segment
  `REJECTED`.
- **The state machine cannot advance either.** `_progress_state` returns immediately when
  `self._profile_available` is false, so the user never leaves `ENROLLING`.
- **G5 and G6 are seven-day clocks**, unreachable inside a five-day collection round.

The consequence is circular: no model means no available scores, which means no
promotable candidate, which means the promotion gate blocks training, which means no
model. `tools/demo/bootstrap_first_model.py` documents this circularity in its own
docstring and works today only because synthetic provenance skips the gate entirely.

The offline path is blocked by the same gate: `ml/evaluation/pipeline.py` calls its
trainers without `promoted_candidates`, so a frozen PILOT corpus would freeze
successfully and then raise `PromotionGateRequiredError` at evaluation.

### 5.2 Why this is a category error, not a defect in the gate

PLAN.md Section 12 is titled *Model Update Manager* and opens with adaptation of an
existing profile: "Behavior genuinely drifts... Profiles must adapt", and Section 12.3
requires the retrained model be evaluated "before replacing the active model". The gate
presupposes an active profile and a baseline to regress against.

PLAN.md Section 5.3 describes the first model as an enrollment transition:
`ENROLLING -> CALIBRATING` on "min_windows reached AND min_distinct_days reached", at
which point the model is trained. It is not described as a promotion and has no
promotion evidence available to it.

`require_promotion_gate` currently applies the update gate to enrollment training. That
is the defect. The resolution is to give enrollment its own explicit gate, not to relax
the update gate.

### 5.3 Design: a separate enrollment admission gate

Add `ml/training/enrollment.py`:

```python
@dataclass(frozen=True)
class EnrollmentAdmission:
    settings: CollectionSettings
    consent: ConsentRecord | None
    enrollment: EnrollmentRecord | None
    manifest_window_ids: frozenset[str]
    observed_at_by_window: Mapping[str, datetime]
    min_windows: int
    min_distinct_days: int
    user_has_active_profile: bool


class EnrollmentAdmissionError(ValueError):
    """Raised when first-profile training data lacks complete admission evidence."""


def require_enrollment_admission(
    user_id: str,
    windows: Sequence[FeatureWindow],
    admission: EnrollmentAdmission,
) -> None:
    ...
```

All conditions are mandatory and none is bypassable:

| Condition | Failure code | Source of truth |
| --- | --- | --- |
| Every window belongs to `user_id` | `FOREIGN_USER_WINDOW` | window records |
| Provenance in `eligible_provenance`, never `SYNTHETIC` | `INELIGIBLE_PROVENANCE` | `config/collection.pilot.yaml` |
| Active consent covering each window's `observed_at` | `MISSING_CONSENT`, `CONSENT_NOT_ACTIVE` | `tools/collection/eligibility.py`, reused unchanged |
| Enrollment record present and predating each window | `MISSING_ENROLLMENT`, `BEFORE_ENROLLMENT` | same |
| Every `window_id` present in a checksum-verified freeze manifest | `WINDOW_NOT_IN_FROZEN_CORPUS` | `tools/collection/freeze.py::verify_freeze` |
| At least `min_windows` admitted windows | `INSUFFICIENT_WINDOWS` | `config/risk.*.yaml` `enrollment` block |
| At least `min_distinct_days` distinct `collection_day` values | `INSUFFICIENT_DISTINCT_DAYS` | same |
| The user has no ACTIVE profile | `PROFILE_ALREADY_ACTIVE` | queried from `model_profiles`, never asserted by the caller |

`FeatureWindow` carries `collection_day` but no wall-clock timestamp, while
`eligibility_reasons` needs an `observed_at` to evaluate consent activity. Rather than
inferring one from `collection_day` — which would silently assume the participant's local
date equals UTC — `observed_at_by_window` is supplied by the loader in Section 8.2 from
the same `stored_at_utc` column `load_window_summaries` already uses. A window absent from
that mapping fails admission with `MISSING_OBSERVATION_TIME`; it is never defaulted.

The final condition is what confines this gate to first-profile training. Once a profile
is active, this gate refuses and `require_promotion_gate` is the only remaining route,
so the Model Update Manager governs every subsequent change to a user's training data
exactly as it does today.

`ml/training/common.py::train_one_class_model` gains a keyword-only
`enrollment_admission: EnrollmentAdmission | None = None` and enforces:

- Windows whose provenance is entirely `SYNTHETIC` or `PUBLIC`: neither boundary
  required. Unchanged behaviour; synthetic and public development paths are unaffected.
- Any `TEAM` or `PILOT` window present: **exactly one** of `promoted_candidates` or
  `enrollment_admission` must be supplied. Supplying neither raises
  `PromotionGateRequiredError` as today. Supplying both raises `ValueError`, because an
  ambiguous admission route is how a boundary silently becomes optional.

There is deliberately no fallback: absence of `promoted_candidates` never implies
enrollment mode. The caller must name the route it is using.

`ml/training/isolation_forest.py`, `ml/training/single_fused_model.py`,
`ml/baselines/mahalanobis.py`, and `ml/baselines/alt_one_class.py` forward the new
keyword. All parameters are additive with defaults, so existing callers and public
signatures continue to work.

### 5.4 ADR-013 (draft, requires human approval)

To be added to PLAN.md Section 6 and cross-referenced from Sections 5.3 and 12.1. This
text is a proposal. It is not approved by its appearance here, and no code depending on
it may merge before a human approves it.

> **ADR-013 — Enrollment Admission Is Distinct From Update Promotion**
>
> **Status:** Proposed.
>
> **Context.** The Model Update Manager promotion gate (Section 12.1) admits new data
> into the training set of a profile that already exists. Gate G3 counts scored windows,
> and scoring requires an active model, so no first profile can ever satisfy it. Applying
> the promotion gate to enrollment therefore makes per-user models unbuildable from real
> data, which contradicts Section 5.3 and Section 9.6.
>
> **Decision.** A user's first profile is admitted by a distinct enrollment admission
> gate requiring eligible provenance, active consent, a recorded enrollment, membership
> of a checksum-verified frozen corpus, the configured minimum window and distinct-day
> counts, and the absence of any active profile for that user. Every subsequent change to
> a user's training data continues to require the complete G1-G6 promotion gate,
> unchanged.
>
> **Consequences.** Enrollment data is admitted against consent and corpus-integrity
> evidence rather than against risk-and-verification evidence that cannot exist yet. The
> poisoning surface the promotion gate protects is unchanged, because enrollment happens
> once per user, from an immutable frozen corpus, before any enforcement exists to
> subvert. The freeze manifest becomes a security boundary as well as a research one.
>
> **Rejected alternative.** Redefining G3 as "windows in segment" for offline use. This
> weakens the promotion gate for every user and every update in order to solve a
> first-model problem, and hides the weakening inside a gate that still claims six
> passing conditions.

### 5.5 Guardrail changes

`tools/guardrails/check.py::check_training_gate` (G07) currently flags any file
containing a `.fit(`/`.partial_fit(`/`train_model(` call that does not mention
`require_promotion_gate`. It is extended, not relaxed:

- G07 accepts a file referencing `require_promotion_gate` **or**
  `require_enrollment_admission`.
- A new assertion requires that `ml/training/common.py` reference **both** names, so
  neither boundary can be deleted or bypassed without the guardrail failing.

## 6. Quarantine and retraining cadence versus a five-day round

### 6.1 Mechanics

- **G5, `quarantine_days: 7`.** `UpdateManager.reassess` sets `g5_quarantine` only when
  `now >= quarantined_at + 7 days`, where `quarantined_at` is the segment's completion
  time. Segments from collection days 1 through 5 become quarantine-clear on days 8
  through 12 respectively.
- **G6, `retraining_cadence_days: 7`.** `SQLiteUpdateRepository.schedule_due` returns
  `True` when no `ACTIVATED` run exists, so the *first* scheduled run is not blocked by
  cadence. Every later run requires seven days since the last activation.

### 6.2 Consequence

**No live promotion can occur during a five-day pilot, and that is correct behaviour.**
The pilot collects; the Model Update Manager acts afterwards. This is not a defect to be
configured away.

### 6.3 Decision: the values do not change

`quarantine_days` and `retraining_cadence_days` remain `7`. Reducing them so that a
five-day schedule can produce promotions would weaken a guardrail purely to make the
pipeline run, which this work explicitly forbids. The calendar absorbs the constraint
instead:

| Day | Activity |
| --- | --- |
| 1-5 | Collection. Segments accumulate as `QUARANTINED` or `REJECTED` candidates. No promotion is expected or required. |
| 5+ | Aggregate health review, then create the write-once freeze manifest. Freezing has no quarantine dependency. |
| 5+ | First profile per participant via the enrollment admission gate (Section 5.3), trained on the TRAIN partition only. |
| 12+ | Earliest date on which every collection day's segments are quarantine-clear, and therefore the earliest honest first *update* run. |

Pilot retention of 120 days covers this comfortably. The development profile's 7-day
retention would have deleted day 1 before the freeze, which is why the pilot profile
exists as a separate file rather than a flag on the development one.

## 7. Required before collection starts

### 7.1 A3 scheduled verification anchors

`EnforcementCoordinator.record_scheduled_verification` exists and creates
`A3_SCHEDULED_PROMPT` records, but has no caller, no scheduler, and no endpoint. Without
it, the only anchor available is the A1 entry anchor, which
`RuntimeOrchestrator._submit_segment_candidate` attaches to the first segment of a
session only. Every later segment in a session fails G2 permanently. Anchors cannot be
reconstructed after collection, so this must exist before day 1.

**Cadence configuration.** Two values currently describe this concept and disagree:

- `config/updates.development.yaml` `update_manager.scheduled_anchor_interval_seconds:
  14400` (4 h) is declared in `contracts.schema.json` and loaded into `UpdateSettings`,
  but has no consumer anywhere in the repository.
- `config/collection.development.yaml` `collection.scheduled_anchor_interval_hours: 24.0`
  drives `tools/collection/eligibility.py::next_scheduled_anchor_due`, which is itself
  uncalled.

Resolution: the runtime prompt cadence is `update_manager.scheduled_anchor_interval_seconds`,
because the backend already loads `UpdateSettings` and does not load collection settings.
`collection.*.yaml`'s `scheduled_anchor_interval_hours` remains the operator-side value
used by collection tooling, and a test asserts the two express the same interval so they
cannot drift apart again.

Both are set to **8 hours** (`28800` seconds; `8.0` hours) in the new pilot configs. This
is an engineering placeholder in the same sense as every other value in `config/`, not an
approved protocol parameter, and requires human sign-off before collection. Rationale for
the proposed value: PLAN.md Section 12.2 describes A3 as "a deliberate low-frequency
prompt" whose burden is "small, predictable, infrequent"; eight hours yields roughly one
to two prompts per working day, which is enough to anchor most segments while remaining
defensible to participants. The existing 4-hour value is more burdensome than Section
12.2 describes, and 24 hours would anchor too few segments to support the E1 experiment.

**Components:**

1. `backend/app/updates/anchors.py`: a scheduler holding the last successful anchor time
   per active session and deciding when a prompt is due, using the configured interval.
   It emits a prompt request; it never fabricates an anchor.
2. Runtime integration in `RuntimeOrchestrator`: check due-ness on the existing
   heartbeat path, dispatch through the existing `ChallengeService` so the participant
   answers the security challenge they configured, and on a correct answer call
   `record_scheduled_verification` with the current session and segment.
3. API: extend the existing `/v1/enforcement/challenge` surface rather than adding a
   parallel one. The scheduled prompt reuses the configured challenge credential and the
   existing response endpoint, so no new credential path and no new secret storage is
   introduced. `protocol/schemas/api.openapi.yaml` is updated first, then the generator
   is run and the generated Python and C++ bindings are committed in the same change.
4. Dashboard: surface a pending scheduled verification in the existing challenge UI
   (`dashboard/src/views/ChallengeForm.tsx`), not as a new view.

A wrong or expired answer records the failed outcome and creates no anchor, matching the
existing A2 behaviour. Continuous low risk is never an anchor.

### 7.2 `config/collection.pilot.yaml`

New file. `config/collection.development.yaml` is left untouched.

```yaml
config_version: collection-pilot-unreviewed-v1
protocol_version: 1.0.0

collection:
  target_collection_days: 5
  min_windows_per_day: 50
  min_full_modality_fraction: 0.40
  max_observed_gap_hours: 24.0
  scheduled_anchor_interval_hours: 8.0
  eligible_provenance: [PILOT]

freeze:
  min_distinct_days: 3
  training_fraction: 0.60
  validation_fraction: 0.20
  evaluation_fraction: 0.20
```

`target_collection_days: 5` matches the planned round, so `build_health_report` stops
reporting `COLLECTION_DAYS_BELOW_TARGET` for every participant and the shortfall signal
stays meaningful. `min_distinct_days` stays at 3: five days partitions to a valid
day-disjoint 2/2/1 split under `_partition_days`. `eligible_provenance: [PILOT]` narrows
this round to pilot participants; the development file keeps `[TEAM, PILOT]`. The
remaining values are unchanged placeholders carried over for human review, and
`config_version` says so.

### 7.3 `config/updates.pilot.yaml`

New file, mirroring the storage-profile pattern so `updates.development.yaml` is not
edited. Identical to the development file except `scheduled_anchor_interval_seconds:
28800`. `quarantine_days` and `retraining_cadence_days` remain `7`, per Section 6.3.
`backend/app/runtime/cli.py` defaults `--updates-config` to this file.

### 7.4 Provenance mislabelling in the session entry anchor

`backend/app/runtime/application.py` starts every session with
`evidence_reference=f"synthetic-entry-{secrets.token_urlsafe(32)}"`. In a pilot run this
stamps the word "synthetic" onto the A1 verification evidence of real participant data.
This is a data-integrity defect, not a cosmetic one: the anchor is the evidence a future
promotion decision rests on. It becomes provenance-neutral (`session-entry-`).

### 7.5 Operator-visible provenance indicator

Nothing in the API or dashboard reveals which store a running instance is writing to, so
an operator cannot confirm before day 1 that a run is recording `PILOT` rather than
`SYNTHETIC`. Add a read-only field reporting `storage.environment`, `storage.data_policy`,
and the derived collection provenance, sourced from `StorageSettings` so it cannot
disagree with reality, exposed through the existing status surface and rendered in the
dashboard header. No new configuration and no participant data is involved.

### 7.6 Documentation still describing normal collection as synthetic

| File | Change |
| --- | --- |
| `backend/README.md` | Replace the "synthetic-development process" walkthrough and `--synthetic-user` example with the pilot default; keep the synthetic invocation as the documented opt-in |
| `docs/deployment/windows.md` | `-SyntheticUser synthetic-user` example, and the claim that the deployment "stores `SYNTHETIC` provenance" and "must not be used for participant collection" |
| `startup.md` | Lines describing disposable synthetic state, synthetic-only supplied configuration, the `--synthetic-user` invocation, and the synthetic session narrative |
| `docs/architecture.md` | "Development launchers accept synthetic provenance only" no longer describes the default launcher |
| `docs/demo.md` | "Start the packaged synthetic runtime" |
| `docs/pilot/README.md` | Point the health and freeze examples at `config/collection.pilot.yaml` |
| `backend/app/runtime/application.py`, `backend/app/runtime/cli.py` | Module docstrings still say "synthetic-development" |
| `guide.md` | Replace the "Note for Manas: the promotion gate" section with the resolved workflow once ADR-013 is approved |

Every one of these keeps the synthetic path documented as an explicit, still-supported
option. None removes it.

### 7.7 Operational preconditions

`data/collection/administration.json` must contain an active consent record and an
enrollment record, under the assigned pseudonym, for every participant before their first
collection day. The enrollment gate, the health report, and the freeze all read it, and
`data/` remains git-ignored. This is human work covered by the existing operator
checklist; no code change is required.

## 8. Required after collection, before evaluation or training

### 8.1 Enrollment admission gate and ADR-013

Section 5. This is the gating item for everything else in this section.

### 8.2 Frozen-corpus loader

No supported path loads PILOT windows out of SQLite into `FeatureWindow` objects for
evaluation. The only implementation is the private `_load_windows` inside
`tools/demo/bootstrap_first_model.py`, which reads whatever is in the database with no
manifest check.

Add `tools/collection/corpus.py::load_frozen_corpus(database, manifest, partition)`:

1. Call `verify_freeze` first and propagate `FreezeError` unchanged. A corpus that no
   longer matches its manifest is never loaded.
2. Load only `window_id`s named in the manifest, so post-freeze windows cannot leak into
   a frozen result.
3. Return `dict[user_id, list[FeatureWindow]]` filtered to the requested partition, using
   the manifest's own `day_assignments`, so day-disjointness comes from the manifest
   rather than being recomputed.
4. Expose the manifest's window-id set for `EnrollmentAdmission.manifest_window_ids` and
   the `stored_at_utc` values for `EnrollmentAdmission.observed_at_by_window`, so the gate
   and the loader agree by construction rather than by convention.

`tools/demo/bootstrap_first_model.py` is left in place, unchanged, as the documented
synthetic/development bootstrap.

### 8.3 Evaluation pipeline

`ml/evaluation/pipeline.py` calls its trainers with no admission argument in
`run_baseline_comparison`, the fusion comparison, and the enrollment-length experiment,
so a PILOT corpus raises at the first training call. Each gains an optional
`admission: EnrollmentAdmission | None = None` parameter, threaded to every trainer.
Synthetic and public evaluation runs pass nothing and behave exactly as today.

### 8.4 First-profile activation

Add `tools/enrollment/` with a CLI that trains and activates a participant's first
profile from the frozen corpus:

- Trains on the **TRAIN** partition only.
- Passes `EnrollmentAdmission` built from the verified manifest, the administration
  records, and the collection and risk configs.
- Measures the activation `ValidationReport` honestly: FRR on the held-out **VALIDATION**
  partition, and FAR by zero-effort cross-evaluation against other participants' windows
  using the existing `ml/evaluation/cross_evaluation.py`. The **EVALUATION** partition is
  never read.
- Records `reason_code: ENROLLMENT_INITIAL_PROFILE_NO_BASELINE` to state plainly that no
  prior profile existed to regress against, rather than fabricating a baseline.
- Activates through `SQLiteUpdateRepository.activate_profile`, the same audited,
  single-transaction path the Model Update Manager uses.

This removes the need to run the demo bootstrap against participant data, and it is what
lets the live runtime leave `ENROLLING`, which in turn produces the available scores that
G3 needs for all subsequent updates.

### 8.5 Scheduled update runs

`UpdateManager.run_scheduled` has no production caller; only tests invoke it. Add a
`candidate_builder` and an operator-triggered entry point that loads the promoted
segments' windows, trains with `promoted_candidates`, and compares candidate against the
active profile on the VALIDATION partition plus cross-user impostors. This is the path
that makes the promotion gate operative rather than latent, and it is required before the
E1 experiment can run.

### 8.6 E1 and E2

`ml/experiments/update_manager.py` implements the calculations but has no driver over a
real corpus. Both experiments replay the update manager across the frozen corpus in date
order. They are evaluation work, not pipeline enablement, and depend on 8.2 through 8.5.

## 9. Optional cleanup

- `backend/app/compose.py` defaults the containerised path to
  `config/storage.development.yaml`. This is correct, because named-pipe ingestion is
  Windows-only and the container never collects, but it should be documented rather than
  silent. Overridable via `CA_STORAGE_CONFIG`.
- Retire the `--synthetic-user` alias after the pilot, once no script depends on it.
- Fold `tools/demo/bootstrap_first_model.py::_load_windows` into the shared loader from
  8.2 once the enrollment CLI supersedes it.
- `docs/demo.md` wording.

## 10. Testing

Following the repository rule that tests cover validation and failure behaviour, not only
the successful path, and using synthetic fixtures only.

| Area | Tests |
| --- | --- |
| Enrollment gate | One failing case per admission condition in Section 5.3; supplying both boundaries raises; supplying neither still raises `PromotionGateRequiredError`; a user with an ACTIVE profile is refused |
| Promotion gate | Existing `ml/tests` assertions unchanged, proving the update gate is not weakened |
| Guardrails | G07 fails when either boundary name is removed from `ml/training/common.py` |
| A3 anchors | A due prompt dispatches; a correct answer records exactly one `A3_SCHEDULED_PROMPT`; a wrong or expired answer records no anchor; the runtime and collection cadence values agree |
| Corpus loader | A tampered manifest raises; post-freeze windows are excluded; partitions stay day-disjoint |
| Configs | `collection.pilot.yaml` and `updates.pilot.yaml` validate; five days partition into three non-empty splits; `quarantine_days` and `retraining_cadence_days` are still 7 |
| Provenance | Existing `test_provenance_flow.py`, extended to cover the entry-anchor evidence reference no longer containing "synthetic" |
| Quarantine timing | A segment completed on day D is not eligible before D+7 and is eligible at D+7 |

`python tools/guardrails/check.py` plus the affected suites run before handoff.

## 11. Explicitly not changed

- `config/storage.development.yaml`, `config/collection.development.yaml`,
  `config/updates.development.yaml`, `config/synthetic.development.yaml`.
- `tools/synthetic/` and `tools/demo/`.
- `ml/training/gate.py::require_promotion_gate` — not one line.
- G1 through G6 semantics, `quarantine_days`, `retraining_cadence_days`,
  `regression_tolerance`, `min_promotable_windows`.
- The existing synthetic corpus on disk, which lives under a different root directory.
- Any existing public signature: every new parameter is keyword-only with a default.

## 12. Approval gates

1. **ADR-013 and the PLAN.md amendments (Sections 5.3 and 12.1 cross-references) require
   explicit human approval before any code in Section 5 or Section 8 merges.** AGENTS.md
   constraint 11.
2. **The 8-hour A3 cadence requires human sign-off before collection**, as an operational
   parameter affecting participants.
3. **`config/collection.pilot.yaml` values other than `target_collection_days` are
   inherited placeholders** and should be reviewed against the approved pilot protocol
   before day 1.
4. Recruitment, consent, installation support, A3 prompt completion, informed mimicry,
   and live takeover remain human-only and are not automated by anything here.
