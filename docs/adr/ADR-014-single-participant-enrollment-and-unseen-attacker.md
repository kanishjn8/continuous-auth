# ADR-014 — Single-Participant Enrollment With an Unseen Live Attacker

**Status:** Accepted, 2026-09-09. Recorded per `PLAN.md` §20.2 as a **major revision
(v3.0)**, because it changes the evaluation methodology.

**Supersedes for this build:** the cohort assumption in `PLAN.md` §9.3, and the
implicit assumption in the delivered activation code that a first profile can only be
activated once cross-participant FAR has been measured.

**Scope note:** this ADR changes *when and against what evidence a profile is
activated*, and *what counts as impostor evidence*. It changes no core principle in
`PLAN.md` §4, no privacy boundary, and no poisoning boundary. The G1–G6 promotion gate
(`ml/training/gate.py`) is untouched.

---

## Context

`PLAN.md` §9.3 planned an 8–15 person pilot cohort. In that design, each participant
supplies zero-effort impostor (I1) data for every other participant's model, so a
false-acceptance rate falls out of the corpus for free.

The delivered study has **one participant**. The intended experiment is different from
the one §9.3 describes and is materially closer to the stated threat model in §14.1:

- the model is trained **only** on the legitimate user's own behaviour;
- there is **no second enrolled participant**;
- after the profile is `ACTIVE` on the legitimate user's own machine, a classmate who
  has never contributed data acts as an **unseen attacker** in a live takeover.

The implementation, however, made cross-participant FAR a **precondition of
activation**. `tools/enrollment/activate.py` raised
`ENROLLMENT_VALIDATION_INSUFFICIENT` unless both genuine **and** impostor VALIDATION
scores existed, and `ml/evaluation/cross_evaluation.py` skips `other_id == user_id`, so
a single-participant corpus produces no impostor scores by construction.

For N=1 this makes activation unreachable. The only ways to satisfy the code as written
were both wrong:

1. **Enroll the attacker as a second participant.** This places the impostor's data
   inside the frozen corpus, makes them a cohort member rather than an unseen attacker,
   and inverts the experiment.
2. **Fabricate `FAR = 0.0`.** This reports a metric that was never measured, which is
   the precise failure the evaluation rules in `PLAN.md` §13.5 exist to prevent.

A third apparent option — collecting more days — resolves nothing: the day threshold
was separately mis-scoped (see *Decision 3* below), and no number of additional days
from a single participant can produce an impostor.

## Decision

1. **Initial enrollment is single-user.** A first profile may be trained, calibrated,
   validated, and activated from one participant's own data alone. A second enrolled
   participant is **not** required for activation.

2. **Initial training, calibration, and validation use only legitimate-user data.**
   `train_one_class_model` continues to raise on more than one distinct `user_id`, and
   the ADR-013 enrollment admission gate continues to be the sole training-data
   boundary for a first profile.

3. **FAR/EER requires impostor data and is therefore UNMEASURED for the initial
   profile when no cohort exists.** It is represented as
   `ValidationMetrics.false_acceptance_rate = None`, meaning *not measured*. It is
   **never** represented as `0.0`, never estimated, and never imputed. The reason code
   `ENROLLMENT_INITIAL_PROFILE_NO_IMPOSTOR_COHORT` and the free-form
   `ValidationReport.operating_point` string record why, adjacent to the number, in the
   same row of `model_profiles`.

4. **FAR is not an activation gate.** A first profile's acceptance criterion is the
   ADR-013 enrollment gate — eligible provenance, active consent, a recorded
   enrollment, membership of a checksum-verified freeze manifest, a recorded
   observation time per window, the configured TRAIN window and distinct-day minimums,
   and the absence of an existing active profile. There is no prior profile to regress
   against, so there is nothing for a FAR comparison to decide.

5. **FRR is measured at the deployed operating threshold.** Without impostors there is
   no Equal Error Rate and therefore no EER threshold, so FRR is measured at the
   operating point the live system actually uses.
   `backend/app/models/service.py` maps a normality percentile to risk as
   `calibrated_risk = 1 - percentile / 100`, and the escalation ladder's first rung is
   `risk.medium_threshold`. The equivalent percentile threshold is therefore
   `(1 - risk.medium_threshold) * 100`, and the reported FRR is the fraction of the
   participant's own **held-out VALIDATION** windows that the live risk engine would
   place at or above MEDIUM. Genuine VALIDATION scores remain **mandatory**: a corpus
   that cannot produce one fails activation with `ENROLLMENT_VALIDATION_NO_GENUINE`.
   That is a data problem, not a cohort problem.

