# Configuration authority

All runtime tunables belong here and must validate against C9 in
`protocol/schemas/config.schema.json`. Source code may not contain fallback security
thresholds or hidden defaults.

The following values remain deliberately unresolved and therefore no production
`thresholds.yaml` is supplied by T-002:

- O3 enrollment duration and minimum enrollment evidence;
- O4 modality quality gates;
- O7 fusion weights;
- O8 risk boundaries;
- O9 smoothing parameters;
- O10 update cadence and quarantine;
- O11 context retention/confidence values.

Their named experiments and human review must produce a versioned configuration. A
service must fail startup loudly when a required security-relevant value is absent; it
must not invent a source-code default.

`storage.development.yaml` is a T-009 local profile for disposable synthetic data only.
Its retention values are engineering limits for local testing, not approved research or
participant-data policy. Pilot/evaluation collection requires a separately reviewed C9
configuration.

`synthetic.development.yaml` is the validated T-003 fixture definition. Its behavioral
profiles are invented engineering inputs, never participant measurements. Generated
events, windows, and scores are development mechanics only and are explicitly ineligible
for headline evaluation.

`ingestion.development.yaml` supplies the bounded frame size, memory-only raw-event ring,
and configurable ADR-007 idle split used by T-007 local development. It contains no
production risk or enforcement thresholds.

`risk.development.yaml` exists only to exercise T-013 with synthetic fixtures. Its O3,
O7, O8, and O9 values are explicitly provisional; it is not a production `thresholds.yaml`
and does not resolve the Plan's open experiment/human-approval items.

`context.development.yaml` similarly provides provisional T-012 bootstrap/floor values
for synthetic testing. Real category observations and human review are still required
before an efficacy claim or production policy can be made.

`collector.development.yaml`, `api.development.yaml`, and
`orchestration.development.yaml` define bounded Windows IPC, loopback authentication,
stream replay, watchdog, and measurement capacities. `updates.development.yaml` holds
the provisional G3/G5/G6/regression policy. `collection.development.yaml` and
`evaluation.development.yaml` are operation/statistics placeholders, while
`benchmark.development.yaml` controls evidence sampling. None resolves an `[OPEN]` ADR
or authorizes participant collection without the required human protocol and review.
