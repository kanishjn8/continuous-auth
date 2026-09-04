# Model Update Manager

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
eligible corpus with the evaluation/config freeze verified, and both remain pending.

The six gates above govern *updates* to a profile that already exists. A user's first
profile is a different transition and is admitted by the ADR-013 enrollment gate
(`ml/training/enrollment.py::require_enrollment_admission`), not by G1-G6: it requires
eligible provenance, active consent, a recorded enrollment, membership of a
checksum-verified freeze manifest, a recorded observation time for every window, the
configured minimum window and distinct-day counts, and the absence of any existing
active profile for that user. `ml/training/common.py` routes every call through exactly
one of these two boundaries — supplying neither the promotion candidates nor an
enrollment admission raises, and supplying both raises, so there is no path that skips
review. Once a profile is active, this gate refuses further use and the unchanged G1-G6
promotion gate is the only remaining route. `python -m tools.enrollment activate` runs
this admission and activation for a participant's first profile from the frozen corpus.

`quarantine_days: 7` and `retraining_cadence_days: 7` in `config/updates.development.yaml`
are unchanged, so a five-day collection round produces no live promotion — by design.
Scheduled update runs are launched with `python -m tools.updates run`; the first one
should not be scheduled earlier than day 12 of a collection round, so that every
collected day has cleared the 7-day quarantine before that run executes.

`config/updates.development.yaml`, like the other five runtime configuration loaders
(`api`, `decisions`, `risk`, `risk` context, `runtime`, `updates`), still requires
`development_only: true` — that configuration is not yet approved for the pilot. Only
the storage and collection profiles have been reviewed for pilot use.
