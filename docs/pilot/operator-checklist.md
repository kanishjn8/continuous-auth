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
  partitions.
- Create the write-once manifest, verify its checksums, and record its version.
- Freeze configurations and code revision before final evaluation; never append late
  windows or tune against evaluation results.