6. **The live attacker drill is post-activation and is not training data.** The
   classmate operates the machine only after the corpus is frozen and the profile is
   `ACTIVE`. Drill sessions are declared with `--drill-label` and recorded in the
   `drill_sessions` table. Their windows are **scored, risk-assessed, enforced,
   alerted, audited, and streamed** — that is the entire point — but they are excluded
   from every corpus loader.

7. **Attacker data is excluded from all future model-update candidates.** A drill
   session suppresses update-candidate submission, the automatic A1 login anchor,
   context-confidence learning, and enrollment/calibration progress. It suppresses
   **nothing** about scoring, risk evaluation, risk-state transitions, escalation, or
   enforcement.

8. **Optional multi-participant cohort evaluation may later measure FAR/EER, but it is
   not required for first activation.** `ml/evaluation/cross_evaluation.py` and the I1
   path in `tools/enrollment/activate.py` remain in the codebase, unchanged and fully
   functional. If a cohort ever exists, both paths run and both metrics are measured;
   the two paths are selected purely by whether impostor scores were found — no flag,
   no config switch, no operator choice.

9. **Model updates remain disabled for this pilot unless explicitly re-enabled later.**
   See *Consequences* below.

10. **The dataset must be frozen BEFORE the attacker drill.** The freeze is the
    security boundary as well as the research boundary (ADR-013). Freezing first makes
    the guarantee *auditable by ordering* rather than merely *enforced by a filter*.

## Consequences

Stated plainly, including what is given up.

**Given up — permanently, for this build:**

- No I1 zero-effort cross-user matrix.
- No cross-user FAR, and therefore no EER, no ROC, and no DET curve.
- No per-user metric distribution, and no cohort confidence intervals.
- `PLAN.md` §13.5's statistical-honesty requirements must be read as N=1 requirements:
  cohort size is 1, and every reported number carries that fact adjacent to it.
- The I2 informed-mimicry experiment as specified in §13.2 needs a cohort member and is
  not run. The live drill is the closest available substitute and is weaker evidence,
  because a single classmate is one sample, not a distribution.
- E1 (drift benefit) and E2 (poisoning resistance) remain **unrun**, because both
  require the update pipeline, which is disabled (below).

**What replaces it:**

- The live attacker drill (`PLAN.md` §13.3) becomes the project's **primary impostor
  evidence**, reported as live security/robustness evidence with the number of drills
  and the number of scored windows stated adjacent to every claim.
- It is reported as a risk trajectory and a detection latency, **not as a FAR**. The
  statistical requirements for a false-acceptance rate are not satisfied by a handful
  of live sessions, and calling the result a FAR would be the same fabrication this ADR
  exists to prevent.

**Model updates are disabled for this pilot.**

An update may only replace an active profile when it is shown not to have raised FAR.
FAR needs impostor evidence; the drill is this project's only impostor evidence, and
the drill is excluded from every corpus by decision 7. A candidate's FAR is therefore
**unmeasurable by construction**, and `UpdateManager._validate` refuses with
`VALIDATION_FAR_UNMEASURED`. `tools/updates/run.py` refuses earlier still — before any
training runs or any artifact is written — with `UPDATE_REQUIRES_IMPOSTOR_COHORT`.

Refusing is the correct outcome, not a limitation to work around. The alternative would
be relaxing `quarantine_days`, `retraining_cadence_days`, `regression_tolerance`, or the
G1–G6 gates in order to make a disabled feature run, which would weaken the exact
safeguard `PLAN.md` §12 claims as a contribution (P7: adaptation must be *earned*).

**Note the asymmetry, which is deliberate:**

| Transition | May proceed with FAR unmeasured? | Why |
| --- | --- | --- |
| First profile → `ACTIVE` | **Yes** | `accepted` gates activation, and the acceptance criterion is the ADR-013 enrollment gate. There is no prior profile to regress against. |
| Update candidate → `ACTIVE` | **No** | `accepted` means "not worse than the active profile". With FAR unmeasured that comparison is undecidable. |

