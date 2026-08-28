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
benefit) and E2 (labelled poisoning resistance). Results are not evidence until run on
the frozen eligible corpus with the evaluation/config freeze verified.
