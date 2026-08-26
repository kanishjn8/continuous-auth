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


