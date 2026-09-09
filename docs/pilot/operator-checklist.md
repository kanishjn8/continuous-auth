# Pilot operator checklist

Use this only after the participant brief and consent materials have human approval.
Evidence containing identities or signatures stays outside Git; collection
administration under `data/` uses only the assigned pseudonym.

## Before collection

- Confirm the approved brief/consent version and voluntary written consent.
- Assign an 8–64 character pseudonym with no name, email address, or employee number.
- Record collector, configuration, and consent versions in the ignored administration
  record.
- Confirm Windows target, hook permissions, local storage permissions, and available
  disk space.
- Explain recorded and prohibited fields, expected duration, A3 prompts, withdrawal,
  support, and device-change reporting.
- Demonstrate pause/resume and verify the heartbeat reports the paused state.
- Run the aggregate health check; do not inspect or create raw input logs.

## During collection

- Review only aggregate coverage, modality balance, gaps, quality labels, heartbeat,
  and device-change alerts.
- Confirm scheduled verification prompts are being answered; the system records A3
  anchors automatically and operators do not create them by hand.
- Pause for participant-requested breaks and investigate loud availability/tamper alerts.
- Never substitute continuous low risk for consent or a verification anchor.

## Before freezing or evaluation

- Confirm consent remains active and provenance is `PILOT`.
- Resolve or document coverage shortfalls and device changes.
- Verify every participant has enough distinct days for three non-empty day-disjoint
  partitions (`freeze.min_distinct_days`), **and** that the resulting TRAIN partition
  covers at least `ml.enrollment.min_train_distinct_days` distinct days. Under the
  60/20/20 split those are different requirements: 3 collection days satisfy the first
  and fail the second; 4 satisfy both.
- Confirm **no attacker drill has been run yet**. The corpus is frozen before any drill,
  never after.
- Create the write-once manifest, verify its checksums, and record its version and
  timestamp — the timestamp is the auditable evidence that the corpus predates the drill.
- Freeze configurations and code revision before final evaluation; never append late
  windows or tune against evaluation results.

## Attacker drill (after activation only)

- Confirm the profile is `ACTIVE`, the shadow period has been reviewed, and the corpus
  is already frozen.
- Follow [attack-drill-protocol.md](attack-drill-protocol.md). Start the backend with
  `--drill-label <label>`; never use that flag for genuine collection.
- Brief the attacker on what is recorded, that the machine may lock, and that they may
  stop at any time. They are not enrolled and contribute no training data.
- Expect `validation_far: null` on the activated profile. That means *not measured*, and
  it is correct. `0.0` would be fabricated — if you see it, stop and investigate.
- Do not create a new freeze after a drill. If a later round requires one, use a new
  version name and verify no drill window appears in the manifest.
