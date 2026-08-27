# Pilot operations boundary

Participant-facing operations remain human-only. Before collection, the approved team
must review the plain-language brief, obtain written consent, assign an 8–64 character
pseudonym, record enrollment versions beneath ignored `data/collection/`, demonstrate
the pause control, and brief the participant to report device changes. Do not place
names, contact details, or signed forms in this repository.

The collector records content-free key classes, mouse movement/button/scroll geometry,
capture-time monotonic timestamps, foreground process name/category, input-device class,
resolution/DPI, and health counters. It does not record key identity, typed content,
titles, document names, paths, network addresses, clipboard, images, or screen data.

The collection CLI provides aggregate health and freeze operations:

```powershell
python -m tools.collection --config config/collection.development.yaml health `
  --database $env:CA_STORAGE_DB --administration data/collection/administration.json
python -m tools.collection --config config/collection.development.yaml freeze `
  --database $env:CA_STORAGE_DB --administration data/collection/administration.json `
  --version pilot-v1 --output data/frozen/pilot-v1/manifest.json
```

The development collection settings are unreviewed placeholders. Missing/inactive
consent, missing enrollment, or non-participant provenance blocks evaluation eligibility.
A freeze is write-once, day-disjoint, and checksum-verified; late windows are not added
to it. Recruitment, consent, installation support, A3 prompt completion, informed
mimicry, and live takeover are not automatable.