**Unchanged by this ADR:** the G1–G6 promotion gate, `require_enrollment_admission`'s
`user_has_active_profile` refusal, `quarantine_days: 7`,
`retraining_cadence_days: 7`, `regression_tolerance: 0.02`,
`min_promotable_windows: 10`, every privacy boundary, every protocol schema, and the
fail-open enforcement policy of ADR-011.

## Rejected alternatives

**1. Enroll a second participant to satisfy the activation code.** Rejected. It would
place the impostor's data inside the frozen corpus, making them a cohort member rather
than an unseen attacker. It changes the experiment to fit the implementation, which is
backwards.

**2. Write `FAR = 0.0` when unmeasured.** Rejected. A fabricated metric, indefensible
under §13.5, and indistinguishable downstream from a real perfect score.

**3. Collect additional collection days.** Rejected as a *solution*, because it
addresses neither blocker. The distinct-day threshold was mis-scoped (see below), and
no quantity of single-participant data produces an impostor.

**4. Lower the enrollment day threshold so the existing corpus passes.** Rejected as
framed. The defect was **scope conflation**, not a value that was too high: one config
key was answering two unrelated questions —

| Policy | Question | Home | Scope |
| --- | --- | --- | --- |
| Live state machine | "How much has the runtime observed before this user stops being `ENROLLING`?" | `config/risk.*.yaml → enrollment.*` | the user's whole observed history |
| Training admission | "How much of the frozen TRAIN partition must a first model be built from?" | `config/ml.*.yaml → enrollment.min_train_*` | the TRAIN partition only |

Feeding the first into the second silently turned a "3 distinct days" rule into an
undocumented "6 collection days" requirement, because a 60/20/20 day-disjoint split
gives TRAIN only about half the collected days. The two policies are separated. The
live threshold `risk.enrollment.min_distinct_days` **stays at 3**, unchanged. The new
training-admission policy is stated below and must stand on its own.

**5. Overload `sessions.entry_auth_evidence`, or add a drill flag to `FeatureWindow`.**
Rejected. `entry_auth_evidence` is the verification-anchor enum and overloading it would
corrupt G2's semantics. `FeatureWindow` is protocol-generated, so a field there forces
codegen and C++ struct changes and alters the freeze digest — a session-level fact
re-grained to window level, at maximum cost.

---

## The training-admission policy statement

> *A first profile must be trained on at least **two distinct calendar days**, so that a
> single day's posture, device position, or mood cannot alone define the identity
> baseline. Fewer than two days represents no cross-day variation at all. More than two
> is not currently justified by evidence: `PLAN.md` §10.5's enrollment-length
> experiment (open decision O3) has not been run, and choosing a higher number would be
> an assumption dressed as a threshold.*

Configured as `config/ml.*.yaml → enrollment.min_train_distinct_days: 2`, with
`min_train_windows: 20`, marked provisional pending §10.5.

This sentence must be readable and agreeable **without knowing how much data exists**.
That the current four-day corpus satisfies it is an *outcome* of the policy, not its
purpose.

## Verified consequence for the delivered corpus

`tools/collection/freeze.py::_partition_days` executed against
`config/collection.pilot.yaml` (reserve one day per partition, then distribute
60/20/20):

| Collection days | TRAIN | VALIDATION | EVALUATION | Admissible at `min_train_distinct_days: 2` |
| --- | --- | --- | --- | --- |
| 3 | 1 | 1 | 1 | no |
| **4 (current)** | **2** | **1** | **1** | **yes** |
| 5 | 2 | 2 | 1 | yes |
| 6 | 3 | 2 | 1 | yes |

The four-day corpus is **retained**. Day 5 would produce an identical 2-day TRAIN
partition and change nothing; day 6 would clear the day gate only to hit the FAR gate.
Neither is collected.

One limitation to carry into every report: 2026-09-05 is a partial day, so TRAIN is
effectively "one full day plus a partial day." This is an argument for eventually
running §10.5's enrollment-length experiment. It is not a reason to delay the drill.
