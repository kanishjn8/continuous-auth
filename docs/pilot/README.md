# Pilot operations boundary

Participant-facing operations remain human-only. Before collection, the approved team
must review the plain-language brief, obtain written consent, assign an 8–64 character
pseudonym, record enrollment versions beneath ignored `data/collection/`, demonstrate
the pause control, and brief the participant to report device changes. Do not place
names, contact details, or signed forms in this repository.

Start from the [participant brief template](participant-brief-template.md) and
[operator checklist](operator-checklist.md); both require human approval before use.
The post-activation attacker drill has its own procedure:
[attack drill protocol](attack-drill-protocol.md). It runs **after** the corpus is
frozen and the profile is `ACTIVE`, never before.

The collector records content-free key classes, mouse movement/button/scroll geometry,
capture-time monotonic timestamps, foreground process name/category, input-device class,
resolution/DPI, and health counters. It does not record key identity, typed content,
titles, document names, paths, network addresses, clipboard, images, or screen data.

The collection CLI provides aggregate health and freeze operations:

```powershell
python -m tools.collection --config config/collection.pilot.yaml health `
  --database $env:CA_STORAGE_DB --administration data/collection/administration.json
python -m tools.collection --config config/collection.pilot.yaml freeze `
  --database $env:CA_STORAGE_DB --administration data/collection/administration.json `
  --version pilot-v1 --output data/frozen/pilot-v1/manifest.json
```

Missing/inactive consent, missing enrollment, or non-participant provenance blocks
evaluation eligibility. A freeze is write-once, day-disjoint, and checksum-verified;
late windows are not added to it. Recruitment, consent, installation support, A3 prompt
completion, informed mimicry, and live takeover are not automatable.

Runtime tunables in the api, risk, context, orchestration, updates, and enforcement
configs remain unreviewed development placeholders (each loader requires
`development_only: true`) regardless of collection round. The data those runtime
components process is nonetheless real `PILOT` data once collection is underway: the
storage profile (`config/storage.pilot.yaml`) and this collection profile
(`config/collection.pilot.yaml`) are the reviewed configuration that governs it, and
provenance is derived from the storage profile alone.
