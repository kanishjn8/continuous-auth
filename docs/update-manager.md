# Model Update Manager

> **Status for this pilot: the capability is implemented and tested; updates are
> DISABLED and nothing is promoted (ADR-014).**
>
> Everything described below exists in the codebase and is covered by tests. It is not
> enabled for the delivered single-user build, and this document describes a capability
> rather than an operational behaviour.
>
> **Why.** An update may only replace an active profile when it is shown not to have
> raised FAR. FAR needs impostor evidence. This project's only impostor evidence is the
> post-activation attacker drill, which is excluded from every corpus by design
> (`docs/architecture.md`, "Attacker-drill data-flow boundary"), so a candidate's FAR is
> unmeasurable *by construction*.
>
> **What that looks like in code.** `ValidationMetrics.false_acceptance_rate` is
> `float | None`, where `None` means **not measured** — never zero.
> `UpdateManager._validate` returns `accepted=False` with code
> `VALIDATION_FAR_UNMEASURED` whenever either side's FAR is `None`.
> `python -m tools.updates run` refuses earlier still, before any training runs or any
> artifact is written, with `UPDATE_REQUIRES_IMPOSTOR_COHORT`, so a refused run leaves no
> orphan model files.
>
> **Nothing was weakened to arrive here.** `ml/training/gate.py`, G1–G6,
> `quarantine_days: 7`, `retraining_cadence_days: 7`, `regression_tolerance: 0.02`,
> `min_promotable_windows: 10`, and `retained_model_versions` are unchanged. Refusal is
> the correct answer, not a limitation being worked around.
>
> **Note the deliberate asymmetry with enrollment.** A *first profile* may activate with
> FAR unmeasured, because `accepted` there gates activation against the ADR-013
> enrollment gate and there is no prior profile to regress against. An *update* may not,
> because `accepted` there means "not worse than the active profile", which without the
> measurement is undecidable.

Candidates are admitted at segment granularity only. G1 requires no enforcement and
only LOW/MEDIUM risk; G2 requires an independent A1, A2, or A3 verification record; G3
requires configured volume; G4 rejects unexplained gaps; G5 requires quarantine age;
and G6 requires the fixed scheduled run. Low risk alone is not an anchor. Incidents
invalidate candidates.

Scheduled retraining receives only promoted candidates at the ML training boundary.
The candidate must pass held-out genuine and impostor regression checks before its
profile is activated in one SQLite transaction. Prior versions are retained to the
configured bound, and rollback atomically selects the immediately previous version.
Load-time feature-schema and checksum validation remain mandatory. A failed build,
validation, activation, or load leaves enforcement fail-open and emits an availability
signal.

`ml.experiments.update_manager` implements E1 (paired frozen-versus-updated drift
benefit) and E2 (labelled poisoning resistance), runnable via `python -m
tools.experiments e1` and `python -m tools.experiments e2`. Both have so far been
exercised only against synthetic fixtures; neither is evidence until run on the frozen
eligible corpus with the evaluation/config freeze verified. **For this build both are
unrun and will stay unrun**, because both require the update pipeline that ADR-014
disables. They are reported as not run, not as forthcoming.

The six gates above govern *updates* to a profile that already exists. A user's first
profile is a different transition and is admitted by the ADR-013 enrollment gate
(`ml/training/enrollment.py::require_enrollment_admission`), not by G1-G6: it requires
eligible provenance, active consent, a recorded enrollment, membership of a
checksum-verified freeze manifest, a recorded observation time for every window, the
configured minimum TRAIN window and distinct-day counts, and the absence of any existing
active profile for that user. `ml/training/common.py` routes every call through exactly
one of these two boundaries — supplying neither the promotion candidates nor an
enrollment admission raises, and supplying both raises, so there is no path that skips
review. Once a profile is active, this gate refuses further use and the unchanged G1-G6
promotion gate is the only remaining route. `python -m tools.enrollment activate` runs
this admission and activation for a participant's first profile from the frozen corpus.

The TRAIN window and distinct-day counts that the enrollment gate enforces come from
`config/ml.*.yaml -> enrollment.min_train_windows / min_train_distinct_days`, and are
scoped to the frozen TRAIN partition. They are **not** the live state-machine thresholds
in `config/risk.*.yaml -> enrollment.min_windows / min_distinct_days`, which are counted
over the user's whole observed history and govern `ENROLLING -> CALIBRATING`. ADR-014
separated the two after a single key was found to be answering both questions, which
silently turned a "3 distinct days" rule into an undocumented "6 collection days"
requirement under a 60/20/20 day-disjoint split.

`quarantine_days: 7` and `retraining_cadence_days: 7` in `config/updates.development.yaml`
are unchanged, so a five-day collection round produces no live promotion — by design.
Scheduled update runs are launched with `python -m tools.updates run`; the first one
should not be scheduled earlier than day 12 of a collection round, so that every
collected day has cleared the 7-day quarantine before that run executes. For this pilot
that run refuses immediately with `UPDATE_REQUIRES_IMPOSTOR_COHORT` regardless of the
day, as described at the top of this document.

`config/updates.development.yaml`, like the other five runtime configuration loaders
(`api`, `decisions`, `risk`, `risk` context, `runtime`, `updates`), still requires
`development_only: true` — that configuration is not yet approved for the pilot. Only
the storage and collection profiles have been reviewed for pilot use.
